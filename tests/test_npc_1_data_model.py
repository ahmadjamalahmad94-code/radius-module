"""NPC Phase 1 — migration 044 + the five new repositories."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_1_")
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


# ─── Migration ───────────────────────────────────────────────


_EXPECTED_TABLES = (
    "npc_remote_access_policies",
    "npc_web_block_policies",
    "npc_web_block_targets",
    "npc_walled_garden_policies",
    "npc_walled_garden_entries",
    "npc_deployments",
    "npc_script_versions",
)


def test_migration_044_creates_all_seven_tables(app):
    with app.app_context():
        from app.radius.db.connection import db
        names = {
            r["name"] for r in db().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for tbl in _EXPECTED_TABLES:
        assert tbl in names, f"missing table {tbl}"


def test_no_secret_columns_on_any_npc_table(app):
    """Phase 1 schema promise: no `private_key`, `password`,
    `secret` column on any NPC table. The repos' allow-listed
    update() is the second guard; this is the schema-level
    one."""
    with app.app_context():
        from app.radius.db.connection import db
        for tbl in _EXPECTED_TABLES:
            cols = [
                r["name"] for r in db().execute(
                    f"PRAGMA table_info({tbl})"
                ).fetchall()
            ]
            for c in cols:
                cl = c.lower()
                assert "private_key" not in cl, (
                    f"{tbl}.{c} looks like a secret column"
                )
                assert "privatekey" not in cl
                assert "password" not in cl, (
                    f"{tbl}.{c} looks like a secret column"
                )
                assert "secret" not in cl, (
                    f"{tbl}.{c} looks like a secret column"
                )


# ─── npc_remote_access_repo ─────────────────────────────────


def test_remote_access_create_and_get(app):
    with app.app_context():
        from app.radius.db.repos import npc_remote_access_repo as r
        pid = r.create(
            tenant_id=1, router_id=42,
            name="Emergency winbox window",
            allow_winbox=True, allow_ssh=True,
            source_address_list="ops-bastion",
            expires_at="2026-06-01T12:00:00Z",
            reason="Investigating reboot loop",
        )
        row = r.get_by_id(1, pid)
    assert row["name"] == "Emergency winbox window"
    assert row["slug"] == "emergency-winbox-window"
    assert row["allow_winbox"] == 1
    assert row["allow_ssh"] == 1
    assert row["allow_api"] == 0
    assert row["source_address_list"] == "ops-bastion"
    assert row["enabled"] == 1


def test_remote_access_arabic_slug_fallback(app):
    with app.app_context():
        from app.radius.db.repos import npc_remote_access_repo as r
        pid = r.create(tenant_id=1, router_id=1,
                       name="نافذة دعم طارئة")
        row = r.get_by_id(1, pid)
    assert row["slug"].startswith("policy-")
    assert all(c.isascii() for c in row["slug"])


def test_remote_access_slug_unique_per_tenant(app):
    with app.app_context():
        import sqlite3
        from app.radius.db.repos import npc_remote_access_repo as r
        r.create(tenant_id=1, router_id=1, name="ops",
                 slug="ops")
        with pytest.raises(sqlite3.IntegrityError):
            r.create(tenant_id=1, router_id=2, name="ops",
                     slug="ops")


def test_remote_access_update_ignores_unknown_keys(app):
    """Allow-list defence: a caller trying to set
    `password=...` must be silently dropped."""
    with app.app_context():
        from app.radius.db.repos import npc_remote_access_repo as r
        pid = r.create(tenant_id=1, router_id=1, name="x")
        row = r.update(
            1, pid,
            allow_winbox=False,
            password="should-not-write",
            secret="nor-this",
            random_extra="ignore",
        )
    assert row["allow_winbox"] == 0
    assert "password" not in row
    assert "secret" not in row


def test_remote_access_delete_cascades_through_npc_tables(app):
    """delete() must clear shared deployments + script rows
    keyed on this policy id."""
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as r,
            npc_deployments_repo as d,
            npc_scripts_repo as s,
            npc_common as nc,
        )
        pid = r.create(tenant_id=1, router_id=1, name="bye")
        d.ensure_for_policy(
            tenant_id=1, service=nc.SERVICE_REMOTE_ACCESS,
            policy_id=pid, router_id=1,
        )
        s.record(
            service=nc.SERVICE_REMOTE_ACCESS,
            policy_id=pid,
            script_body="# noop\n",
        )
        assert r.delete(1, pid) is True
        from app.radius.db.connection import db
        deps = db().execute(
            "SELECT 1 FROM npc_deployments "
            "WHERE service='remote_access' AND policy_id=?",
            (pid,),
        ).fetchall()
        scripts = db().execute(
            "SELECT 1 FROM npc_script_versions "
            "WHERE service='remote_access' AND policy_id=?",
            (pid,),
        ).fetchall()
    assert deps == []
    assert scripts == []


# ─── npc_web_block_repo ──────────────────────────────────────


def test_web_block_policy_and_targets(app):
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pid = wb.create_policy(
            tenant_id=1, router_id=10,
            name="TikTok block — Hotspot",
            fail_open=True,
        )
        a = wb.add_target(
            policy_id=pid, value="TIKTOK.COM",
            normalized_value="tiktok.com",
            target_type=wb.TARGET_TYPE_DOMAIN,
            category="tiktok",
        )
        b = wb.add_target(
            policy_id=pid, value="tiktok.com",
            normalized_value="tiktok.com",
            target_type=wb.TARGET_TYPE_DOMAIN,
            category="tiktok",
        )
        listing = wb.list_targets(pid)
        counts = wb.target_counts(pid)
    # Idempotent on (policy_id, normalized_value).
    assert a == b
    assert len(listing) == 1
    assert counts["tiktok"] == 1
    assert counts["total"] == 1


def test_web_block_target_rejects_invalid_enums(app):
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pid = wb.create_policy(tenant_id=1, router_id=1,
                               name="x")
        with pytest.raises(ValueError):
            wb.add_target(policy_id=pid, value="x",
                          target_type="wildcard",
                          category="custom")
        with pytest.raises(ValueError):
            wb.add_target(policy_id=pid, value="example.com",
                          target_type=wb.TARGET_TYPE_DOMAIN,
                          status="archived")


def test_web_block_add_many_summary(app):
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pid = wb.create_policy(tenant_id=1, router_id=1,
                               name="batch")
        out = wb.add_targets_many(pid, [
            {"value": "tiktok.com",
             "normalized_value": "tiktok.com",
             "target_type": wb.TARGET_TYPE_DOMAIN,
             "category": "tiktok"},
            {"value": "tiktok.com",
             "normalized_value": "tiktok.com",
             "target_type": wb.TARGET_TYPE_DOMAIN,
             "category": "tiktok"},
            {"value": "garbage",
             "target_type": "wildcard"},
        ])
    assert out["inserted"] == 1
    assert out["updated"] == 1
    assert out["skipped"] == 1


def test_web_block_policy_delete_cascades_targets(app):
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pid = wb.create_policy(tenant_id=1, router_id=1,
                               name="d")
        wb.add_target(policy_id=pid, value="a.com",
                      normalized_value="a.com",
                      target_type=wb.TARGET_TYPE_DOMAIN,
                      category="custom")
        assert len(wb.list_targets(pid)) == 1
        wb.delete_policy(1, pid)
        assert wb.list_targets(pid) == []


# ─── npc_walled_garden_repo ──────────────────────────────────


def test_walled_garden_policy_and_entries(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_walled_garden_repo as wg,
        )
        pid = wg.create_policy(
            tenant_id=1, router_id=10,
            hotspot_profile="hsprof1",
            name="Payment + SMS OTP allowlist",
        )
        wg.add_entry(
            policy_id=pid, value="api.payments.test",
            normalized_value="api.payments.test",
            entry_type=wg.ENTRY_TYPE_DST_HOST,
        )
        wg.add_entry(
            policy_id=pid, value="api.payments.test",
            normalized_value="api.payments.test",
            entry_type=wg.ENTRY_TYPE_DST_ADDRESS,
        )
        listing = wg.list_entries(pid)
        counts = wg.entry_counts(pid)
    # Same host but different entry_type ⇒ two distinct rows
    # (dedup key is the triple).
    assert len(listing) == 2
    assert counts["dst_host"] == 1
    assert counts["dst_address"] == 1
    assert counts["total"] == 2


def test_walled_garden_entry_rejects_invalid_enums(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_walled_garden_repo as wg,
        )
        pid = wg.create_policy(tenant_id=1, router_id=1,
                               name="x")
        with pytest.raises(ValueError):
            wg.add_entry(policy_id=pid, value="a.com",
                         entry_type="dst_glob")


def test_walled_garden_idempotent_on_triple(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_walled_garden_repo as wg,
        )
        pid = wg.create_policy(tenant_id=1, router_id=1,
                               name="idem")
        a = wg.add_entry(policy_id=pid, value="HI.COM",
                         normalized_value="hi.com",
                         entry_type=wg.ENTRY_TYPE_DST_HOST)
        b = wg.add_entry(policy_id=pid, value="hi.com",
                         normalized_value="hi.com",
                         entry_type=wg.ENTRY_TYPE_DST_HOST)
    assert a == b


def test_walled_garden_policy_delete_cascades_entries(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_walled_garden_repo as wg,
        )
        pid = wg.create_policy(tenant_id=1, router_id=1,
                               name="del")
        wg.add_entry(policy_id=pid, value="x.com",
                     normalized_value="x.com",
                     entry_type=wg.ENTRY_TYPE_DST_HOST)
        wg.delete_policy(1, pid)
        assert wg.list_entries(pid) == []


# ─── npc_deployments_repo + npc_scripts_repo ─────────────────


def test_deployments_service_discriminator_rejects_bogus(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_deployments_repo as d,
        )
        with pytest.raises(ValueError):
            d.ensure_for_policy(
                tenant_id=1, service="bogus_service",
                policy_id=1, router_id=1,
            )


def test_deployments_full_lifecycle_per_service(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_deployments_repo as d,
            npc_common as nc,
        )
        # Same policy_id can exist across services without
        # collision — the (service, policy_id) pair is what
        # identifies a deployment.
        for svc in (nc.SERVICE_REMOTE_ACCESS,
                    nc.SERVICE_WEB_BLOCK,
                    nc.SERVICE_WALLED_GARDEN):
            row = d.ensure_for_policy(
                tenant_id=1, service=svc,
                policy_id=7, router_id=3,
            )
            assert row["status"] == "draft"
            assert row["service"] == svc

        prev = d.record_preview(
            tenant_id=1, service=nc.SERVICE_WEB_BLOCK,
            policy_id=7, router_id=3,
            script_hash="abc123",
        )
        assert prev["status"] == "previewed"
        assert prev["generated_script_hash"] == "abc123"

        ok = d.record_apply_success(
            tenant_id=1, service=nc.SERVICE_WEB_BLOCK,
            policy_id=7, router_id=3,
            script_hash="abc123", audit_id=99,
        )
        assert ok["status"] == "applied"
        assert ok["last_audit_id"] == 99
        assert ok["last_error"] == ""

        bad = d.record_apply_failure(
            tenant_id=1, service=nc.SERVICE_WEB_BLOCK,
            policy_id=7, router_id=3,
            error="MikroTikTrap: bad-syntax",
            audit_id=100,
        )
        assert bad["status"] == "failed"
        assert "bad-syntax" in bad["last_error"]


def test_deployments_list_per_service_filter(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_deployments_repo as d,
            npc_common as nc,
        )
        for svc in (nc.SERVICE_REMOTE_ACCESS,
                    nc.SERVICE_WEB_BLOCK,
                    nc.SERVICE_WALLED_GARDEN):
            d.ensure_for_policy(
                tenant_id=1, service=svc,
                policy_id=1, router_id=1,
            )
        all_rows = d.list_for_tenant(1)
        only_wb = d.list_for_tenant(
            1, service=nc.SERVICE_WEB_BLOCK,
        )
    assert len(all_rows) == 3
    assert len(only_wb) == 1
    assert only_wb[0]["service"] == "web_block"


def test_deployments_set_status_validates(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_deployments_repo as d,
            npc_common as nc,
        )
        d.ensure_for_policy(
            tenant_id=1, service=nc.SERVICE_WEB_BLOCK,
            policy_id=1, router_id=1,
        )
        with pytest.raises(ValueError):
            d.set_status(
                tenant_id=1, service=nc.SERVICE_WEB_BLOCK,
                policy_id=1, status="brand-new",
            )
        out = d.set_status(
            tenant_id=1, service=nc.SERVICE_WEB_BLOCK,
            policy_id=1, status=d.STATUS_DISABLED,
        )
    assert out["status"] == "disabled"


def test_scripts_record_and_get_by_hash(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_scripts_repo as s, npc_common as nc,
        )
        body = (
            "# remote access toggles for policy 1\n"
            "/ip firewall filter add "
            "comment=\"HOBE_NPC_REMOTE:1:winbox\" "
            "chain=input action=accept "
            "protocol=tcp dst-port=8291\n"
        )
        sid = s.record(
            service=nc.SERVICE_REMOTE_ACCESS,
            policy_id=1,
            script_body=body,
            rollback_script_body=(
                "/ip firewall filter remove "
                "[find comment~\"HOBE_NPC_REMOTE:1:\"]\n"
            ),
            command_count=1,
        )
        row = s.get_by_id(sid)
        same = s.get_by_hash(
            service=nc.SERVICE_REMOTE_ACCESS,
            script_hash=row["script_hash"],
        )
    assert row["command_count"] == 1
    assert row["script_hash"] == s.compute_hash(body)
    assert same["id"] == row["id"]


def test_scripts_refuse_to_store_secrets(app):
    """Scripts repo refuses any body containing password= or
    private-key= — even if the renderer somehow let one
    through."""
    with app.app_context():
        from app.radius.db.repos import (
            npc_scripts_repo as s, npc_common as nc,
        )
        for nasty in (
            "/user set admin password=hunter2",
            "/interface wireguard add private-key=abc",
            "PrivateKey = SOMETHING_BASE64\n",
        ):
            with pytest.raises(s.SecretInScriptError):
                s.record(
                    service=nc.SERVICE_REMOTE_ACCESS,
                    policy_id=1,
                    script_body=nasty,
                )


def test_scripts_latest_for_policy_returns_newest(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_scripts_repo as s, npc_common as nc,
        )
        first = s.record(
            service=nc.SERVICE_WEB_BLOCK,
            policy_id=1, script_body="# v1\n/foo\n",
        )
        second = s.record(
            service=nc.SERVICE_WEB_BLOCK,
            policy_id=1, script_body="# v2\n/foo\n/bar\n",
        )
        latest = s.latest_for_policy(
            service=nc.SERVICE_WEB_BLOCK,
            policy_id=1,
        )
        listing = s.list_for_policy(
            service=nc.SERVICE_WEB_BLOCK,
            policy_id=1,
        )
    assert latest["id"] == second
    assert listing[0]["id"] == second
    assert listing[1]["id"] == first
    # Bodies stripped from listing.
    assert "script_body" not in listing[0]


def test_scripts_compute_hash_deterministic(app):
    with app.app_context():
        from app.radius.db.repos import npc_scripts_repo as s
    assert s.compute_hash("a") == s.compute_hash("a")
    assert s.compute_hash("a") != s.compute_hash("b")
    assert len(s.compute_hash("anything")) == 64  # sha256 hex


# ─── slug helpers ────────────────────────────────────────────


def test_slugify_handles_ascii_and_arabic_alike(app):
    with app.app_context():
        from app.radius.db.repos import npc_common as nc
    # ASCII → normal kebab-case.
    assert nc.slugify("Policy v1") == "policy-v1"
    # Arabic → hash fallback.
    a = nc.slugify("سياسة الإنترنت")
    b = nc.slugify("سياسة الإنترنت")
    assert a.startswith("policy-")
    assert a == b   # deterministic
    # Empty → empty.
    assert nc.slugify("") == ""
