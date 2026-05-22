"""VX2.6 — Guarded apply route tests.

These tests NEVER touch a live router. They monkeypatch
`site_exit._connect_client` to return a fake client whose
`.run()` records every call. That lets us assert:
  - the apply route refuses to execute when safety blocks
  - it ONLY executes `add` commands (no broad-prefix removes)
  - every executed command's comment starts with the managed
    prefix
  - audit events land for both success and failure paths
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_vx2_6_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                        os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"v26_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="v26-pass", full_name="V26",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "v26-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _post(client, url, data, **kwargs):
    data = dict(data or {})
    data.setdefault("_csrf_token", _csrf(client))
    return client.post(url, data=data, **kwargs)


# ─── Seeding ────────────────────────────────────────────────


def _seed_full(app, *, nas_id=1):
    """Seed: router + recent backup + node + policy + target.
    Returns the policy id."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import (
            router_backups_repo, vps_exit_nodes_repo,
            site_exit_policies_repo, site_exit_targets_repo,
        )
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address,
                     api_user, api_password, api_port,
                     secret, vendor, nas_type, enabled,
                     created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'hr', 'pw', 8728,
                           'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct')""",
                (nas_id, f"v26-r{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )
        router_backups_repo.record(
            tenant_id=1, router_id=nas_id,
            backup_type="binary", filename="fresh.backup",
            status="success",
        )
        nid = vps_exit_nodes_repo.create(
            tenant_id=1, name=f"vps-{nas_id}",
            public_ip="203.0.113.99",
            wireguard_interface_name="wg-vps",
            wireguard_gateway_ip="10.10.0.1",
            enabled=True,
        )
        pid = site_exit_policies_repo.create(
            tenant_id=1, router_id=nas_id,
            exit_node_id=nid,
            name=f"policy-{nas_id}",
            fail_mode="block_when_vps_down",
        )
        site_exit_targets_repo.add(
            policy_id=pid, value="speedtest.net",
            normalized_value="speedtest.net",
            target_type="domain",
            group_name="speedtest_measurement",
            include_www=True,
        )
        return pid


def _all_confirmations() -> dict:
    return {
        "confirm_preview_seen":         "1",
        "confirm_backup_status":        "1",
        "confirm_vps_exit_understood":  "1",
        "confirm_fail_mode_understood": "1",
        "confirm_selected_sites_only":  "1",
        "wan_interface_list":           "WAN",
    }


class _FakeClient:
    """Records every `.run(path, attrs=...)` call. Never
    actually talks to a router."""
    def __init__(self, *, fail_on: str = ""):
        self.calls: list[tuple[str, dict]] = []
        self.connected = False
        self.fail_on = fail_on

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def run(self, path: str, *, attrs: dict | None = None):
        self.calls.append((path, dict(attrs or {})))
        if self.fail_on and self.fail_on in path:
            raise RuntimeError(
                f"fake router refused {path}")


def _patch_client(monkeypatch, fake: _FakeClient) -> None:
    from app.radius.routes import site_exit as r
    monkeypatch.setattr(
        r, "_connect_client", lambda nas: fake,
    )


# ─── Auth ───────────────────────────────────────────────────


def test_apply_route_login_guarded(client):
    res = client.post(
        "/admin/radius/mt/1/site-exit/policies/1/apply",
        follow_redirects=False,
    )
    assert res.status_code in {302, 303, 400}
    # 302 to login OR 400 from CSRF guard — never 500.


def test_apply_route_404_for_unknown_router(app, client):
    _login(client)
    res = _post(
        client,
        "/admin/radius/mt/9999/site-exit/policies/1/apply",
        _all_confirmations(),
    )
    assert res.status_code == 404


# ─── Safety gating ──────────────────────────────────────────


def test_apply_blocked_when_safety_blocks_does_not_call_wire(
    app, client, monkeypatch,
):
    """No confirmations → safety blocks → wire client must
    never be invoked."""
    pid = _seed_full(app, nas_id=1)
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    _login(client)
    res = _post(
        client,
        f"/admin/radius/mt/1/site-exit/policies/{pid}/apply",
        {"wan_interface_list": "WAN"},   # missing confirms
    )
    assert res.status_code == 200
    # Critical: the wire client must NEVER have been touched.
    assert fake.calls == []
    assert fake.connected is False


def test_apply_blocked_records_blocked_audit_event(
    app, client, monkeypatch,
):
    pid = _seed_full(app, nas_id=2)
    _patch_client(monkeypatch, _FakeClient())
    _login(client)
    _post(
        client,
        f"/admin/radius/mt/2/site-exit/policies/{pid}/apply",
        {"wan_interface_list": "WAN"},  # missing confirms
    )
    # The audit row exists with result_status=blocked.
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(1, action="site_exit.apply_attempted",
                                  limit=10)
    assert any(r["result_status"] == "blocked" for r in rows)


# ─── Happy path ─────────────────────────────────────────────


def test_apply_with_full_confirmations_runs_managed_adds(
    app, client, monkeypatch,
):
    pid = _seed_full(app, nas_id=3)
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    _login(client)
    res = _post(
        client,
        f"/admin/radius/mt/3/site-exit/policies/{pid}/apply",
        _all_confirmations(),
    )
    assert res.status_code == 200
    # The fake received calls — they're all `add` paths.
    assert fake.calls, "expected wire calls"
    for path, _attrs in fake.calls:
        assert path.endswith("/add"), \
            f"non-add path leaked through: {path}"
    # Critical safety contract: every executed cmd carries the
    # managed comment prefix.
    for path, attrs in fake.calls:
        comment = attrs.get("comment", "")
        assert comment.startswith("HOBE_VX2_SITE_EXIT:"), (
            f"executed {path} has unmanaged comment: {comment!r}"
        )


def test_apply_only_calls_add_never_calls_remove(
    app, client, monkeypatch,
):
    """Defence-in-depth — the wire path must NEVER receive a
    `/remove` operation. Removes use script `[find]` syntax
    which is not safe via the API."""
    pid = _seed_full(app, nas_id=4)
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    _login(client)
    _post(
        client,
        f"/admin/radius/mt/4/site-exit/policies/{pid}/apply",
        _all_confirmations(),
    )
    for path, _ in fake.calls:
        assert "/remove" not in path, (
            f"forbidden remove path executed: {path}")


def test_apply_success_records_audit_and_deployment(
    app, client, monkeypatch,
):
    pid = _seed_full(app, nas_id=5)
    _patch_client(monkeypatch, _FakeClient())
    _login(client)
    _post(
        client,
        f"/admin/radius/mt/5/site-exit/policies/{pid}/apply",
        _all_confirmations(),
    )
    with app.app_context():
        from app.radius.db.repos import (
            audit_repo, site_exit_deployments_repo as d,
        )
        actions = {r["action"] for r in
                   audit_repo.recent(1, limit=20)}
        deploy = d.get_for_policy(1, pid)
    # success path emits both attempted + succeeded.
    assert "site_exit.apply_attempted" in actions
    assert "site_exit.apply_succeeded" in actions
    assert deploy["status"] == "applied"
    assert deploy["generated_script_hash"]
    assert deploy["last_error"] == ""


def test_apply_failure_records_audit_and_deployment(
    app, client, monkeypatch,
):
    pid = _seed_full(app, nas_id=6)
    # Fake fails on the very first add — mangle/route paths
    # are unique strings, but any-add would do; use a marker
    # that exists in every plan.
    _patch_client(monkeypatch,
                   _FakeClient(fail_on="/ip/firewall/address-list"))
    _login(client)
    _post(
        client,
        f"/admin/radius/mt/6/site-exit/policies/{pid}/apply",
        _all_confirmations(),
    )
    with app.app_context():
        from app.radius.db.repos import (
            audit_repo, site_exit_deployments_repo as d,
        )
        actions = {r["action"] for r in
                   audit_repo.recent(1, limit=20)}
        deploy = d.get_for_policy(1, pid)
    assert "site_exit.apply_failed" in actions
    assert deploy["status"] == "failed"
    assert "refused" in deploy["last_error"].lower() \
        or deploy["last_error"]


def test_apply_persists_script_version(
    app, client, monkeypatch,
):
    pid = _seed_full(app, nas_id=7)
    _patch_client(monkeypatch, _FakeClient())
    _login(client)
    _post(
        client,
        f"/admin/radius/mt/7/site-exit/policies/{pid}/apply",
        _all_confirmations(),
    )
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_scripts_repo as s,
        )
        latest = s.latest_for_policy(pid)
    assert latest is not None
    assert latest["script_hash"]
    assert latest["command_count"] >= 1


def test_apply_audit_payload_has_no_secrets(
    app, client, monkeypatch,
):
    """Every audit payload (attempted/succeeded/failed) must be
    free of WireGuard private-key tripwires."""
    pid = _seed_full(app, nas_id=8)
    _patch_client(monkeypatch, _FakeClient())
    _login(client)
    _post(
        client,
        f"/admin/radius/mt/8/site-exit/policies/{pid}/apply",
        _all_confirmations(),
    )
    import json
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(1, limit=20)
    payloads = []
    for r in rows:
        if "site_exit" in r["action"]:
            payloads.append(r.get("payload_json") or "{}")
    serialised = "\n".join(payloads).lower()
    for tripwire in (
        "private-key=", "privatekey", "private_key",
        "begin private key",
    ):
        assert tripwire not in serialised, (
            f"audit payload contains {tripwire!r}")
