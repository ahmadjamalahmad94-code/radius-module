"""NPC Phase 4 — JSON API endpoints for all 3 sub-services."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_4_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "test-token")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        yield c
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


_HEADERS = {"Authorization": "Bearer test-token"}


def _json(resp):
    return json.loads(resp.data.decode("utf-8"))


# ─── Bearer auth + routing sanity ────────────────────────────


def test_unauth_request_returns_401(client):
    r = client.get("/api/v1/network-policy/web-block/policies")
    assert r.status_code == 401


def test_three_subservice_list_endpoints_exist(client):
    for sub in ("remote-access", "web-block", "walled-garden"):
        r = client.get(
            f"/api/v1/network-policy/{sub}/policies",
            headers=_HEADERS,
        )
        assert r.status_code == 200, sub
        body = _json(r)
        assert body["ok"] is True
        assert body["data"]["items"] == []
        assert body["data"]["count"] == 0


def test_policy_list_rejects_bad_router_id_in_arabic(client):
    for sub in ("remote-access", "web-block", "walled-garden"):
        r = client.get(
            f"/api/v1/network-policy/{sub}/policies?router_id=bad",
            headers=_HEADERS,
        )
        assert r.status_code == 422, sub
        assert _json(r)["error"]["message"] == "معرّف الراوتر يجب أن يكون رقمًا صحيحًا."


# ─── Remote access CRUD ──────────────────────────────────────


def _ra_create_body(**overrides) -> dict:
    base = {
        "name": "Emergency",
        "router_id": 9,
        "allow_winbox": True,
        "allow_webfig_https": True,
        "source_address_list": "ops-bastion",
        "expires_at": "2027-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_remote_access_create_get_patch_delete(client):
    # Create
    r = client.post(
        "/api/v1/network-policy/remote-access/policies",
        json=_ra_create_body(), headers=_HEADERS,
    )
    assert r.status_code == 201
    pid = _json(r)["data"]["id"]

    # Get
    r = client.get(
        f"/api/v1/network-policy/remote-access/policies/{pid}",
        headers=_HEADERS,
    )
    assert r.status_code == 200
    row = _json(r)["data"]
    assert row["name"] == "Emergency"
    assert row["allow_winbox"] == 1

    # Patch — toggle winbox off, change reason.
    r = client.patch(
        f"/api/v1/network-policy/remote-access/policies/{pid}",
        json={"allow_winbox": False,
              "reason": "investigating reboot loop"},
        headers=_HEADERS,
    )
    assert r.status_code == 200
    row = _json(r)["data"]
    assert row["allow_winbox"] == 0
    assert row["reason"] == "investigating reboot loop"

    # Delete
    r = client.delete(
        f"/api/v1/network-policy/remote-access/policies/{pid}",
        headers=_HEADERS,
    )
    assert r.status_code == 200
    assert _json(r)["data"]["deleted"] == pid

    # Subsequent get → 404.
    r = client.get(
        f"/api/v1/network-policy/remote-access/policies/{pid}",
        headers=_HEADERS,
    )
    assert r.status_code == 404


def test_remote_access_create_rejects_missing_required_fields(client):
    r = client.post(
        "/api/v1/network-policy/remote-access/policies",
        json={}, headers=_HEADERS,
    )
    assert r.status_code == 422
    body = _json(r)
    assert body["ok"] is False


def test_remote_access_get_unknown_returns_404(client):
    r = client.get(
        "/api/v1/network-policy/remote-access/policies/9999",
        headers=_HEADERS,
    )
    assert r.status_code == 404


# ─── Web-block CRUD + targets ────────────────────────────────


def test_web_block_full_flow(client):
    # Create policy
    r = client.post(
        "/api/v1/network-policy/web-block/policies",
        json={"name": "TikTok block", "router_id": 1,
              "fail_open": True},
        headers=_HEADERS,
    )
    assert r.status_code == 201
    pid = _json(r)["data"]["id"]

    # Add two targets — second one is a dup, gets updated.
    for value in ("tiktok.com", "TIKTOK.COM"):
        r = client.post(
            f"/api/v1/network-policy/web-block/policies/{pid}"
            "/targets",
            json={"value": value,
                  "normalized_value": value.lower(),
                  "target_type": "domain",
                  "category": "tiktok"},
            headers=_HEADERS,
        )
        assert r.status_code == 201

    # List targets — dedup leaves one row.
    r = client.get(
        f"/api/v1/network-policy/web-block/policies/{pid}"
        "/targets",
        headers=_HEADERS,
    )
    assert r.status_code == 200
    body = _json(r)["data"]
    assert body["count"] == 1
    assert body["counts"]["tiktok"] == 1
    target_id = body["items"][0]["id"]

    # Delete one target
    r = client.delete(
        f"/api/v1/network-policy/web-block/policies/{pid}"
        f"/targets/{target_id}",
        headers=_HEADERS,
    )
    assert r.status_code == 200

    # Delete the policy
    r = client.delete(
        f"/api/v1/network-policy/web-block/policies/{pid}",
        headers=_HEADERS,
    )
    assert r.status_code == 200


def test_web_block_target_rejects_unknown_type(client):
    r = client.post(
        "/api/v1/network-policy/web-block/policies",
        json={"name": "x", "router_id": 1}, headers=_HEADERS,
    )
    pid = _json(r)["data"]["id"]
    r = client.post(
        f"/api/v1/network-policy/web-block/policies/{pid}"
        "/targets",
        json={"value": "x", "target_type": "wildcard"},
        headers=_HEADERS,
    )
    assert r.status_code == 422


# ─── Walled garden ───────────────────────────────────────────


def test_walled_garden_full_flow(client):
    r = client.post(
        "/api/v1/network-policy/walled-garden/policies",
        json={"name": "Payments allowlist", "router_id": 1,
              "hotspot_profile": "hsprof1"},
        headers=_HEADERS,
    )
    assert r.status_code == 201
    pid = _json(r)["data"]["id"]

    # Add two entries — same host + IP allow.
    r = client.post(
        f"/api/v1/network-policy/walled-garden/policies/{pid}"
        "/entries",
        json={"value": "api.payments.test",
              "entry_type": "dst_host"},
        headers=_HEADERS,
    )
    assert r.status_code == 201
    r = client.post(
        f"/api/v1/network-policy/walled-garden/policies/{pid}"
        "/entries",
        json={"value": "8.8.8.8",
              "entry_type": "dst_address",
              "dst_port": "443", "protocol": "tcp"},
        headers=_HEADERS,
    )
    assert r.status_code == 201

    r = client.get(
        f"/api/v1/network-policy/walled-garden/policies/{pid}"
        "/entries",
        headers=_HEADERS,
    )
    body = _json(r)["data"]
    assert body["count"] == 2
    assert body["counts"]["dst_host"] == 1
    assert body["counts"]["dst_address"] == 1


# ─── Preview ─────────────────────────────────────────────────


def test_remote_access_preview_returns_forward_and_rollback(client):
    r = client.post(
        "/api/v1/network-policy/remote-access/policies",
        json=_ra_create_body(), headers=_HEADERS,
    )
    pid = _json(r)["data"]["id"]

    r = client.post(
        f"/api/v1/network-policy/remote-access/policies/{pid}"
        "/preview",
        headers=_HEADERS,
    )
    assert r.status_code == 200
    body = _json(r)["data"]
    assert body["can_apply"] is True
    assert body["service"] == "remote_access"
    assert "HOBE_NPC_REMOTE:" in body["forward_script"]
    assert "HOBE_NPC_REMOTE:" in body["rollback_script"]
    assert len(body["script_hash"]) == 64
    assert isinstance(body["script_version_id"], int)
    assert body["summary"]["command_count"] >= 2


def test_web_block_preview_with_targets(client):
    r = client.post(
        "/api/v1/network-policy/web-block/policies",
        json={"name": "TikTok", "router_id": 1,
              "fail_open": True},
        headers=_HEADERS,
    )
    pid = _json(r)["data"]["id"]
    client.post(
        f"/api/v1/network-policy/web-block/policies/{pid}"
        "/targets",
        json={"value": "tiktok.com",
              "normalized_value": "tiktok.com",
              "target_type": "domain",
              "category": "tiktok"},
        headers=_HEADERS,
    )
    r = client.post(
        f"/api/v1/network-policy/web-block/policies/{pid}"
        "/preview",
        headers=_HEADERS,
    )
    body = _json(r)["data"]
    assert body["can_apply"] is True
    assert "address=tiktok.com" in body["forward_script"]
    assert "dst-address-list=HOBE_NPC_BLOCK_" in \
        body["forward_script"]


def test_blocked_preview_returns_can_apply_false(client):
    """Remote-access policy with no service toggles is
    invalid — preview must surface the blocker without 500."""
    r = client.post(
        "/api/v1/network-policy/remote-access/policies",
        json={
            "name": "Broken", "router_id": 1,
            "allow_winbox": False,
            "allow_webfig_https": False,
            # No source allowlist, no expiry → blocker.
        },
        headers=_HEADERS,
    )
    pid = _json(r)["data"]["id"]
    r = client.post(
        f"/api/v1/network-policy/remote-access/policies/{pid}"
        "/preview",
        headers=_HEADERS,
    )
    assert r.status_code == 200
    body = _json(r)["data"]
    assert body["can_apply"] is False
    assert body["summary"]["blocking_errors"]
    # No script_version_id recorded for an unapplyable plan.
    assert "script_version_id" not in body


def test_runtime_api_script_duplicate_changes_apply_and_rollback(client, monkeypatch):
    r = client.post(
        "/api/v1/network-policy/remote-access/policies",
        json=_ra_create_body(name="Ops access"),
        headers=_HEADERS,
    )
    assert r.status_code == 201
    pid = _json(r)["data"]["id"]

    preview = client.post(
        f"/api/v1/network-policy/remote-access/policies/{pid}/preview",
        headers=_HEADERS,
    )
    assert preview.status_code == 200

    script = client.get(
        f"/api/v1/network-policy/remote-access/policies/{pid}/preview.rsc",
        headers=_HEADERS,
    )
    assert script.status_code == 200
    script_body = _json(script)["data"]
    assert script_body["filename"].endswith("-preview.rsc")
    assert "HOBE_NPC_REMOTE:" in script_body["script"]

    duplicated = client.post(
        f"/api/v1/network-policy/remote-access/policies/{pid}/duplicate",
        headers=_HEADERS,
    )
    assert duplicated.status_code == 201
    assert _json(duplicated)["data"]["name"].endswith("(نسخة)")
    duplicated_again = client.post(
        f"/api/v1/network-policy/remote-access/policies/{pid}/duplicate",
        headers=_HEADERS,
    )
    assert duplicated_again.status_code == 201
    assert _json(duplicated_again)["data"]["slug"].endswith("-copy-2")

    with client.application.app_context():
        from app.radius.db.repos import npc_change_sets_repo as cs
        change_set_id = cs.create(
            tenant_id=1,
            service="remote_access",
            policy_id=pid,
            action_type=cs.ACTION_APPLY,
            preview_hash="abc",
            requested_router_ids=(9,),
            created_by="test",
        )
        cs.update_status(
            1, change_set_id,
            status=cs.STATUS_SUCCEEDED,
            finished_at_now=True,
        )
        cs.add_target(
            change_set_id=change_set_id,
            tenant_id=1,
            router_id=9,
            rendered_script="/ip firewall filter add comment=HOBE_NPC_REMOTE:1",
            rollback_script="/ip firewall filter remove [find comment~\"^HOBE_NPC_\"]",
            status=cs.TARGET_STATUS_SUCCEEDED,
        )

    changes = client.get(
        f"/api/v1/network-policy/remote-access/policies/{pid}/changes",
        headers=_HEADERS,
    )
    assert changes.status_code == 200
    changes_body = _json(changes)["data"]
    assert changes_body["count"] == 1
    assert changes_body["items"][0]["rollback_eligible"] is True
    assert changes_body["items"][0]["targets"]

    import app.api.v1.network_policy as npc_api

    captured = {}

    class _ApplyResult:
        ok = True

        def as_dict(self):
            return {
                "ok": True,
                "change_set_id": 44,
                "status": "succeeded",
                "targets": [],
                "blockers": [],
                "warnings": [],
                "reason_ar": "تم التنفيذ بنجاح.",
            }

    def _fake_apply(**kwargs):
        captured["apply"] = kwargs
        return _ApplyResult()

    monkeypatch.setattr(
        npc_api.snapshot_capture_svc,
        "capture_pre_apply_snapshot",
        lambda **_: SimpleNamespace(snapshot_id=33),
    )
    monkeypatch.setattr(npc_api.apply_svc, "request_apply", _fake_apply)
    apply_res = client.post(
        f"/api/v1/network-policy/remote-access/policies/{pid}/apply",
        json={"execution_mode": "full", "confirmations": ["backup_ready"]},
        headers=_HEADERS,
    )
    assert apply_res.status_code == 200
    assert _json(apply_res)["data"]["change_set_id"] == 44
    assert captured["apply"]["snapshot_id"] == 33
    assert captured["apply"]["confirmations"] == ("backup_ready",)

    class _RollbackResult:
        def as_dict(self):
            return {
                "ok": True,
                "change_set_id": 45,
                "status": "rolled_back",
                "targets": [],
                "reason_ar": "تم التراجع بنجاح.",
            }

    monkeypatch.setattr(
        npc_api.rollback_svc,
        "request_rollback",
        lambda **_: _RollbackResult(),
    )
    rollback = client.post(
        f"/api/v1/network-policy/remote-access/policies/{pid}"
        f"/changes/{change_set_id}/rollback",
        headers=_HEADERS,
    )
    assert rollback.status_code == 200
    assert _json(rollback)["data"]["original_change_set_id"] == change_set_id


# ─── Audit trail ─────────────────────────────────────────────


def test_state_changing_calls_emit_audit_rows(client):
    """Create + delete a web_block policy and confirm the audit
    log contains the expected action strings."""
    r = client.post(
        "/api/v1/network-policy/web-block/policies",
        json={"name": "audit-probe", "router_id": 1},
        headers=_HEADERS,
    )
    pid = _json(r)["data"]["id"]
    client.post(
        f"/api/v1/network-policy/web-block/policies/{pid}"
        "/targets",
        json={"value": "x.com", "target_type": "domain"},
        headers=_HEADERS,
    )
    client.delete(
        f"/api/v1/network-policy/web-block/policies/{pid}",
        headers=_HEADERS,
    )

    # Read the audit log directly to keep this test
    # independent of the audit JSON endpoint's shape.
    from app.radius.db.connection import db
    rows = db().execute(
        "SELECT action, target_id FROM audit_log "
        "WHERE target_type='npc_web_block_policy' "
        "ORDER BY id"
    ).fetchall()
    actions = [r["action"] for r in rows]
    assert "npc.web_block.policy_created" in actions
    assert "npc.web_block.target_added" in actions
    assert "npc.web_block.policy_deleted" in actions


# ─── Tenant isolation ────────────────────────────────────────


def test_tenant_isolation_via_token(client):
    """Default token has tenant_id=1. A policy created here is
    not visible to a synthetic tenant=2 request — direct DB
    test since we only have one valid token."""
    r = client.post(
        "/api/v1/network-policy/web-block/policies",
        json={"name": "tenant1-only", "router_id": 1},
        headers=_HEADERS,
    )
    pid = _json(r)["data"]["id"]

    from app.radius.db.repos import npc_web_block_repo as wb
    # Tenant 2 cannot read tenant 1's policy.
    assert wb.get_policy(2, pid) is None
    # Tenant 1 can.
    assert wb.get_policy(1, pid) is not None
