"""VX2.1 — Site exit data model + repository tests."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_vx2_1_")
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


def test_migration_043_creates_all_five_tables(app):
    with app.app_context():
        from app.radius.db.connection import db
        names = {
            r["name"] for r in db().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for tbl in (
        "vps_exit_nodes", "site_exit_policies",
        "site_exit_targets", "site_exit_deployments",
        "site_exit_script_versions",
    ):
        assert tbl in names, f"missing table {tbl}"


def test_no_private_key_column_anywhere(app):
    """The data model promises secrets never land in the DB.
    A literal column named *private_key* would be the most
    obvious slip; this test guards against it."""
    with app.app_context():
        from app.radius.db.connection import db
        for tbl in (
            "vps_exit_nodes", "site_exit_policies",
            "site_exit_targets", "site_exit_deployments",
            "site_exit_script_versions",
        ):
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
                assert "secret" not in cl, (
                    f"{tbl}.{c} looks like a secret column"
                )


# ─── vps_exit_nodes_repo ─────────────────────────────────────


def test_vps_exit_nodes_create_and_get(app):
    with app.app_context():
        from app.radius.db.repos import vps_exit_nodes_repo as r
        nid = r.create(
            tenant_id=1, name="vps-main",
            public_ip="203.0.113.10",
            wireguard_interface_name="wg-vps",
            wireguard_gateway_ip="10.10.0.1",
            tunnel_cidr="10.10.0.0/24",
            enabled=True,
        )
        row = r.get_by_id(1, nid)
    assert row["name"] == "vps-main"
    assert row["public_ip"] == "203.0.113.10"
    assert row["enabled"] == 1
    assert row["last_health_status"] == ""


def test_vps_exit_nodes_name_unique_per_tenant(app):
    with app.app_context():
        import sqlite3
        from app.radius.db.repos import vps_exit_nodes_repo as r
        r.create(tenant_id=1, name="dup")
        with pytest.raises(sqlite3.IntegrityError):
            r.create(tenant_id=1, name="dup")


def test_vps_exit_nodes_update_ignores_unknown_keys(app):
    """If someone tries to slip a column we never want
    written — e.g. private_key — the repo must refuse silently
    instead of writing it."""
    with app.app_context():
        from app.radius.db.repos import vps_exit_nodes_repo as r
        nid = r.create(tenant_id=1, name="evolve")
        row = r.update(
            1, nid,
            public_ip="198.51.100.1",
            private_key="this should not be written",
            secret="nor this",
        )
    assert row["public_ip"] == "198.51.100.1"
    # The phantom columns simply don't exist on the row.
    assert "private_key" not in row
    assert "secret" not in row


def test_vps_exit_nodes_set_health_writes_only_health(app):
    with app.app_context():
        from app.radius.db.repos import vps_exit_nodes_repo as r
        nid = r.create(tenant_id=1, name="probe-target",
                        public_ip="203.0.113.20")
        ok = r.set_health(1, nid, status=r.HEALTH_OK,
                           last_handshake_at="2026-05-22T20:00:00Z")
        row = r.get_by_id(1, nid)
    assert ok
    assert row["last_health_status"] == "ok"
    assert row["last_handshake_at"] == "2026-05-22T20:00:00Z"
    # Operator-configured fields untouched.
    assert row["public_ip"] == "203.0.113.20"


def test_vps_exit_nodes_set_health_normalises_bad_value(app):
    with app.app_context():
        from app.radius.db.repos import vps_exit_nodes_repo as r
        nid = r.create(tenant_id=1, name="probe-2")
        r.set_health(1, nid, status="garbage")
        row = r.get_by_id(1, nid)
    # Unknown status falls back to the blank "" — never raises.
    assert row["last_health_status"] == ""


# ─── site_exit_policies_repo ─────────────────────────────────


def _seed_node(app, name="vps-x"):
    from app.radius.db.repos import vps_exit_nodes_repo as r
    return r.create(tenant_id=1, name=name, enabled=True)


def test_policy_create_and_slugify(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_policies_repo as p,
        )
        nid = _seed_node(app)
        pid = p.create(
            tenant_id=1, router_id=42, exit_node_id=nid,
            name="My Policy V1",
            fail_mode=p.FAIL_MODE_BLOCK_WHEN_VPS_DOWN,
        )
        row = p.get_by_id(1, pid)
    assert row["name"] == "My Policy V1"
    assert row["slug"] == "my-policy-v1"
    assert row["fail_mode"] == "block_when_vps_down"
    assert row["include_subdomains"] == 1
    assert row["include_router_output"] == 0


def test_policy_rejects_bad_fail_mode(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_policies_repo as p,
        )
        nid = _seed_node(app)
        with pytest.raises(ValueError):
            p.create(
                tenant_id=1, router_id=1, exit_node_id=nid,
                name="bad", fail_mode="route_all_traffic",
            )


def test_policy_slug_unique_per_tenant(app):
    with app.app_context():
        import sqlite3
        from app.radius.db.repos import (
            site_exit_policies_repo as p,
        )
        nid = _seed_node(app)
        p.create(tenant_id=1, router_id=1, exit_node_id=nid,
                 name="alpha", slug="same")
        with pytest.raises(sqlite3.IntegrityError):
            p.create(tenant_id=1, router_id=2, exit_node_id=nid,
                     name="beta", slug="same")


def test_policy_delete_cascades_to_targets(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_policies_repo as p,
            site_exit_targets_repo as t,
        )
        nid = _seed_node(app)
        pid = p.create(tenant_id=1, router_id=1,
                        exit_node_id=nid, name="x")
        t.add(policy_id=pid, value="example.com",
              target_type=t.TARGET_TYPE_DOMAIN,
              group_name=t.GROUP_SPEEDTEST_MEASUREMENT)
        assert len(t.list_for_policy(pid)) == 1
        p.delete(1, pid)
        assert t.list_for_policy(pid) == []


# ─── site_exit_targets_repo ──────────────────────────────────


def _seed_policy(app):
    from app.radius.db.repos import (
        site_exit_policies_repo as p,
        vps_exit_nodes_repo as v,
    )
    nid = v.create(tenant_id=1, name="vps-t", enabled=True)
    return p.create(tenant_id=1, router_id=7,
                     exit_node_id=nid, name="t-policy")


def test_target_add_idempotent_on_normalized_value(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        pid = _seed_policy(app)
        a = t.add(policy_id=pid, value="speedtest.net",
                   normalized_value="speedtest.net",
                   target_type=t.TARGET_TYPE_DOMAIN,
                   group_name=t.GROUP_SPEEDTEST_MEASUREMENT)
        b = t.add(policy_id=pid, value="SPEEDTEST.NET",
                   normalized_value="speedtest.net",
                   target_type=t.TARGET_TYPE_DOMAIN,
                   group_name=t.GROUP_SPEEDTEST_MEASUREMENT)
    # Same normalized_value → same row id.
    assert a == b
    assert a > 0


def test_target_rejects_invalid_enums(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        pid = _seed_policy(app)
        with pytest.raises(ValueError):
            t.add(policy_id=pid, value="x",
                   target_type="wildcard",
                   group_name=t.GROUP_SPEEDTEST_MEASUREMENT)
        with pytest.raises(ValueError):
            t.add(policy_id=pid, value="example.com",
                   target_type=t.TARGET_TYPE_DOMAIN,
                   group_name="brand-new-group")
        with pytest.raises(ValueError):
            t.add(policy_id=pid, value="example.com",
                   target_type=t.TARGET_TYPE_DOMAIN,
                   group_name=t.GROUP_RAW_IP_TARGETS,
                   status="archived")


def test_target_group_counts_aggregate(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        pid = _seed_policy(app)
        for d, g in (
            ("speedtest.net", t.GROUP_SPEEDTEST_MEASUREMENT),
            ("fast.com",      t.GROUP_SPEEDTEST_MEASUREMENT),
            ("whatismyip.com", t.GROUP_PUBLIC_IP_CHECKERS),
        ):
            t.add(policy_id=pid, value=d,
                   normalized_value=d,
                   target_type=t.TARGET_TYPE_DOMAIN,
                   group_name=g)
        counts = t.group_counts(pid)
    assert counts["speedtest_measurement"] == 2
    assert counts["public_ip_checkers"] == 1
    assert counts["raw_ip_targets"] == 0
    assert counts["total"] == 3


def test_target_add_many_summary(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        pid = _seed_policy(app)
        out = t.add_many(pid, [
            {"value": "speedtest.net",
             "normalized_value": "speedtest.net",
             "target_type": t.TARGET_TYPE_DOMAIN,
             "group_name": t.GROUP_SPEEDTEST_MEASUREMENT},
            {"value": "speedtest.net",
             "normalized_value": "speedtest.net",
             "target_type": t.TARGET_TYPE_DOMAIN,
             "group_name": t.GROUP_SPEEDTEST_MEASUREMENT},
            {"value": "rotten",
             "target_type": "wildcard",  # invalid enum
             "group_name": t.GROUP_MANUAL_REVIEW},
        ])
    assert out["inserted"] == 1
    assert out["updated"] == 1
    assert out["skipped"] == 1


# ─── site_exit_deployments_repo ──────────────────────────────


def test_deployment_ensure_creates_one_draft_per_policy(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_deployments_repo as d,
        )
        pid = _seed_policy(app)
        first = d.ensure_for_policy(
            tenant_id=1, policy_id=pid, router_id=7)
        again = d.ensure_for_policy(
            tenant_id=1, policy_id=pid, router_id=7)
    assert first["status"] == "draft"
    assert first["id"] == again["id"]


def test_deployment_records_preview_apply_failure(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_deployments_repo as d,
        )
        pid = _seed_policy(app)
        prev = d.record_preview(
            tenant_id=1, policy_id=pid, router_id=7,
            script_hash="hash-A")
        assert prev["status"] == "previewed"
        assert prev["generated_script_hash"] == "hash-A"

        ok = d.record_apply_success(
            tenant_id=1, policy_id=pid, router_id=7,
            script_hash="hash-A", audit_id=42)
        assert ok["status"] == "applied"
        assert ok["last_audit_id"] == 42
        assert ok["last_error"] == ""

        bad = d.record_apply_failure(
            tenant_id=1, policy_id=pid, router_id=7,
            error="MikroTik refused", audit_id=43)
    assert bad["status"] == "failed"
    assert "MikroTik refused" in bad["last_error"]
    assert bad["last_audit_id"] == 43


def test_deployment_set_status_validates(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_deployments_repo as d,
        )
        pid = _seed_policy(app)
        d.ensure_for_policy(
            tenant_id=1, policy_id=pid, router_id=7)
        with pytest.raises(ValueError):
            d.set_status(tenant_id=1, policy_id=pid,
                          status="brand-new")
        out = d.set_status(tenant_id=1, policy_id=pid,
                            status=d.STATUS_DISABLED)
    assert out["status"] == "disabled"


# ─── site_exit_scripts_repo ──────────────────────────────────


def test_script_record_and_lookup_by_hash(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_scripts_repo as s,
        )
        pid = _seed_policy(app)
        body = (
            "/routing table add name=HOBE_VX2_1 fib "
            "comment=\"HOBE_VX2_SITE_EXIT:1:routing-table\"\n"
            "/ip firewall address-list add list=HOBE_VX2_DST_1 "
            "address=speedtest.net "
            "comment=\"HOBE_VX2_SITE_EXIT:1:target:1:speedtest_measurement\"\n"
        )
        sid = s.record(
            policy_id=pid,
            script_body=body,
            rollback_script_body=(
                "/ip firewall address-list remove "
                "[find comment~\"HOBE_VX2_SITE_EXIT:1:\"]\n"
            ),
            command_count=2,
        )
        row = s.get_by_id(sid)
        same = s.get_by_hash(row["script_hash"])
    assert row["command_count"] == 2
    assert row["script_hash"] == s.compute_hash(body)
    assert same["id"] == row["id"]


def test_script_refuses_to_store_private_key_in_body(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_scripts_repo as s,
        )
        pid = _seed_policy(app)
        nasty = (
            "/interface wireguard add name=wg-vps "
            "listen-port=51820 private-key=THIS_IS_SECRET\n"
        )
        with pytest.raises(s.SecretInScriptError):
            s.record(policy_id=pid, script_body=nasty)


def test_script_latest_for_policy_returns_newest(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_scripts_repo as s,
        )
        pid = _seed_policy(app)
        first = s.record(policy_id=pid,
                          script_body="# v1\n/foo\n")
        second = s.record(policy_id=pid,
                           script_body="# v2\n/foo\n/bar\n")
        latest = s.latest_for_policy(pid)
        listing = s.list_for_policy(pid)
    assert latest["id"] == second
    # listing is newest-first and bodies are omitted to keep
    # responses small.
    assert listing[0]["id"] == second
    assert listing[1]["id"] == first
    assert "script_body" not in listing[0]


def test_script_compute_hash_deterministic(app):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_scripts_repo as s,
        )
    assert s.compute_hash("a") == s.compute_hash("a")
    assert s.compute_hash("a") != s.compute_hash("b")
    assert len(s.compute_hash("anything")) == 64  # sha256 hex
