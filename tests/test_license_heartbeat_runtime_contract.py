"""Regression: in runtime_contract_sync mode the worker must ALSO fire a
license heartbeat (/api/license/check) so SNAPSHOT_LICENSE keeps refreshing and
license_lifecycle.evaluate() cannot lock with a FALSE sync_grace_exhausted while
the runtime contract is fresh.

Proves the fix + guards the untouched paths (heartbeat failure is non-fatal;
non-runtime-contract mode still fetches license as before).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_hb_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "1")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BASE_URL", "https://panel.example")
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "TESTKEY1234567890TESTKEY123456AB")
    # exercise the REAL lifecycle evaluator (the suite-wide test bypass would
    # otherwise short-circuit evaluate() to reason="test_bypass").
    monkeypatch.delenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


class Transport:
    """Answers license-check + runtime-contract; records URLs; license-check
    behaviour is switchable to simulate a heartbeat failure."""
    def __init__(self, license_ok=True):
        self.urls = []
        self.license_ok = license_ok

    def request_json(self, **kw):
        url = kw.get("url") or ""
        self.urls.append(url)
        if url.endswith("/api/license/check"):
            if not self.license_ok:
                raise ConnectionError("simulated license-check outage")
            return {"ok": True, "status": "active",
                    "expires_at": (datetime.utcnow() + timedelta(days=20)).isoformat()}
        if url.endswith("/runtime-contract"):
            return {"ok": True, "status": "active",
                    "contract": {"license": {"active": True, "status": "active"}}}
        return {"ok": False, "status": "unexpected", "url": url}


def _svc(app, transport, *, runtime_contract_sync):
    from app.radius.services.admin_panel_client import (
        AdminBridgeConfig, AdminPanelClient, LicenseAdminSnapshotStore)
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService
    if runtime_contract_sync:
        os.environ["HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC"] = "1"
    else:
        os.environ.pop("HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC", None)
    store = LicenseAdminSnapshotStore()
    cfg = AdminBridgeConfig.from_env()
    client = AdminPanelClient(config=cfg, transport=transport, store=store)
    return LicenseAdminRuntimeSyncService(config=cfg, admin_client=client, store=store), store


def _seed_stale_license(store, days):
    from app.radius.services.admin_panel_client import SNAPSHOT_LICENSE
    from app.radius.db.connection import db
    store.save(tenant_id=1, snapshot_type=SNAPSHOT_LICENSE, normalized_status="active",
               source_url="https://panel.example/api/license/check", payload={"status": "active"},
               fetched_at=(datetime.utcnow() - timedelta(days=days)).replace(microsecond=0).isoformat())
    db().commit()


def _lic_fa(store):
    from app.radius.services.admin_panel_client import SNAPSHOT_LICENSE
    row = store.state(tenant_id=1, snapshot_type=SNAPSHOT_LICENSE)["last_success"]
    return row["fetched_at"] if row else None


def test_heartbeat_refreshes_license_and_clears_false_lock(app):
    """runtime_contract_sync=ON + stale license → heartbeat refreshes it →
    evaluate flips sync_grace_exhausted → active."""
    with app.app_context():
        from app.radius.services import license_lifecycle as LL
        from app.radius.db.connection import db
        t = Transport(license_ok=True)
        svc, store = _svc(app, t, runtime_contract_sync=True)
        _seed_stale_license(store, days=10)
        assert LL.evaluate(1).reason == "sync_grace_exhausted"          # false lock before

        before = _lic_fa(store)
        r = svc.sync_once(tenant_id=1); db().commit()
        assert r["source"] == "runtime_contract"                       # took path A
        assert any(u.endswith("/api/license/check") for u in t.urls)   # heartbeat fired
        assert any(u.endswith("/runtime-contract") for u in t.urls)    # contract still synced
        assert _lic_fa(store) != before                                # license refreshed
        assert LL.evaluate(1).reason == "active"                       # lock cleared
        assert not LL.evaluate(1).blocks_panel


def test_heartbeat_failure_is_nonfatal_to_contract(app):
    """A failing license heartbeat must not break the runtime-contract sync."""
    with app.app_context():
        from app.radius.db.connection import db
        t = Transport(license_ok=False)                                # heartbeat raises
        svc, store = _svc(app, t, runtime_contract_sync=True)
        r = svc.sync_once(tenant_id=1); db().commit()
        assert r["ok"] is True and r["source"] == "runtime_contract"   # contract still OK
        assert any(u.endswith("/api/license/check") for u in t.urls)   # heartbeat was attempted


def test_non_runtime_contract_mode_unchanged(app):
    """runtime_contract_sync=OFF still goes through the license path (path B)."""
    with app.app_context():
        from app.radius.db.connection import db
        t = Transport(license_ok=True)
        svc, store = _svc(app, t, runtime_contract_sync=False)
        r = svc.sync_once(tenant_id=1); db().commit()
        assert r["source"] == "license_check"                          # path B
        assert any(u.endswith("/api/license/check") for u in t.urls)
        assert _lic_fa(store) is not None                              # license snapshot written
