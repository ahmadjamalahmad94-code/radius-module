from __future__ import annotations

import os
import secrets

import pytest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-v2-product-" + secrets.token_hex(8)
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


def _template_source() -> str:
    with open(
        os.path.join(ROOT, "app", "templates", "radius", "setup_wizard_v2.html"),
        "r",
        encoding="utf-8",
    ) as fh:
        return fh.read()


def _css_source(filename: str) -> str:
    with open(os.path.join(ROOT, "app", "static", "css", filename), "r", encoding="utf-8") as fh:
        return fh.read()


def _assert_inside_collapsed_advanced(html: str, marker: str):
    marker_index = html.find(marker)
    assert marker_index != -1
    opening = html.rfind("<details", 0, marker_index)
    closing = html.rfind("</details>", 0, marker_index)
    assert opening > closing
    assert 'class="swv2-advanced"' in html[opening : opening + 120]
    assert " open" not in html[opening : html.find(">", opening)]


def test_v2_route_renders_confidence_journey(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    source = _template_source()
    assert "data-swv2-confidence-journey" in html
    assert 'data-confidence-state="started"' in html
    assert 'data-confidence-state="completed"' in html
    assert "بدأنا" in source
    assert "اكتمل الإعداد" in source


def test_advanced_details_are_collapsed_by_default(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert '<details class="swv2-advanced"' in html
    assert '<details class="swv2-advanced" open' not in html
    _assert_inside_collapsed_advanced(html, 'data-swv2-plan-json="internet"')
    _assert_inside_collapsed_advanced(html, 'data-swv2-service-json="hotspot"')
    _assert_inside_collapsed_advanced(html, "data-swv2-added-json")


def test_success_states_have_product_explanations(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    source = _template_source()
    assert 'data-swv2-success="internet"' in html
    assert 'data-swv2-success="vpn"' in html
    assert 'data-swv2-success-card="internet"' in html
    assert 'data-swv2-success-card="vpn"' in html
    assert 'data-swv2-success-card="final"' in html
    assert "ما الذي تحقق؟" in source
    assert "الشبكة جاهزة للتوسع" in source


def test_engineering_view_and_lab_warnings_remain_visible(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    source = _template_source()
    assert "/admin/radius/setup-wizard" in html
    assert "وضع المختبر الداخلي" in source
    assert "data-swv2-server-peer-apply disabled" in html
    assert "data-swv2-server-peer-rollback disabled" in html


def test_mobile_friendly_product_classes_exist():
    css = _css_source("setup_wizard_v2.css")
    fleet_css = _css_source("setup_wizard_fleet.css")

    assert ".swv2-confidence-journey" in css
    assert ".swv2-empty-state" in css
    assert ".swv2-success-card" in css
    assert "@media (max-width: 560px)" in css
    assert ".swv2-btn" in css and "width: 100%" in css
    assert "@media (max-width: 720px)" in fleet_css
    assert ".swfleet-table-wrap" in fleet_css


def test_primary_ui_does_not_expose_raw_json_blocks(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/setup-wizard-v2").get_data(as_text=True)

    assert "payload JSON" not in html
    assert "raw operation list" not in html
    assert "raw inventory" not in html
    for marker in (
        'data-swv2-plan-json="internet"',
        'data-swv2-plan-json="vpn"',
        'data-swv2-service-json="hotspot"',
        'data-swv2-service-json="broadband"',
        "data-swv2-added-json",
        "data-swv2-recovery-json",
    ):
        _assert_inside_collapsed_advanced(html, marker)


def test_unsupported_service_copy_is_honest():
    source = _template_source()

    assert "غير مدعومة في هذا المسار حاليًا" in source
    assert "تحتاج تفعيلًا لاحقًا" in source or "تحتاج تفعيل" in source


def test_existing_engineering_route_still_renders(app):
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/setup-wizard")
        html = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "setup_wizard.js" in html
    assert "setup_wizard_v2.js" not in html
