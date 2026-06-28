"""Write-path caps that make the bridge bloat structurally impossible.

The 188MB-backup bug recurred because two append-only tables grew without
bound: license_admin_bridge_snapshots (165MB) and
license_admin_heartbeat_attempts (21MB). A daily prune worker is only a
backstop — the PRIMARY fix is at the WRITE PATH. These tests prove that no
matter how many rows are written, each table stays bounded:

  * The snapshot store keeps only the latest N per (tenant, snapshot_type)
    scope on every save(), always preserving the last successful snapshot.
  * The heartbeat store keeps only the latest N per tenant on every
    record_attempt().
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_writecap_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]
    from app import create_app
    yield create_app()
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]


def _count(table: str) -> int:
    from app.radius.db.connection import db
    return int(db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# ── Snapshot store ────────────────────────────────────────────────────────


def test_snapshot_save_is_bounded_per_scope(app):
    with app.app_context():
        from app.radius.services.admin_panel_client import (
            LicenseAdminSnapshotStore, SNAPSHOT_KEEP_PER_SCOPE,
        )
        store = LicenseAdminSnapshotStore()
        # Hammer 200 saves across two scopes — old behavior = 200 rows.
        for i in range(100):
            store.save(tenant_id=1, snapshot_type="license",
                       normalized_status="active", source_url="x",
                       payload={"n": i})
            store.save(tenant_id=1, snapshot_type="capacity_contract",
                       normalized_status="valid", source_url="x",
                       payload={"n": i})

        # Each scope is capped at keep (all rows here are successes, so the
        # last-success preservation adds nothing extra).
        total = _count("license_admin_bridge_snapshots")
        assert total == 2 * SNAPSHOT_KEEP_PER_SCOPE
        # And the survivors are the most-recent ones.
        latest = store.latest(tenant_id=1, snapshot_type="license")
        assert latest["payload_json"] == {"n": 99}


def test_snapshot_save_preserves_old_success(app):
    with app.app_context():
        from app.radius.services.admin_panel_client import (
            LicenseAdminSnapshotStore, SNAPSHOT_KEEP_PER_SCOPE,
        )
        store = LicenseAdminSnapshotStore()
        # One success, then a long run of failures.
        first = store.save(tenant_id=1, snapshot_type="license",
                           normalized_status="active", source_url="x",
                           payload={"ok": True})
        for i in range(50):
            store.save(tenant_id=1, snapshot_type="license",
                       normalized_status="unavailable", source_url="x",
                       error={"n": i})

        # Bounded at keep failures + the preserved old success.
        assert _count("license_admin_bridge_snapshots") == SNAPSHOT_KEEP_PER_SCOPE + 1
        success = store.latest_success(tenant_id=1, snapshot_type="license")
        assert success is not None
        assert int(success["id"]) == int(first["id"])  # the ancient success survived


def test_snapshot_cap_disabled_by_env(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_RETENTION_LICENSE_ADMIN_BRIDGE_SNAPSHOTS_KEEP", "0")
    with app.app_context():
        from app.radius.services.admin_panel_client import LicenseAdminSnapshotStore
        store = LicenseAdminSnapshotStore()
        for i in range(10):
            store.save(tenant_id=1, snapshot_type="license",
                       normalized_status="active", source_url="x", payload={"n": i})
        # Disabled → append-only (the daily worker is then the only guard).
        assert _count("license_admin_bridge_snapshots") == 10


# ── Heartbeat store ───────────────────────────────────────────────────────


def test_heartbeat_record_is_bounded_per_tenant(app):
    with app.app_context():
        from app.radius.services.license_admin_instance_health import (
            HeartbeatAttempt, InstanceHealthService, HEARTBEAT_KEEP_PER_TENANT,
        )
        # Use a tiny cap via env so the test is fast but still proves the bound.
        os.environ["HOBERADIUS_RETENTION_LICENSE_ADMIN_HEARTBEAT_ATTEMPTS_KEEP"] = "10"
        try:
            svc = InstanceHealthService()
            for i in range(60):
                svc.record_attempt(HeartbeatAttempt(
                    tenant_id=1, idempotency_key=f"k{i}", dry_run=True,
                    status="ok", payload={"n": i}))
            # Hard-capped at 10 regardless of how many were written.
            assert _count("license_admin_heartbeat_attempts") == 10
            latest = svc.latest_attempt(tenant_id=1)
            assert latest["payload_json"] == {"n": 59}
        finally:
            os.environ.pop("HOBERADIUS_RETENTION_LICENSE_ADMIN_HEARTBEAT_ATTEMPTS_KEEP", None)
        # The module default is a sane, finite bound.
        assert HEARTBEAT_KEEP_PER_TENANT > 0
