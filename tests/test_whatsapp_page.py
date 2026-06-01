"""Page tests for the WhatsApp subscriber-messaging admin page.

NO network: ``AdminPanelClient.get_whatsapp_status`` (and ``send_whatsapp_test``)
are monkeypatched on the class so the page never reaches out. We assert:
  * the page renders 200 with a CONNECTED status,
  * a bridge FAILURE renders the pending «تعذّر جلب الحالة …» card (still 200),
  * the page exposes NO ``graph.facebook.com`` and NO Meta-token input field,
  * POSTing the settings form persists a per-event toggle to tenant_settings.
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "whatsapp_page.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "wa_admin"
        sess["admin_name"] = "WA Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "wa-csrf"


def _patch_status(monkeypatch, value):
    from app.radius.services.admin_panel_client import AdminPanelClient

    monkeypatch.setattr(AdminPanelClient, "get_whatsapp_status", lambda self: value)


def _connected_status() -> dict:
    return {
        "ok": True,
        "status": "connected",
        "response": {
            "enabled": True,
            "connected": True,
            "phone": "962790001122",
            "usage": {"sent": 12, "remaining": 988, "limit": 1000},
        },
    }


def test_whatsapp_page_renders_connected_status(app, monkeypatch):
    _patch_status(monkeypatch, _connected_status())
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/whatsapp")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "text/html" in response.content_type
    assert "رسائل واتساب للمشتركين" in html
    assert "متصل" in html  # connected pill
    assert "962790001122" in html  # phone from the bridge
    # No pending error card when the bridge succeeds.
    assert "تعذّر جلب الحالة من لوحة التراخيص" not in html


def test_whatsapp_page_failure_renders_pending_card(app, monkeypatch):
    _patch_status(monkeypatch, {"ok": False, "status": "unavailable"})
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/whatsapp")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "تعذّر جلب الحالة من لوحة التراخيص" in html


def test_whatsapp_page_has_no_meta_endpoint_and_no_token_input(app, monkeypatch):
    _patch_status(monkeypatch, _connected_status())
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/whatsapp").get_data(as_text=True)

    lower = html.lower()
    # No direct Meta endpoint reference.
    assert "graph.facebook.com" not in lower
    assert "facebook.com" not in lower
    # No Meta-token input of any kind on the page.
    for forbidden in (
        'name="access_token"',
        'name="meta_token"',
        'name="whatsapp_token"',
        'name="waba_id"',
        'name="app_secret"',
        'name="verify_token"',
        'name="phone_number_id"',
        'name="token"',
    ):
        assert forbidden not in lower, f"page must not contain a token field: {forbidden}"


def test_whatsapp_page_shows_event_toggles_and_test_form(app, monkeypatch):
    _patch_status(monkeypatch, _connected_status())
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/whatsapp").get_data(as_text=True)

    # Every event gate appears as a checkbox.
    for key in ("otp", "expiry", "quota", "maintenance", "password", "portal"):
        assert f'name="send_{key}"' in html
    # The test-message form posts to the test route and asks for a phone.
    assert "/admin/radius/whatsapp/test" in html
    assert 'name="recipient_phone"' in html


def test_post_settings_persists_toggle_to_tenant_settings(app, monkeypatch):
    _patch_status(monkeypatch, _connected_status())
    with app.test_client() as client:
        _auth_session(client)
        response = client.post(
            "/admin/radius/whatsapp/settings",
            data={
                "_csrf_token": "wa-csrf",
                "send_otp": "1",
                "send_expiry": "1",
                # quota/maintenance/password/portal intentionally omitted → OFF
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        from app.radius.db.repos import tenants_repo

        assert tenants_repo.get_setting(1, "whatsapp.send.otp") == "1"
        assert tenants_repo.get_setting(1, "whatsapp.send.expiry") == "1"
        assert tenants_repo.get_setting(1, "whatsapp.send.quota", "0") == "0"
        assert tenants_repo.get_setting(1, "whatsapp.send.portal", "0") == "0"


def test_post_settings_can_turn_a_toggle_back_off(app, monkeypatch):
    _patch_status(monkeypatch, _connected_status())
    with app.app_context():
        from app.radius.db.repos import tenants_repo

        tenants_repo.set_setting(1, "whatsapp.send.otp", "1")

    with app.test_client() as client:
        _auth_session(client)
        # Submit the form with NO checkboxes → every gate goes OFF.
        client.post(
            "/admin/radius/whatsapp/settings",
            data={"_csrf_token": "wa-csrf"},
            follow_redirects=True,
        )

    with app.app_context():
        from app.radius.db.repos import tenants_repo

        assert tenants_repo.get_setting(1, "whatsapp.send.otp", "0") == "0"


def test_whatsapp_test_routes_through_bridge_only(app, monkeypatch):
    _patch_status(monkeypatch, _connected_status())
    calls = []

    from app.radius.services.admin_panel_client import AdminPanelClient

    def _fake_test(self, recipient_phone, idempotency_key):
        calls.append({"phone": recipient_phone, "key": idempotency_key})
        return {"ok": True, "status": "sent"}

    monkeypatch.setattr(AdminPanelClient, "send_whatsapp_test", _fake_test)

    with app.test_client() as client:
        _auth_session(client)
        response = client.post(
            "/admin/radius/whatsapp/test",
            data={"_csrf_token": "wa-csrf", "recipient_phone": "962790007788"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert len(calls) == 1
    assert calls[0]["phone"] == "962790007788"
    assert calls[0]["key"]  # a stable idempotency key was supplied
