from __future__ import annotations

import html
import os
import re
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ui_ar_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    yield create_app()

    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"ui_ar_{uuid4().hex[:10]}"
    password = "ui-ar-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Arabic UI Tester",
        is_super_admin=True,
    )
    response = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def _visible_text(markup: str) -> str:
    markup = re.sub(r"<script\b[^>]*>.*?</script>", " ", markup, flags=re.I | re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", " ", markup, flags=re.I | re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def test_core_admin_pages_use_arabic_visible_copy(client):
    _web_login(client)
    routes = [
        "/admin/radius/payments/settings",
        "/admin/radius/payments/requests",
        "/admin/radius/payments/reconciliation",
        "/admin/radius/operations",
        "/admin/radius/operations/speed-control",
        "/admin/radius/business-operators",
        "/admin/radius/communications/campaigns",
        "/admin/radius/events",
        "/admin/radius/events/security",
        "/admin/radius/reports",
        "/admin/radius/reports/login_states",
        "/admin/radius/setup-wizard-v2",
    ]
    forbidden = [
        "Payment Collection Center",
        "Payment Requests",
        "Payment Reconciliation",
        "Review Queue",
        "Manual Wallet",
        "Business Operators",
        "Managers & Distributors",
        "Operations Center",
        "Speed Control Center",
        "Reports Center",
        "Campaigns",
        "Security Events",
        "Events Center",
        "Save",
        "Filter",
        "Settings",
        "Status",
        "API",
        "NAS:",
        "MAC:",
    ]

    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        text = _visible_text(response.get_data(as_text=True))
        for token in forbidden:
            assert token not in text, f"{token!r} leaked in {route}"


def test_localization_glossary_documents_allowed_technical_terms():
    glossary = Path("app/radius/docs/ARABIC_UI_GLOSSARY.md").read_text(encoding="utf-8")
    assert "لوحة التحكم" in glossary
    assert "تجربة جافة" in glossary
    assert "`RADIUS`" in glossary
    assert "`MikroTik`" in glossary


def test_network_devices_page_uses_actionable_monitoring_copy(client):
    _web_login(client)
    response = client.get("/admin/radius/network/devices")
    assert response.status_code == 200
    text = _visible_text(response.get_data(as_text=True))
    assert "قريبًا" not in text
    assert "إعدادات المراقبة" in text
