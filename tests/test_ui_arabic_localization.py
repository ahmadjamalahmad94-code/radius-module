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
        response = client.get(route, follow_redirects=True)
        assert response.status_code == 200, route
        text = _visible_text(response.get_data(as_text=True))
        for token in forbidden:
            assert token not in text, f"{token!r} leaked in {route}"


def test_localization_glossary_documents_allowed_technical_terms():
    glossary = Path("app/radius/docs/ARABIC_UI_GLOSSARY.md").read_text(encoding="utf-8")
    assert "لوحة التحكم" in glossary
    assert "معاينة بدون تنفيذ" in glossary
    assert "`RADIUS`" in glossary
    assert "`MikroTik`" in glossary


def test_network_devices_page_uses_actionable_monitoring_copy(client):
    _web_login(client)
    response = client.get("/admin/radius/network/devices")
    assert response.status_code == 200
    text = _visible_text(response.get_data(as_text=True))
    assert "قريبًا" not in text
    assert "إعدادات المراقبة" in text


def test_operational_pages_do_not_show_future_placeholder_copy(client):
    _web_login(client)
    routes = [
        "/admin/radius/network/devices/new",
        "/admin/radius/operations/speed-control",
        "/admin/radius/communications/channels",
    ]
    forbidden = [
        "لم يُربط بعد",
        "تحديث قادم",
        "Sprint القادم",
        "تشغيل لاحق",
        "المراحل القادمة",
        "مرحلة قادمة",
        "UI-only stub",
    ]

    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        text = _visible_text(response.get_data(as_text=True))
        for token in forbidden:
            assert token not in text, f"{token!r} leaked in {route}"


def test_source_templates_do_not_keep_future_placeholder_copy():
    paths = [
        Path("app/templates/radius/setup_wizard_v3_router_service_flow.html"),
        Path("app/templates/radius/network_devices_form.html"),
        Path("app/templates/radius/operations_speed_control.html"),
        Path("app/templates/radius/communications_channels.html"),
        Path("app/radius/routes/network_policy.py"),
    ]
    forbidden = [
        "لم يُربط بعد",
        "تحديث قادم",
        "Sprint القادم",
        "تشغيل لاحق",
        "المراحل القادمة",
        "مرحلة قادمة",
        "UI-only stub",
    ]

    for path in paths:
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{token!r} remained in {path}"


def test_source_templates_do_not_keep_old_english_operator_labels():
    paths = [
        Path("app/templates/radius/audit_list.html"),
        Path("app/templates/radius/audit_log_detail.html"),
        Path("app/templates/radius/backups.html"),
        Path("app/templates/radius/cards_print_batch.html"),
        Path("app/templates/radius/dashboard.html"),
        Path("app/templates/radius/finance_billing.html"),
        Path("app/templates/radius/invoices_form.html"),
        Path("app/templates/radius/mt_dashboard.html"),
        Path("app/templates/radius/mt_backups.html"),
        Path("app/templates/radius/mt_diagnostics.html"),
        Path("app/templates/radius/mt_router_overview.html"),
        Path("app/templates/radius/setup_wizard.html"),
        Path("app/templates/radius/_status.html"),
        Path("app/templates/radius/users_form.html"),
    ]
    forbidden = [
        "<h3>Payload</h3>",
        ">Google Drive<",
        "Google Drive غير",
        "ربط Google Drive",
        "<th>Username</th>",
        "<th>Password</th>",
        ">Uptime<",
        ">Process<",
        ">Disk<",
        ">Storage<",
        ">Clock<",
        ">Link<",
        ">TCP probe<",
        ">API login<",
        ">identity<",
        ">timeout 20s<",
        "Ping OK",
        'placeholder="Main Router"',
        ">Guarded operations<",
        ">Pilot only<",
        "Generate Pilot Drill Checklist",
        "Pilot drill step",
        "Router backup/export taken",
        "Out-of-band access confirmed",
        "WAN/interface verified",
        "Rollback plan reviewed",
        "Feature flag still OFF unless controlled test",
        "pilot drill checklist will appear here",
        ">Balance<",
        "Primary DNS (PPP)",
        "Secondary DNS (PPP)",
        "صيغة JSON",
        'textarea name="payload_json"',
        'textarea name="blocked_json"',
        '{"interface":"ether1"',
        '{"wg_interface_name":"hr-wg"',
        "{{ r.payload_json }}",
        'title="{{ r.payload_json }}"',
        "{{ w.info|tojson }}",
        "{{ ov.counters | tojson(indent=2) }}",
        "{{ restore_plan | tojson(indent=2) }}",
        "معطّل في هذه المرحلة",
    ]

    for path in paths:
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{token!r} remained in {path}"


def test_mikrotik_operation_forms_do_not_keep_old_english_placeholders():
    content = Path("app/static/js/mt_dashboard.js").read_text(encoding="utf-8")
    forbidden = [
        'placeholder="weekly-1"',
        'placeholder="backup-01"',
        'placeholder="kernel panic"',
        'placeholder="main-gw"',
        'placeholder="rename for clarity"',
        ">شغّل ping<",
        ">شغّل Traceroute<",
        "يُسجَّل في audit",
        "[A-Za-z0-9._-] حتى 32 حرفًا",
        "افتراضي backup-YYYYMMDD-HHMMSS",
        "API token",
        "تعذّر الاتصال بالـ API",
        "قناة API آمنة",
    ]

    for token in forbidden:
        assert token not in content, f"{token!r} remained in mt_dashboard.js"


def test_print_template_preview_does_not_render_raw_json():
    content = Path("app/templates/radius/print_templates.html").read_text(
        encoding="utf-8"
    )
    forbidden = [
        "{{ preview.preview | tojson(indent=2) }}",
        'class="pr-json"',
        "معاينة Backend",
    ]

    for token in forbidden:
        assert token not in content, f"{token!r} remained in print_templates.html"


def test_operational_pages_hide_raw_technical_copy(client):
    _web_login(client)
    routes = [
        "/admin/radius/webhooks",
        "/admin/radius/webhooks/deliveries",
        "/admin/radius/cards/batches/import",
        "/admin/radius/cards/print/new",
        "/admin/radius/mt/setup",
    ]
    forbidden = [
        "Webhook",
        "CSRF failed",
        "فشل CSRF",
        "Internet Card",
        "Hotspot Voucher",
        "Keep login data",
        "Use the username",
        "HTTP 404",
        "RouterOS Terminal",
        "New Terminal",
        "قريباً",
        "لاحقًا",
        "لاحقاً",
    ]

    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        text = _visible_text(response.get_data(as_text=True))
        for token in forbidden:
            assert token not in text, f"{token!r} leaked in {route}"
