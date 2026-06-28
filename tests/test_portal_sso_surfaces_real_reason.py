"""Regression: the «ربط جوجل درايف» / portal-SSO button must surface the
licensing panel's REAL reason, not a dead-end «لم يصل رابط الدخول» banner.

The bridge transport returns ``ok:true`` for any non-network answer — even a
4xx JSON body the panel sent on purpose (no portal user, license inactive).
The route must read the INNER ``response`` and flash the panel's own message.
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "portal_sso_reason.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "portal-sso-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "license-secret-test-value")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "sso_reason"
        sess["admin_name"] = "SSO Reason"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "sso-reason-csrf"


def _flashes(client):
    with client.session_transaction() as sess:
        return [msg for _cat, msg in (sess.get("_flashes") or [])]


def _patch_sso(monkeypatch, value):
    from app.radius.services import admin_panel_client as apc

    monkeypatch.setattr(
        apc.AdminPanelClient, "request_portal_sso", lambda self: value
    )
    # The lifecycle gate redirects an un-activated license to the activate
    # page before our route runs — neutralise it so we exercise the handler.
    from app.radius.services import license_lifecycle as ll

    class _Decision:
        blocks_panel = False
        state = None

    monkeypatch.setattr(ll, "evaluate_cached", lambda tid: _Decision())


def test_inner_panel_message_is_surfaced(app, monkeypatch):
    # Transport OK, but the panel answered 404 no_user with its own message.
    _patch_sso(monkeypatch, {
        "ok": True,
        "status": "no_user",
        "response": {"ok": False, "status": "no_user", "message": "لا يوجد مستخدم عميل نشط."},
    })
    with app.test_client() as client:
        _auth_session(client)
        resp = client.get("/admin/radius/license-file/portal-sso")
        assert resp.status_code == 302
        flashes = _flashes(client)
    assert any("لا يوجد مستخدم عميل نشط" in m for m in flashes), flashes
    # The misleading dead-end banner must NOT be shown.
    assert not any("لم يصل رابط الدخول" in m for m in flashes), flashes


def test_inner_status_without_message_falls_back_to_label(app, monkeypatch):
    # Panel sent a status but no message → use the Arabic status label.
    _patch_sso(monkeypatch, {
        "ok": True,
        "status": "no_user",
        "response": {"ok": False, "status": "no_user"},
    })
    with app.test_client() as client:
        _auth_session(client)
        client.get("/admin/radius/license-file/portal-sso")
        flashes = _flashes(client)
    assert any("مستخدم لبوابة العميل" in m for m in flashes), flashes


def test_valid_sso_url_redirects_to_google_portal(app, monkeypatch):
    _patch_sso(monkeypatch, {
        "ok": True,
        "status": "ok",
        "response": {"ok": True, "sso_url": "https://hoberadius.com/portal/sso?t=abc"},
    })
    with app.test_client() as client:
        _auth_session(client)
        resp = client.get("/admin/radius/license-file/portal-sso")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "https://hoberadius.com/portal/sso?t=abc"


def test_transport_failure_shows_status_label(app, monkeypatch):
    _patch_sso(monkeypatch, {"ok": False, "status": "timeout"})
    with app.test_client() as client:
        _auth_session(client)
        client.get("/admin/radius/license-file/portal-sso")
        flashes = _flashes(client)
    assert any("انتهت مهلة الاتصال" in m for m in flashes), flashes
