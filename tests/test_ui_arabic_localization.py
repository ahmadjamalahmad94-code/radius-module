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
        "/admin/radius/operations",
        "/admin/radius/operations/speed-control",
        "/admin/radius/communications/channels",
        "/admin/radius/alerts",
    ]
    forbidden = [
        "لم يُربط بعد",
        "تحديث قادم",
        "Sprint القادم",
        "تشغيل لاحق",
        "المراحل القادمة",
        "مرحلة قادمة",
        "UI-only stub",
        "قيد التطوير",
        "صحة NAS/RADIUS",
        "أجهزة NAS",
        "NAS المفعلة",
        "لا توجد جلسات نشطة في radacct",
        "مسار قاعدة RADIUS",
        "ترافيك عالٍ",
        "emergency-placeholders",
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
        Path("app/templates/radius/operations_center.html"),
        Path("app/templates/radius/operations_speed_control.html"),
        Path("app/templates/radius/mt_alerts_index.html"),
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
        "قيد التطوير",
        "صحة NAS/RADIUS",
        "أجهزة NAS",
        "NAS المفعلة",
        "لا توجد جلسات نشطة في radacct",
        "مسار قاعدة RADIUS",
        "ترافيك عالٍ",
        "emergency-placeholders",
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
        Path("app/templates/radius/devices_form.html"),
        Path("app/templates/radius/devices_list.html"),
        Path("app/templates/radius/mt_operations.html"),
        Path("app/templates/radius/mt_setup_form.html"),
        Path("app/templates/radius/mt_setup_script.html"),
        Path("app/templates/radius/sync_list.html"),
        Path("app/templates/admin/_admin_layout.html"),
        Path("app/templates/admin/_sidebar.html"),
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
        "Devices (NAS) form",
        "NAS devices list",
        "{% block title %}NAS{% endblock %}",
        "{% block page_title %}NAS{% endblock %}",
        "NAS — أجهزة شبكة RADIUS",
        "إعدادات MikroTik / NAS",
        "عنوان IP",
        "التحقق من رسالة RADIUS",
        "منفذ MikroTik API",
        "منفذ SSH",
        "API / SNMP",
        "وصول MikroTik و SNMP",
        "اسم مستخدم MikroTik",
        "كلمة مرور MikroTik",
        "أجهزة MikroTik",
        "إدارة MikroTik",
        "لا توجد اتصالات MikroTik",
        "للحصول على JSON",
        "واجهة MikroTik",
        "جهاز MikroTik",
        "في MikroTik",
        "غرفة عمليات MikroTik",
        "طابور المزامنة إلى MikroTik",
        "معالج إضافة راوتر MikroTik",
        "دفع DHCP",
        "إعادة تخصيص IPs",
        "RouterOS v6",
        "نسخة RouterOS",
        "RouterOS {{ v }}.x",
        "RouterOS {{ ros_version }}.x",
        "منفذ RouterOS",
        "Default Route",
        "PPTP —",
        "L2TP/IPsec",
        "SSTP",
        "Terminal",
        "WinBox",
        "dashboard الراوتر",
        "RADIUS secret",
        "كلمة سرّ API",
        "JSON.stringify(data.reset",
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


def test_setup_wizard_outputs_do_not_render_raw_json():
    content = Path("app/static/js/setup_wizard.js").read_text(encoding="utf-8")
    forbidden = [
        "outputPreview.textContent = JSON.stringify",
        "pilotOutput.textContent = JSON.stringify",
    ]

    for token in forbidden:
        assert token not in content, f"{token!r} remained in setup_wizard.js"


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


def test_mikrotik_dashboard_does_not_render_raw_operator_json():
    template = Path("app/templates/radius/mt_dashboard.html").read_text(
        encoding="utf-8"
    )
    script = Path("app/static/js/mt_dashboard.js").read_text(encoding="utf-8")
    css = Path("app/static/css/mt_dashboard.css").read_text(encoding="utf-8")
    forbidden = [
        "data-mt-action-output",
        "data-mt-action-raw-wrap",
        "JSON.stringify(payload",
        "JSON.stringify(ev",
        "mt-health-evidence pre",
        "عرض الاستجابة الخام",
    ]

    combined = "\n".join([template, script, css])
    for token in forbidden:
        assert token not in combined, f"{token!r} remained in MT dashboard UI"


def test_setup_wizard_v3_operator_copy_is_arabic():
    template = Path("app/templates/radius/setup_wizard_v3.html").read_text(
        encoding="utf-8"
    )
    script = Path("app/static/js/setup_wizard_v3.js").read_text(
        encoding="utf-8"
    )
    forbidden_template = [
        "<strong>Hotspot",
        "<strong>PPPoE",
        "<strong>Mixed",
        "<strong>VLAN",
        "<strong>Broadband",
        "<strong>Walled Garden",
        "<label>VLAN ID</label>",
        "توليد سكربت Hotspot",
        "توليد سكربت Broadband",
        "منافذ Hotspot",
        "منافذ PPPoE",
        "IPs التي",
    ]
    forbidden_script = [
        "اختر على الأقل منفذاً واحداً للـ Hotspot.",
        "تعذّر توليد سكربت Hotspot.",
        "سكربت Hotspot جاهز",
        "إعدادات Hotspot",
        "اختر على الأقل منفذاً واحداً لـ PPPoE.",
        "تعذّر توليد سكربت Broadband.",
        "سكربت Broadband جاهز",
        "إعدادات PPPoE",
        "state=BLOCKED",
        "state=${run.v3_state}",
        "ابدأ run",
    ]

    for token in forbidden_template:
        assert token not in template, f"{token!r} remained in v3 template"
    for token in forbidden_script:
        assert token not in script, f"{token!r} remained in v3 script"


def test_core_operations_do_not_show_old_service_english_labels():
    files = {
        Path("app/templates/radius/finance_billing.html"): [
            '<option value="PPPoE">PPPoE',
        ],
        Path("app/templates/radius/invoices_form.html"): [
            '<option value="Hotspot">Hotspot',
            '<option value="PPPoE">PPPoE',
        ],
        Path("app/templates/radius/mt_audit_timeline.html"): [
            "لا الـ JSON",
            "الراو (JSON)",
        ],
        Path("app/templates/radius/mt_dashboard.html"): [
            "جلسات Hotspot",
            "جلسات Hotspot و PPP",
            "بطاقات Hotspot",
            "يوزرات Hotspot",
            "اشتراكات PPPoE",
            "Hotspot —",
            "برمجة Hotspot",
            "برمجة Broadband",
            "اختبار Ping",
            "Traceroute",
            ">Hotspot<",
            ">PPP<",
        ],
        Path("app/templates/radius/svc_partials/open-sites.html"): [
            "لتسجيل الدخول في Hotspot",
            "<strong>Hotspot مفعّلاً</strong>",
            "برمجة Hotspot",
            "Walled Garden",
            "اكتب domain",
        ],
        Path("app/templates/radius/svc_partials/remote-access.html"): [
            "<strong>SSH</strong>",
            "<strong>WebFig</strong>",
            "من IP محدّد",
            "Winbox (port",
            "SSH (port",
            "WebFig (port",
            "API (port",
            "IP المصدر",
            "أي IP",
            "RouterOS مؤقّت",
            "بدون system/users",
            "Winbox/SSH/WebFig",
            "صلاحيّات admin",
            "عنوان VPS",
            "عنوان شبكة VPN الداخلي",
            "عنوان VPN:",
        ],
        Path("app/templates/radius/svc_partials/hotspot.html"): [
            "{# Hotspot service",
            "<h2>تهيئة Hotspot</h2>",
            "بوابة Hotspot",
            "already-active Hotspot",
            "[Hotspot]",
            "script that was sent",
        ],
        Path("app/templates/radius/svc_partials/broadband.html"): [
            "{# Broadband service",
            "Mirrors the Hotspot",
            "<h2>برمجة Broadband</h2>",
            "PPPoE —",
            "واجهات الـ PPPoE",
            "خدمة PPPoE",
            "[Broadband]",
            "script that was sent",
        ],
        Path("app/templates/radius/users_form.html"): [
            ">PPPoE<",
            "برود باند (PPPoE)",
            "NAS-IP-Address",
            "سياسة MikroTik أو RADIUS",
            "سلسلة فلترة MikroTik",
            "قائمة عناوين MikroTik",
            "مسار MikroTik",
            "مجموعة مستخدم MikroTik",
            "مجموعة Winbox",
            "Password input",
            "Service-type cards",
        ],
    }

    for path, forbidden in files.items():
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{token!r} remained in {path}"


def test_card_and_session_pages_hide_raw_network_labels():
    files = {
        Path("app/templates/radius/cards_checker.html"): [
            "من سجلات RADIUS المحفوظة",
            "عدد NAS",
            "آخر IP / NAS",
            "<th>NAS</th>",
            "يرسل أمر فصل إلى RADIUS",
            "سجلات RADIUS السابقة",
            "أرسله NAS داخل حقول الاتصال",
        ],
        Path("app/templates/radius/cards_checker_v2.html"): [
            'class="cc-info-label">NAS',
            "<dt><i class=\"fa-solid fa-server\"></i> NAS</dt>",
            "تحديث الـ MikroTik فوراً عبر CoA",
        ],
        Path("app/templates/radius/cards_of_batch.html"): [
            'data-label="IP / NAS"',
            ">IP / NAS</th>",
        ],
        Path("app/templates/radius/users_list.html"): [
            "بحث (اسم/يوزر/جوال)",
            ">IP / NAS</th>",
            'mac:"MAC", ip:"IP / NAS"',
        ],
        Path("app/templates/radius/sessions_list.html"): [
            "RADIUS_MODE=mikrotik",
            '("nas", "NAS")',
            'data-col="nas">NAS',
            "مزوّد RADIUS يعمل بوضع mikrotik",
        ],
        Path("app/templates/radius/users_profile.html"): [
            "<th>NAS</th>",
        ],
    }

    for path, forbidden in files.items():
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{token!r} remained in {path}"


def test_policy_and_communications_pages_use_arabic_operator_copy():
    files = {
        Path("app/templates/radius/tool_set_speeds.html"): [
            "MikroTik routers",
            "sync_queue",
        ],
        Path("app/templates/radius/_network_policy_shell.html"): [
            "MikroTik من هذه الصفحات",
        ],
        Path("app/templates/radius/network_policy_form.html"): [
            "SSH (TCP 22)",
            "API (TCP 8728)",
            "API SSL (TCP 8729)",
            "اسم MikroTik hotspot profile",
            "بصيغة MikroTik",
            "لا تتّصل بالـ MikroTik",
        ],
        Path("app/templates/radius/login.html"): [
            "تحكّم MikroTik والشبكة",
        ],
        Path("app/templates/radius/site_exit.html"): [
            "قائمة عناوين MikroTik",
            "API الراوتر",
            "لا تحتاج SSH",
        ],
        Path("app/templates/radius/network_policy_preview.html"): [
            "سكربت MikroTik",
            "لا تتّصل بالـ MikroTik",
        ],
        Path("app/templates/radius/communications_channels.html"): [
            "رابط الإرسال (API)",
        ],
        Path("app/templates/radius/communications_guide.html"): [
            "مزوّد واتساب API",
            "رابط إرسال (API)",
            "API URL",
            "رابط الـ API",
            "Send Message API",
            "UltraMsg / AdvWhats / غيره",
        ],
        Path("app/templates/admin/_sidebar.html"): [
            "قيد التطوير",
        ],
    }

    for path, forbidden in files.items():
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{token!r} remained in {path}"


def test_router_operator_pages_hide_raw_vendor_copy():
    files = {
        Path("app/templates/radius/mt_diagnostics.html"): [
            "فحص TCP",
            "دخول API",
            "لكل MikroTik",
            "Firewall",
            "API user",
            "TCP فاشل",
            "API فاشل",
            "إعدادات MikroTik",
            "أجهزة NAS",
            "على MikroTik عبر",
            "حساب API على MikroTik",
        ],
        Path("app/templates/radius/mt_list.html"): [
            "اتصالات MikroTik",
            "لا اتصالات MikroTik",
            "يستخدم TLS",
            'hub.pill("TLS"',
        ],
        Path("app/templates/radius/mt_form.html"): [
            "إضافة اتصال MikroTik",
            "اتصال MikroTik",
            "التشغيل و TLS",
            "تفعيل TLS",
            "بدون TLS",
        ],
        Path("app/templates/radius/mt_legacy_gone.html"): [
            "غرفة عمليات MikroTik",
            "K9 dashboard",
            "host / port / user / password / secret / vendor",
            "WireGuard tunnel + API user + RADIUS secret + سكربت RouterOS",
        ],
        Path("app/templates/radius/mt_loop_setup.html"): [
            "سكربت MikroTik",
        ],
        Path("app/templates/radius/mt_push_setup.html"): [
            "إعداد دفع DHCP من المايكروتيك",
            "من MikroTik",
            "الـ API",
            "MT API",
            "port forwarding",
            "mikrotik-push",
            "hash",
            "Winbox أو WebFig",
            "Terminal",
            "سكربت MikroTik",
            "إصلاح API",
        ],
        Path("app/templates/radius/mt_dashboard.html"): [
            "NAS #{{ nas.id }}",
            ">RouterOS<",
        ],
        Path("app/templates/radius/mt_backups.html"): [
            "NAS #{{ nas.id }}",
        ],
        Path("app/templates/radius/mt_audit_timeline.html"): [
            "NAS #{{ nas.id }}",
        ],
    }

    for path, forbidden in files.items():
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{token!r} remained in {path}"


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
