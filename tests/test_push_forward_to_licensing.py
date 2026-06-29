"""Radius push is a THIN FORWARDER to the licensing panel (central FCM).

Proves the reconciled design after centralizing FCM in licensing:
  • the radius module no longer holds a Firebase key or calls FCM (the old
    fcm_push / fcm_credentials / device_push_tokens_repo modules are gone);
  • a locally-created notification forwards its push to licensing over the
    signed bridge; a bridge-sourced notification does NOT (licensing owns it);
  • the «أرسل إشعار تجريبي» test-push forwards (mode=sync) and maps the panel
    response;
  • the app's device-token registration endpoint forwards to licensing.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_push_fwd_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────────────────────────────────────────────────────────────────
# The misplaced modules are gone
# ─────────────────────────────────────────────────────────────────────────

def test_local_fcm_modules_removed():
    import importlib
    for mod in ("app.services.fcm_push", "app.services.fcm_credentials",
                "app.radius.db.repos.device_push_tokens_repo"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


# ─────────────────────────────────────────────────────────────────────────
# notify(): local forwards, bridge does not
# ─────────────────────────────────────────────────────────────────────────

def test_local_notification_forwards_push(app, monkeypatch):
    from app.radius.services import notifications as notif

    calls = []
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    with app.app_context():
        nid = notif.notify(1, type="system", title="مرحبا", body="جسم",
                           link="/x", source="local", dedup_key="local-1")
        assert nid is not None
        assert len(calls) == 1
        assert calls[0]["title"] == "مرحبا"
        assert calls[0]["ntype"] == "system"


def test_bridge_notification_does_not_forward_push(app, monkeypatch):
    from app.radius.services import notifications as notif

    calls = []
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    with app.app_context():
        nid = notif.notify(1, type="license", title="من اللوحة", body="b",
                           source="bridge", source_ref="r1", dedup_key="bridge:r1")
        assert nid is not None
        assert calls == []  # licensing owns bridge notifications; no re-forward


# ─────────────────────────────────────────────────────────────────────────
# _forward_push / send_test_push go through the bridge
# ─────────────────────────────────────────────────────────────────────────

def test_send_test_push_forwards_sync_and_maps_response(app, monkeypatch):
    from app.radius.services import admin_panel_client
    from app.radius.services import notifications as notif

    calls = {}

    class FakePanel:
        def forward_push(self, **kwargs):
            calls.update(kwargs)
            return {"ok": True, "status": "ok",
                    "response": {"ok": True, "status": "sent", "sent": 3,
                                 "failed": 0, "devices": 3}}

    monkeypatch.setattr(admin_panel_client, "AdminPanelClient", FakePanel)
    with app.app_context():
        res = notif.send_test_push(1)
    assert calls["mode"] == "sync"
    assert res["ok"] is True and res["sent"] == 3 and res["devices"] == 3


def test_send_test_push_surfaces_fcm_disabled(app, monkeypatch):
    from app.radius.services import admin_panel_client
    from app.radius.services import notifications as notif

    class FakePanel:
        def forward_push(self, **kwargs):
            return {"ok": True, "status": "ok",
                    "response": {"ok": False, "status": "fcm_disabled",
                                 "sent": 0, "failed": 0, "devices": 1}}

    monkeypatch.setattr(admin_panel_client, "AdminPanelClient", FakePanel)
    with app.app_context():
        res = notif.send_test_push(1)
    assert res["ok"] is False and res["reason"] == "fcm_disabled"


def test_send_test_push_handles_bridge_unavailable(app, monkeypatch):
    from app.radius.services import admin_panel_client
    from app.radius.services import notifications as notif

    class FakePanel:
        def forward_push(self, **kwargs):
            return {"ok": False, "status": "disabled", "error": {"code": "bridge_disabled"}}

    monkeypatch.setattr(admin_panel_client, "AdminPanelClient", FakePanel)
    with app.app_context():
        res = notif.send_test_push(1)
    assert res["ok"] is False and res["reason"] == "disabled"


# ─────────────────────────────────────────────────────────────────────────
# Device-token endpoint forwards to licensing
# ─────────────────────────────────────────────────────────────────────────

def test_register_push_token_forwards_to_licensing(client, monkeypatch):
    from app.radius.services import admin_panel_client

    calls = {}

    class FakePanel:
        def register_push_token(self, **kwargs):
            calls.update(kwargs)
            return {"ok": True, "status": "ok",
                    "response": {"ok": True, "status": "registered", "devices": 1}}

    monkeypatch.setattr(admin_panel_client, "AdminPanelClient", FakePanel)
    res = client.post("/api/v1/devices/push-token", headers=AUTH,
                      json={"token": "fcm-abc", "platform": "android",
                            "app_version": "2.0"})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["registered"] is True and data["count"] == 1
    assert calls["token"] == "fcm-abc" and calls["platform"] == "android"


def test_unregister_push_token_forwards_to_licensing(client, monkeypatch):
    from app.radius.services import admin_panel_client

    calls = {}

    class FakePanel:
        def unregister_push_token(self, **kwargs):
            calls.update(kwargs)
            return {"ok": True, "status": "ok",
                    "response": {"ok": True, "status": "unregistered", "removed": 1}}

    monkeypatch.setattr(admin_panel_client, "AdminPanelClient", FakePanel)
    res = client.delete("/api/v1/devices/push-token", headers=AUTH,
                        json={"token": "fcm-abc"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["removed"] == 1
    assert calls["token"] == "fcm-abc"


def test_register_push_token_rejects_missing_token(client):
    res = client.post("/api/v1/devices/push-token", headers=AUTH, json={})
    assert res.status_code == 400
