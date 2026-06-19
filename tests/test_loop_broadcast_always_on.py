"""feat/loop-broadcast-always-on — اختبارات النموذج «دائم الإتاحة» لخدمَتي
نظافة الشبكة (تتبّع اللوب + منع البث).

السياق (قرار المالك يونيو 2026): الخدمتان لا تَخضعان لحارس عامّ on/off —
نَموذج «شبكة عامّة فقط» يَجعلهما مُلائمتين دومًا. التفعيل = اختيار المداخل
(per-interface) في صفحة الخدمة، لا زرّ «تفعيل» عامّ على البطاقة.

تَغطّي:
  (1) الـbackend: ‎_get_state دائمًا ‎enabled=True لهاتين بغضّ النظر عن
      المخزَّن — أيّ قارئ خادمي (apply/deploy) يَراهما متاحتين فينفّذ.
  (2) UI mt_dashboard: لا pill «تفعيل» ولا pill «ترقية» على بطاقتَي
      ‎bt_wifi_block + loop_detect (انحدار الـUI الذي طَلبه المالك).
  (3) UI port_script_services: زرّ «طلب تفعيل بمواصفات» مَخفيّ
      للخدمتَين (لا حارس مزوّد على هذه الخدمات).
  (4) الـbadge «مفعّلة/غير مفعّلة» في الصفحتين يَنعكس من ‎ports|length
      لا من ‎enabled (= «مَضبوطة على N منفذ» / «بلا مداخل»).

شغّل وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_loopbcast_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"loop_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="pw", full_name="Loop Tester",
        is_super_admin=True,
    )
    rv = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "pw"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def _seed_nas(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, 'pss-rtr', '203.0.113.21', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


# ════════════════════════════════════════════════════════════════════════
# (1) Backend: _get_state دائمًا enabled=True لهاتين
# ════════════════════════════════════════════════════════════════════════
class TestAlwaysAvailableBackend:

    def test_bt_wifi_block_enabled_default_true(self, app):
        """لا توجد قيمة مَخزَّنة → enabled=True (always-available)."""
        with app.app_context():
            from app.radius.routes import port_script_services as route
            st = route._get_state(42, "bt_wifi_block")
        assert st["enabled"] is True
        assert st["ports"] == []

    def test_loop_detect_enabled_default_true(self, app):
        with app.app_context():
            from app.radius.routes import port_script_services as route
            st = route._get_state(42, "loop_detect")
        assert st["enabled"] is True
        assert st["ports"] == []

    def test_explicit_disabled_persist_ignored_on_read(self, app):
        """حتى لو كَتب أحد ‎_set_state(enabled=False)، القراءة تَتجاهله
        وتُرجِع True. هذا الـinvariant يَحمي downstream readers
        (apply/deploy) من «اعتقاد» أنّ الخدمة مُغلقة عَرَضًا."""
        with app.app_context():
            from app.radius.routes import port_script_services as route
            route._set_state(7, "bt_wifi_block", enabled=False, ports=[])
            assert route._get_state(7, "bt_wifi_block")["enabled"] is True
            route._set_state(7, "loop_detect", enabled=False, ports=[])
            assert route._get_state(7, "loop_detect")["enabled"] is True

    def test_ports_round_trip(self, app):
        """الـports = الـsource of truth لـ«ما يُنشر فعلًا». تَخزّن وتُقرأ
        كما هي بدون تأثير على enabled."""
        with app.app_context():
            from app.radius.routes import port_script_services as route
            route._set_state(7, "bt_wifi_block", enabled=True,
                              ports=["ether2", "ether3"])
            st = route._get_state(7, "bt_wifi_block")
        assert st["enabled"] is True
        assert st["ports"] == ["ether2", "ether3"]

    def test_other_slugs_unaffected(self, app):
        """الـslugs غير «دائمة الإتاحة» (لو وُجدت لاحقًا) تَحتفظ بالسلوك
        الافتراضيّ القديم: enabled يَتبع المخزَّن."""
        with app.app_context():
            from app.radius.routes import port_script_services as route
            assert "future_opt_in" not in route._ALWAYS_AVAILABLE_SLUGS
            st = route._get_state(1, "future_opt_in")
            assert st["enabled"] is False  # الافتراضيّ القديم


# ════════════════════════════════════════════════════════════════════════
# (2) UI mt_dashboard: pills «تفعيل/ترقية» غائبتان عن البطاقتين
# ════════════════════════════════════════════════════════════════════════
class TestDashboardCardsNoPills:
    """انحدار الـUI الذي طَلب المالك: لا global activate على البطاقة."""

    def _dashboard_html(self, client, app) -> str:
        _seed_nas(app)
        _login(client)
        rv = client.get("/admin/radius/mt/1/dashboard")
        assert rv.status_code == 200, rv.status_code
        return rv.get_data(as_text=True)

    def test_bt_wifi_card_has_no_activate_pill(self, app, client):
        body = self._dashboard_html(client, app)
        # تَأكّد أنّ البطاقة موجودة
        assert 'data-rh-svc-card="bt_wifi_block"' in body
        # ابحث في نطاق البطاقة (نقطع 1200 حرفًا بعدها) — يَجب ألا يَحوي
        # ssm-pill--activate ولا ssm-pill--upgrade مع data-svc-type=
        # "bt_wifi_block".
        idx = body.find('data-rh-svc-card="bt_wifi_block"')
        snippet = body[idx:idx + 1200]
        assert "ssm-pill--activate" not in snippet, \
            "bt_wifi_block card must NOT have a global activate pill"
        assert "ssm-pill--upgrade" not in snippet, \
            "bt_wifi_block card must NOT have a global upgrade pill"

    def test_loop_detect_card_has_no_activate_pill(self, app, client):
        body = self._dashboard_html(client, app)
        assert 'data-rh-svc-card="loop_detect"' in body
        idx = body.find('data-rh-svc-card="loop_detect"')
        snippet = body[idx:idx + 1200]
        assert "ssm-pill--activate" not in snippet, \
            "loop_detect card must NOT have a global activate pill"
        assert "ssm-pill--upgrade" not in snippet, \
            "loop_detect card must NOT have a global upgrade pill"

    def test_bt_wifi_card_still_links_to_per_interface_form(self, app, client):
        """الـcard لم تَخرج من الـsidebar/الصفحة — لا تَزال رابطًا لصفحة
        الخدمة (التفعيل = اختيار المداخل)."""
        body = self._dashboard_html(client, app)
        # الـhref يَستخدم mt_port_services_form بـslug bt_wifi_block
        assert "/port-services?slug=bt_wifi_block" in body \
            or "port-services/bt_wifi_block" in body \
            or "slug=bt_wifi_block" in body

    def test_loop_detect_card_still_links_to_per_interface_form(self, app, client):
        body = self._dashboard_html(client, app)
        assert "/port-services?slug=loop_detect" in body \
            or "port-services/loop_detect" in body \
            or "slug=loop_detect" in body


# ════════════════════════════════════════════════════════════════════════
# (3) صفحة الخدمة: زرّ «طلب تفعيل بمواصفات» مَخفيّ للخدمتين
# ════════════════════════════════════════════════════════════════════════
class TestServicePageNoSpecRequest:

    def _page(self, app, client, slug: str) -> str:
        _seed_nas(app)
        from app.radius.routes import port_script_services as route
        # نَمنع اكتشاف المنافذ كي لا يَطلب الراوتر الوهمي
        from unittest.mock import patch
        with patch.object(route, "_discover", return_value=[]):
            _login(client)
            rv = client.get(f"/admin/radius/mt/1/port-services?slug={slug}")
        assert rv.status_code == 200
        return rv.get_data(as_text=True)

    def test_bt_wifi_block_page_no_spec_modal_open(self, app, client):
        body = self._page(app, client, "bt_wifi_block")
        # data-svc-type="bt_wifi_block" لا يَجب أن يَظهر في الصفحة كزرّ
        # طلب مواصفات (يُفتح modal)
        assert 'data-svc-type="bt_wifi_block"' not in body
        # ولا نَصّ الزرّ نفسه
        assert "طلب تفعيل بمواصفات" not in body
        assert "طلب ترقية المواصفات" not in body

    def test_loop_detect_page_no_spec_modal_open(self, app, client):
        body = self._page(app, client, "loop_detect")
        assert 'data-svc-type="loop_detect"' not in body
        assert "طلب تفعيل بمواصفات" not in body
        assert "طلب ترقية المواصفات" not in body

    def test_per_interface_form_still_works(self, app, client):
        """صفحة الخدمة لا تَزال تَعرض اختيار المنافذ + معاينة السكربت —
        هذه هي «آلية التفعيل» الجديدة."""
        body = self._page(app, client, "bt_wifi_block")
        assert "معاينة سكربت التفعيل" in body
        assert "معاينة سكربت الإزالة" in body


# ════════════════════════════════════════════════════════════════════════
# (4) Badge «مفعّلة/غير مفعّلة» يَنعكس من ports|length لا من enabled
# ════════════════════════════════════════════════════════════════════════
class TestVisualReflectsPortsNotEnabled:

    def test_no_ports_shows_unconfigured_banner(self, app, client):
        """بلا مداخل → بانر «غير مفعّلة» حتى لو enabled=True في الخادم."""
        _seed_nas(app)
        from app.radius.routes import port_script_services as route
        from unittest.mock import patch
        with patch.object(route, "_discover", return_value=[]):
            _login(client)
            body = client.get(
                "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
            ).get_data(as_text=True)
        # الـenabled في الـbackend دائمًا True لكن الـUI يَعتمد ports
        with app.app_context():
            assert route._get_state(1, "bt_wifi_block")["enabled"] is True
        # ومع ذلك الـbanner يَظهر «غير مفعّلة» (ports=[])
        assert "غير مفعّلة" in body

    def test_with_ports_shows_configured_banner(self, app, client):
        """مَع مَداخل → بانر «مفعّلة حاليًا على ether2, ether3»."""
        _seed_nas(app)
        from app.radius.routes import port_script_services as route
        from unittest.mock import patch
        _login(client)
        with app.test_request_context():
            # نُحاكي تَنزيل الـports عبر API الحفظ
            from flask import g as _g
            _g.tenant_id = 1
            _g.admin_id = 1
            route._set_state(1, "bt_wifi_block", enabled=True,
                              ports=["ether2", "ether3"])
        with patch.object(route, "_discover", return_value=[]):
            body = client.get(
                "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
            ).get_data(as_text=True)
        # «مفعّلة حاليًا على المنافذ: ether2, ether3»
        assert "مفعّلة" in body
        assert "ether2" in body
        assert "ether3" in body
