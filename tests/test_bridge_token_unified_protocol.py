"""Customer-side unified bridge-token protocol: the radius reports its token
with version + fingerprint and reconciles the panel's canonical response
(adopt on panel_wins/stale, mark acked on adopted_customer/no_change,
back-compat with legacy {ok,seq}, keep retrying on failure).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_btok_uni_")
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


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_bridge_token_report(self, *, token, version=None,
                                 fingerprint=None, issued_at=None):
        self.calls.append({"token": token, "version": version,
                           "fingerprint": fingerprint})
        return self.response


def _svc(response):
    from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService
    return BridgeTokenSyncService(admin_client=FakeClient(response))


def test_report_sends_version_and_fingerprint(app):
    with app.app_context():
        from app.radius.services.license_bridge_token_sync import _fingerprint
        svc = _svc({"ok": True, "response": {"ok": True,
                                             "outcome": "adopted_customer", "version": 3}})
        svc.generate_and_report(tenant_id=1)
        call = svc.admin_client.calls[0]
        assert call["version"] == 0                        # fresh local → v0
        assert call["fingerprint"] == _fingerprint(call["token"])
        st = svc.current_state(tenant_id=1)
        assert st["panel_acked"] is True and st["panel_seq"] == "3"
        assert st["source"] == "local"                     # our token stood


def test_adopt_panel_token_on_stale_report(app):
    with app.app_context():
        panel_token = "P" * 40
        svc = _svc({"ok": True, "response": {"ok": True, "outcome": "stale_report",
                                             "token": panel_token, "version": 7}})
        svc.generate_and_report(tenant_id=1)
        st = svc.current_state(tenant_id=1)
        assert st["source"] == "panel"
        assert st["panel_seq"] == "7"
        assert st["token_hint"] == panel_token[-4:]
        assert st["panel_acked"] is True
        assert svc.get_active_token(tenant_id=1) == panel_token   # decrypts to panel token


def test_backward_compat_legacy_seq_response(app):
    with app.app_context():
        svc = _svc({"ok": True, "response": {"ok": True, "seq": "v9"}})   # old panel
        svc.generate_and_report(tenant_id=1)
        st = svc.current_state(tenant_id=1)
        assert st["panel_acked"] is True and st["panel_seq"] == "v9"
        assert st["source"] == "local"                     # nothing to adopt


def test_connection_failure_keeps_retrying(app):
    with app.app_context():
        svc = _svc({"ok": False, "status": "unknown", "error": {"code": "x"}})
        res = svc.generate_and_report(tenant_id=1)
        assert res["ok"] is False and res["action"] == "report_failed"
        st = svc.current_state(tenant_id=1)
        assert st["panel_acked"] is False                  # not acked → retries


def test_heartbeat_reports_active_token_with_stored_version(app):
    with app.app_context():
        panel_token = "Q" * 40
        svc = _svc({"ok": True, "response": {"ok": True, "outcome": "stale_report",
                                             "token": panel_token, "version": 7}})
        svc.generate_and_report(tenant_id=1)                # adopts panel token v7
        # next cycle: heartbeat reports the adopted token WITH its version
        svc.admin_client.response = {"ok": True, "response": {
            "ok": True, "outcome": "no_change", "token": panel_token, "version": 7}}
        svc.admin_client.calls.clear()
        svc.ensure_token_and_report_pending(tenant_id=1)
        assert svc.admin_client.calls[0]["version"] == 7
        assert svc.admin_client.calls[0]["token"] == panel_token
