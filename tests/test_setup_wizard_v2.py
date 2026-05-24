from __future__ import annotations

import os
import secrets

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-v2-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "test.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "qa_admin"
        sess["admin_name"] = "QA Admin"
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "test-csrf"


def test_setup_wizard_v2_route_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard-v2")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "data-setup-wizard-v2" in html
    assert "معالج إعداد HobeRadius" in html
    assert "setup_wizard_v2.css" in html
    assert "setup_wizard_v2.js" in html


def test_setup_wizard_v2_source_cards_render(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "DHCP Client" in html
    assert "PPPoE" in html
    assert "Static IP" in html
    assert "VLAN" in html
    assert 'data-source-type="dhcp"' in html
    assert 'data-source-type="pppoe"' in html


def test_setup_wizard_v2_stepper_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "الترحيب" in html
    assert "مصدر الإنترنت" in html
    assert "سكربت الإنترنت" in html
    assert "تحقق الإنترنت" in html
    assert "ربط VPN/RADIUS" in html


def test_setup_wizard_v2_preserves_engineering_link(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "فتح الوضع الهندسي" in html
    assert "/admin/radius/setup-wizard" in html


def test_setup_wizard_v2_verification_and_script_sections_exist(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert 'data-swv2-verify-output="internet"' in html
    assert 'data-swv2-verify-output="vpn"' in html
    assert 'data-swv2-script-preview="internet"' in html
    assert 'data-swv2-script-preview="vpn"' in html
    assert "تم تجهيز سكربت الإنترنت" in html


def test_existing_engineering_wizard_route_is_untouched(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "setup_wizard.js" in html
    assert "setup_wizard_v2.js" not in html
