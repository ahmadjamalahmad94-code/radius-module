"""P7 — radius-module WhatsApp status passthrough + manage deep-link.

The thin client renders the panel's secret-free status (connected / not
connected / needs setup) from the signed bridge call, exposes an
"إدارة ربط واتساب" deep-link to the license-panel WhatsApp pane, and never
renders or stores any Meta secret. ``get_whatsapp_status`` is monkeypatched so
no bridge/network call is made.
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "whatsapp_p7.db")
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
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "wa-csrf"


def _patch_status(monkeypatch, value):
    from app.radius.services.admin_panel_client import AdminPanelClient
    monkeypatch.setattr(AdminPanelClient, "get_whatsapp_status", lambda self: value)


def _panel_status(onboarding: str, *, account_status: str | None = None, **extra) -> dict:
    """A status payload in the REAL panel shape (account_status / onboarding_state)."""
    resp = {
        "enabled": True,
        "account_status": account_status or ("connected" if onboarding == "connected" else "disconnected"),
        "onboarding_state": onboarding,
        "embedded_available": True,
        "display_phone_number": "+970599123456",
        "business_display_name": "Acme ISP",
        "usage": {"daily": {"used": 3, "limit": 100}},
    }
    resp.update(extra)
    return {"ok": True, "status": "connected", "response": resp}


def _html(app, monkeypatch, status):
    _patch_status(monkeypatch, status)
    with app.test_client() as client:
        _auth_session(client)
        return client.get("/admin/radius/whatsapp").get_data(as_text=True)


# ───────────────────────── status passthrough (3 states) ─────────────────────────

def test_status_connected_real_panel_shape(app, monkeypatch):
    html = _html(app, monkeypatch, _panel_status("connected"))
    assert 'data-onboarding="connected"' in html
    assert "متصل" in html
    assert "+970599123456" in html
    assert "Acme ISP" in html


def test_status_not_connected(app, monkeypatch):
    html = _html(app, monkeypatch, _panel_status("not_connected"))
    assert 'data-onboarding="not_connected"' in html
    assert "غير متصل" in html
    assert "أعد ربط واتساب" in html


def test_status_needs_setup(app, monkeypatch):
    html = _html(app, monkeypatch, _panel_status("needs_setup"))
    assert 'data-onboarding="needs_setup"' in html
    assert "بحاجة إلى الإعداد" in html
    assert "لم يتم ربط واتساب الرسمي بعد" in html


def test_status_derives_state_from_account_status_without_onboarding_field(app, monkeypatch):
    # Older panel builds may omit onboarding_state — the client derives it.
    status = {"ok": True, "status": "connected",
              "response": {"enabled": True, "account_status": "connected",
                           "display_phone_number": "+970599123456"}}
    html = _html(app, monkeypatch, status)
    assert 'data-onboarding="connected"' in html
    assert "متصل" in html


# ───────────────────────── manage deep-link ─────────────────────────

def test_manage_link_deeplinks_to_panel_whatsapp_pane(app, monkeypatch):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "license_admin_bridge.base_url", "https://license-panel.test")

    html = _html(app, monkeypatch, _panel_status("needs_setup"))
    assert 'data-testid="wa-manage-link"' in html
    assert 'href="https://license-panel.test/portal/whatsapp"' in html
    assert "إدارة ربط واتساب" in html


# ───────────────────────── no secrets ─────────────────────────

def test_status_passthrough_never_renders_a_token(app, monkeypatch):
    # Even if a token-like field somehow appeared in the payload, the thin client
    # renders only the safe fields (phone / business / usage / state).
    leaky = _panel_status("connected", access_token="EAABsecretLEAK123", app_secret="sshh")
    html = _html(app, monkeypatch, leaky)
    lower = html.lower()
    assert "eaabsecretleak123" not in lower
    assert "sshh" not in lower
    assert "access_token" not in lower
    assert "app_secret" not in lower
    # And no Meta endpoint / token input is present on the page.
    assert "facebook.com" not in lower
    for forbidden in ('name="access_token"', 'name="token"', 'name="app_secret"',
                      'name="waba_id"', 'name="phone_number_id"'):
        assert forbidden not in lower
