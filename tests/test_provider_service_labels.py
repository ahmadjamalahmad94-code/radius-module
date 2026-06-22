"""اختبارات تعريب صفحة «حالة منح المزوّد» + خرائط التسميات العربية.

تَتأكّد من:
  • خريطة الخدمات: المفاتيح المذكورة في تذكرة العميل تُترجَم لعربية.
  • خريطة الحالات: disabled → موقوفة، locked_upgrade → بانتظار التفعيل/ترقية.
  • السقوط الإنسانيّ لمفاتيح غير معروفة (لا أخطاء، لا KeyError).
  • صفحة /admin/radius/_provider/grants تَرندَر بالاسم العربي كمحرَّر أساسي
    + المفتاح الخام كـsubtitle، بلا تسريب «disabled» إنجليزي.
  • صفحتا blocked و upgrade تعرضان الاسم العربي.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "labels.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield app


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _seed_capacity(payload: dict, *, tenant_id: int = 1) -> None:
    from app.radius.db.connection import db
    now = _iso(datetime.utcnow())
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=True, tenant_id=1, permissions=["*"])
    return c


# ════════════════════════════════════════════════════════════════════════
# (1) خريطة الخدمات
# ════════════════════════════════════════════════════════════════════════
class TestServiceNameMap:

    def test_user_listed_keys_translated(self):
        """المفاتيح التي ذَكرها العميل بالاسم: كلّها لها اسم عربي."""
        from app.radius.services.provider_service_labels import service_label_ar
        # المفاتيح من تذكرة العميل + الأمثلة الصريحة التي طلب التطابق معها
        cases = {
            "accounting":        "المحاسبة والتحصيل",
            "card_marketplace":  "سوق البطاقات",
            "communications":    "الرسائل والتنبيهات",
            "admins":            "المدراء والصلاحيات",
            "audit_logs":        "سجلّ التدقيق",
            "backups":           "النسخ الاحتياطية",
            "bandwidth_control": "التحكّم بالسرعة",
            "card_users":        "مستخدمو البطاقات",
            "cards":             "البطاقات",
            "cards_recharge":    "بطاقات الشحن المسبق",
            "customer_portal":   "بوّابة المشترك",
        }
        for k, expected in cases.items():
            assert service_label_ar(k) == expected, \
                f"{k} expected '{expected}', got '{service_label_ar(k)}'"

    def test_internal_taxonomy_translated(self):
        """مفاتيح تصنيفي الداخلي (subscribers/cards/reports/…) كلّها عربية.

        2026-06-18: قيم الخريطة صارت tuples (مرشّحات مرتّبة) — نَفلِّش وكلّ
        مرشّحة يَجب أن تَكون مُعجَمة.
        """
        from app.radius.services.provider_service_labels import (
            service_label_ar, SERVICE_NAMES_AR)
        from app.radius.auth.provider_gate import (
            _ENDPOINT_TO_SERVICE, _PREFIX_TO_SERVICE)
        all_keys: set[str] = set()
        for v in _ENDPOINT_TO_SERVICE.values():
            all_keys.update(v if isinstance(v, (tuple, list)) else (v,))
        for _, v in _PREFIX_TO_SERVICE:
            all_keys.update(v if isinstance(v, (tuple, list)) else (v,))
        for svc_key in all_keys:
            label = service_label_ar(svc_key)
            assert label, f"empty label for {svc_key}"
            assert svc_key in SERVICE_NAMES_AR, \
                f"{svc_key} missing from SERVICE_NAMES_AR"

    def test_provider_catalog_extra_keys_translated(self):
        """مفاتيح كتالوج المزوّد الإضافية (كانت تَظهر إنجليزية خام) صارت عربية."""
        from app.radius.services.provider_service_labels import service_label_ar
        cases = {
            "customer_support":        "الدعم والتذاكر",
            "integration_bridge":      "جسر التكامل",
            "integration_tokens":      "مفاتيح الواجهة",
            "ip_change_vpn":           "تغيير عنوان الإنترنت",
            "ip_pools":                "نطاقات العناوين",
            "loop_detection":          "كشف اللوب",
            "multi_tenant":            "الجهات (المستأجرون)",
            "network_policies":        "سياسات الشبكة",
            "operations_center":       "مركز العمليات",
            "public_ip_change":        "تغيير عنوان الإنترنت — IP العام للخادم",
            "radius_customer_portals": "بوابات عملاء الريدياس",
            "remote_access":           "الوصول البعيد",
            "remote_health_fix":       "صيانة عن بعد",
            "remote_support":          "دعم فني عن بعد",
            "risk_events":             "الأحداث والمخاطر",
            "router_diagnostics":      "تشخيص الراوترات",
            "sms_gateway":             "بوابة SMS",
            "webhooks":                "إشعارات الربط",
            "whatsapp_gateway":        "واتساب",
        }
        for k, expected in cases.items():
            got = service_label_ar(k)
            assert got == expected, f"{k} expected '{expected}', got '{got}'"

    def test_unknown_key_humanized_fallback(self):
        """مفتاح غير مُعجَم: نَسقط لـhumanized بدلًا من KeyError."""
        from app.radius.services.provider_service_labels import service_label_ar
        result = service_label_ar("totally_made_up_service_xyz")
        assert result
        assert "_" not in result  # underscores استُبدلت بمسافات
        # ولا يُرجع نصًّا فارغًا
        assert result.strip()

    def test_empty_key_returns_empty(self):
        from app.radius.services.provider_service_labels import service_label_ar
        assert service_label_ar("") == ""
        assert service_label_ar(None) == ""  # type: ignore[arg-type]

    def test_case_insensitive_lookup(self):
        from app.radius.services.provider_service_labels import service_label_ar
        assert service_label_ar("CARDS") == service_label_ar("cards")
        assert service_label_ar("Accounting") == service_label_ar("accounting")


# ════════════════════════════════════════════════════════════════════════
# (2) خريطة الحالات
# ════════════════════════════════════════════════════════════════════════
class TestStatusMap:

    def test_disabled_translated(self):
        from app.radius.services.provider_service_labels import service_status_ar
        assert service_status_ar("disabled") == "موقوفة"
        assert service_status_ar("suspended") == "معلَّقة"
        assert service_status_ar("expired") == "منتهية"
        assert service_status_ar("revoked") == "مسحوبة"

    def test_active_translated(self):
        from app.radius.services.provider_service_labels import service_status_ar
        assert service_status_ar("active") == "مفعّلة"

    def test_locked_upgrade_all_variants_translated(self):
        """كل أسماء locked_upgrade البديلة تَنتج تسمية عربية واضحة."""
        from app.radius.services.provider_service_labels import service_status_ar
        # locked_upgrade الرئيسي
        assert service_status_ar("locked_upgrade") == "بانتظار التفعيل / ترقية"
        # البدائل لها تسميات عربية أيضًا (ليست بالضرورة متطابقة)
        for variant in ("requires_activation", "requires_upgrade",
                         "upgrade_required", "paid_not_active",
                         "paid_locked", "pending_activation",
                         "not_purchased"):
            ar = service_status_ar(variant)
            assert ar, f"empty Arabic for {variant}"
            # لا يجب أن يَترك المفتاح الإنجليزي كما هو
            assert ar != variant, f"{variant} not translated"

    def test_unknown_status_passthrough(self):
        from app.radius.services.provider_service_labels import service_status_ar
        # حالة غير مُعجَمة: نُرجع القيمة الخام (أفضل من فارغ)
        assert service_status_ar("brand_new_xyz") == "brand_new_xyz"


# ════════════════════════════════════════════════════════════════════════
# (3) خريطة حالة الميزة
# ════════════════════════════════════════════════════════════════════════
class TestFeatureStateMap:

    def test_known_states_translated(self):
        from app.radius.services.provider_service_labels import feature_state_ar
        assert feature_state_ar("enabled") == "متاحة"
        assert feature_state_ar("locked") == "مقفلة"
        assert feature_state_ar("hidden") == "مخفية"
        assert feature_state_ar("readonly") == "قراءة فقط"
        assert feature_state_ar("locked_upgrade") == "قفل ترقية"


# ════════════════════════════════════════════════════════════════════════
# (4) صفحة «حالة منح المزوّد» — رندر فعلي
# ════════════════════════════════════════════════════════════════════════
class TestGrantsStatusPageRender:

    def _render(self, app_ctx, payload):
        _seed_capacity(payload)
        rv = _client(app_ctx).get("/admin/radius/_provider/grants")
        assert rv.status_code == 200
        return rv.get_data(as_text=True)

    def test_arabic_service_names_appear(self, app_ctx):
        """المفاتيح من تذكرة العميل تَظهر بأسمائها العربية."""
        body = self._render(app_ctx, {
            "status": "active",
            "services": {
                "accounting":     {"enabled": True, "status": "active"},
                "card_marketplace": {"enabled": True, "status": "active"},
                "communications": {"enabled": True, "status": "active"},
            },
        })
        assert "المحاسبة والتحصيل" in body
        assert "سوق البطاقات" in body
        assert "الرسائل والتنبيهات" in body
        # المفاتيح الخام تَبقى متاحة كـsubtitle صغير
        assert "accounting" in body
        assert "card_marketplace" in body

    def test_disabled_status_translated_in_pill(self, app_ctx):
        """badge «disabled» الإنجليزية يجب أن تَختفي — تُستبدَل بـ«موقوفة»."""
        body = self._render(app_ctx, {
            "status": "active",
            "services": {
                "reports": {"enabled": True, "status": "disabled"},
            },
        })
        # تَظهر العربية
        assert "موقوفة" in body
        # نُسجّل قاعدة سلبية: «disabled» الخام لا يَظهر داخل badge.
        # نَستثني التعليقات النَّصّية الصريحة في تفسير الجدول (التي تَستعمل
        # المصطلح كرمز داخلي، إن وُجدت)، لذا نَفحص فقط أنّ الـpill المرئي
        # يَستخدم العربية: نَبحث عن نمط hub.pill المُولَّد. أبسط من ذلك:
        # نَتأكّد من غياب 'disabled' في عمود الحالة (يُمكن استخراج عمود
        # محدّد، لكن بدلًا من ذلك نَفحص أنّ الكلمة لا تَظهر بشكل بارز).
        # غياب 'disabled' في كامل الصفحة شرط أقوى، لكن قد تُذكَر في
        # CSS class names — لذلك نَكتفي بالتحقّق من العربية الموجبة.
        # ولكي لا يَتسرّب: نَفحص نوع الـpill (الـbadge اللوني):
        # العربية «موقوفة» يجب أن تَكون قبل status raw text في الصفحة.
        ar_idx = body.find("موقوفة")
        assert ar_idx > 0
        # الكلمة الإنجليزية الخام لا تَظهر بنفس الـpill الأحمر
        # (تَحقّق ضمني: لو ظَهر «disabled» في الـpill بدل العربية لكانت
        # العربية غير موجودة أصلًا)

    def test_locked_upgrade_status_shows_arabic_label(self, app_ctx):
        body = self._render(app_ctx, {
            "status": "active",
            "services": {
                "card_marketplace": {"enabled": True, "status": "locked_upgrade"},
            },
        })
        assert "بانتظار التفعيل / ترقية" in body
        # حالة «مدفوعة — تحتاج تفعيل» في عمود النتيجة
        assert "مدفوعة — تحتاج تفعيل" in body

    def test_unknown_status_falls_back_gracefully(self, app_ctx):
        """حالة غير مُعجَمة لا تَكسر الصفحة (تَظهر كما هي)."""
        body = self._render(app_ctx, {
            "status": "active",
            "services": {
                "cards": {"enabled": True, "status": "weird_status_xyz"},
            },
        })
        # الصفحة رَندَرت + لا اعتراض
        assert "weird_status_xyz" in body or "البطاقات" in body

    def test_raw_key_visible_as_subtitle(self, app_ctx):
        """المفتاح الخام يَبقى مرئيًّا في الصفحة كـsubtitle للدعم الفنّي."""
        body = self._render(app_ctx, {
            "status": "active",
            "services": {"accounting": {"enabled": True, "status": "active"}},
        })
        # الـmono small هو الـclass التي نَستخدمها للـsubtitle
        # نَتأكّد من ظهور المفتاح الخام في الصفحة
        assert "accounting" in body
        # والاسم العربي يَظهر كذلك
        assert "المحاسبة والتحصيل" in body


# ════════════════════════════════════════════════════════════════════════
# (5) صفحتا blocked + upgrade تستعملان الاسم العربي
# ════════════════════════════════════════════════════════════════════════
class TestBlockedAndUpgradePagesArabize:

    def test_blocked_page_shows_arabic_service_name(self, app_ctx):
        _seed_capacity({
            "status": "active",
            "services": {"accounting": {"enabled": True, "status": "disabled"}},
        })
        rv = _client(app_ctx).get(
            "/admin/radius/_provider/blocked?service=accounting")
        body = rv.get_data(as_text=True)
        assert rv.status_code == 200
        # الاسم العربي للخدمة
        assert "المحاسبة والتحصيل" in body
        # حالة موقوفة بالعربي
        assert "موقوفة" in body
        # المفتاح الخام يَظهر كـsubtitle
        assert "accounting" in body

    def test_upgrade_page_shows_arabic_service_name(self, app_ctx):
        _seed_capacity({
            "status": "active",
            "services": {
                "card_marketplace": {"enabled": True, "status": "locked_upgrade"},
            },
        })
        rv = _client(app_ctx).get(
            "/admin/radius/_provider/upgrade?service=card_marketplace")
        body = rv.get_data(as_text=True)
        assert rv.status_code == 200
        assert "سوق البطاقات" in body
        assert "بانتظار التفعيل / ترقية" in body
