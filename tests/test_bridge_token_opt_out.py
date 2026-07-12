"""Opt-out for the outbound bridge-token report. When the panel doesn't serve
the /bridge-token/report endpoint, license_admin_bridge.bridge_token_enabled=0
(env HOBERADIUS_ADMIN_BRIDGE_TOKEN=0) stops the periodic failed report + log
noise WITHOUT touching the license sync. Default stays ON.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_btok_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_bridge_token_report_opt_out_short_circuits(app, monkeypatch):
    with app.app_context():
        from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService
        # OFF → both entry points no-op without touching DB/network
        monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_TOKEN", "0")
        svc = BridgeTokenSyncService()
        assert svc.ensure_token_and_report_pending(tenant_id=1) == {
            "ok": True, "action": "disabled"}
        assert svc.generate_and_report(tenant_id=1) == {
            "ok": True, "action": "disabled"}


def test_bridge_token_default_is_enabled(app, monkeypatch):
    with app.app_context():
        from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService
        monkeypatch.delenv("HOBERADIUS_ADMIN_BRIDGE_TOKEN", raising=False)
        # default ON → not short-circuited (returns a real action, not 'disabled')
        res = BridgeTokenSyncService().ensure_token_and_report_pending(tenant_id=1)
        assert res.get("action") != "disabled"
