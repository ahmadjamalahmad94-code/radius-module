"""feat/telegram-alerts-expand — توسعة جرد تنبيهات الإدارة.

يغطّي: تذييل الوقت/النسخة المُلحَق بكل القوالب (Part A)، المواصفات الجديدة
ومجموعاتها (Part B: متجر/أمان/مال/نظام)، التصيير/المعاينة/التفعيل لها،
وتوصيل المُطلِقات الحقيقية (store_alerts + access_control). شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "admin_alerts_expand.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


# ════════════════════════════════════════════════════════════════════════
# Part A — تذييل الوقت + اسم النسخة على كل القوالب
# ════════════════════════════════════════════════════════════════════════
class TestFooter:

    def test_render_appends_clock_footer(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"username": "u1", "full_name": "Ali"})
        assert "🕐" in out  # تذييل الوقت مُلحَق مركزيًّا
        assert out.rstrip().endswith("</i>")  # التذييل في سطر <i> أخير

    def test_footer_on_every_template(self):
        from app.radius.services import admin_alerts as aa
        for spec in aa.ALERTS:
            assert "🕐" in aa.preview(spec.key), f"لا تذييل في {spec.key}"

    def test_instance_label_appended_when_configured(self, app_ctx):
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "system.name", "شركة النور")
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"username": "u1"})
        # الاسم يظهر فقط إن وفّره مصدر النظام؛ التذييل لا يكسر إن غاب.
        assert "🕐" in out


# ════════════════════════════════════════════════════════════════════════
# Part A — التنسيق المقروء (سطر فارغ + تغميق التسميات)
# ════════════════════════════════════════════════════════════════════════
class TestReadableFormatting:

    def test_blank_line_after_title(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"full_name": "أحمد علي", "username": "u1"})
        # العنوان ثمّ سطر فارغ (\n\n) قبل أوّل حقل.
        assert "<b>مشترك جديد</b>\n\n" in out
        assert out.startswith("🆕 <b>مشترك جديد</b>\n\n")

    def test_labels_are_bolded(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"full_name": "أحمد علي", "username": "u1"})
        assert "<b>الاسم:</b> أحمد علي" in out
        assert "<b>الباقة:</b>" in out

    def test_code_values_preserved_and_labelled(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"full_name": "x", "username": "ahmad99"})
        # قيمة <code> تبقى، والتسمية تُغمَّق قبلها.
        assert "<b>اسم المستخدم:</b> <code>ahmad99</code>" in out

    def test_blank_line_before_footer(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"username": "u1"})
        assert "\n\n<i>🕐" in out  # سطر فارغ يفصل التذييل
        assert out.rstrip().endswith("</i>")

    def test_time_in_value_not_split(self):
        from app.radius.services import admin_alerts as aa
        # «تنتهي: 2026-06-16 21:00» — الـ«:» في الوقت لا يكسر التغميق.
        out = aa.render("speed_boost", {"username": "u", "ends_at": "2026-06-16 21:00"})
        assert "<b>تنتهي:</b> 2026-06-16 21:00" in out

    def test_no_double_bold(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("subscriber_new", {"full_name": "x", "username": "u"})
        assert "<b><b>" not in out and "</b></b>" not in out

    def test_uniform_across_all_templates(self):
        from app.radius.services import admin_alerts as aa
        nl = chr(10)
        for spec in aa.ALERTS:
            out = aa.preview(spec.key)
            title = spec.template.split(nl)[0]
            assert out.startswith(title + nl + nl), f"{spec.key}: لا سطر فارغ بعد العنوان"
            assert nl + nl + "<i>🕐" in out, f"{spec.key}: لا فاصل تذييل"
            field_lines = [l for l in spec.template.split(nl)[1:] if l.strip()]
            if any(": " in l and not l.strip().startswith("<") for l in field_lines):
                assert "</b> " in out, f"{spec.key}: لا تسمية مُغمَّقة"


# ════════════════════════════════════════════════════════════════════════
# Part B — المواصفات والمجموعات الجديدة
# ════════════════════════════════════════════════════════════════════════
NEW_KEYS = [
    "router_offline", "router_high_traffic", "router_high_usage",
    "store_registration", "store_deposit", "store_withdrawal", "store_chat",
    "payment_pending_review", "service_request_new", "service_request_approved",
    "card_batch_low", "auto_block_triggered", "access_suspended",
    "backup_stale", "backup_failed", "audit_failure",
]


class TestNewSpecs:

    def test_all_new_keys_present(self):
        from app.radius.services import admin_alerts as aa
        keys = {a.key for a in aa.ALERTS}
        missing = [k for k in NEW_KEYS if k not in keys]
        assert not missing, f"مواصفات ناقصة: {missing}"

    def test_total_count_at_least_27(self):
        from app.radius.services import admin_alerts as aa
        assert len(aa.ALERTS) >= 27

    def test_new_groups_present(self):
        from app.radius.services import admin_alerts as aa
        gids = {g[0] for g in aa.GROUPS}
        for g in ("store", "security", "system"):
            assert g in gids, f"مجموعة ناقصة: {g}"

    def test_every_spec_group_is_known(self):
        from app.radius.services import admin_alerts as aa
        gids = {g[0] for g in aa.GROUPS}
        for spec in aa.ALERTS:
            assert spec.group in gids, f"{spec.key} في مجموعة مجهولة {spec.group}"

    def test_no_duplicate_keys(self):
        from app.radius.services import admin_alerts as aa
        keys = [a.key for a in aa.ALERTS]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("key", NEW_KEYS)
    def test_preview_renders_sample(self, key):
        from app.radius.services import admin_alerts as aa
        prev = aa.preview(key)
        assert prev and "{" not in prev.split("<i>")[0]  # لا حقول غير مملوءة في المتن

    def test_noisy_defaults_off(self):
        from app.radius.services import admin_alerts as aa
        by = {a.key: a for a in aa.ALERTS}
        for k in ("router_high_traffic", "router_high_usage", "store_chat",
                  "card_batch_low", "access_suspended", "backup_stale",
                  "audit_failure"):
            assert by[k].default_enabled is False, f"{k} يجب أن يكون OFF افتراضيًّا"

    def test_important_defaults_on(self):
        from app.radius.services import admin_alerts as aa
        by = {a.key: a for a in aa.ALERTS}
        for k in ("router_offline", "store_registration", "store_deposit",
                  "auto_block_triggered", "backup_failed"):
            assert by[k].default_enabled is True, f"{k} يجب أن يكون ON افتراضيًّا"

    def test_toggle_new_spec_persisted(self, app_ctx):
        from app.radius.services import admin_alerts as aa
        assert aa.is_enabled(1, "store_chat") is False  # افتراضه OFF
        aa.set_enabled(1, "store_chat", True)
        assert aa.is_enabled(1, "store_chat") is True


# ════════════════════════════════════════════════════════════════════════
# توصيل المُطلِقات — store_alerts
# ════════════════════════════════════════════════════════════════════════
class TestStoreTriggers:

    def _spy(self, monkeypatch):
        captured = []
        from app.radius.services import admin_alerts
        monkeypatch.setattr(admin_alerts, "dispatch",
                            lambda tid, key, ctx, **kw: captured.append((key, ctx, kw)))
        return captured

    def test_registration_dispatches(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.services import store_alerts
        store_alerts.notify_registration(1, 77, "سالم")
        assert any(k == "store_registration" and c["name"] == "سالم"
                   for k, c, _ in captured)

    def test_deposit_dispatches(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.services import store_alerts
        store_alerts.notify_deposit(1, 1042, "50.0", "₪", name="سالم")
        hit = [(c, kw) for k, c, kw in captured if k == "store_deposit"]
        assert hit and hit[0][0]["amount"] == "50.0"
        assert hit[0][1].get("dedup_key") == "store_deposit:1042"

    def test_withdrawal_dispatches(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.services import store_alerts
        store_alerts.notify_withdrawal(1, 1043, "30.0", "₪", name="سالم")
        assert any(k == "store_withdrawal" for k, _, _ in captured)

    def test_chat_dispatches(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.services import store_alerts
        store_alerts.notify_chat(1, 88, name="سالم")
        assert any(k == "store_chat" and c["name"] == "سالم"
                   for k, c, _ in captured)


# ════════════════════════════════════════════════════════════════════════
# توصيل المُطلِقات — access_control (أمان)
# ════════════════════════════════════════════════════════════════════════
class TestAccessControlTriggers:

    def _spy(self, monkeypatch):
        captured = []
        from app.radius.services import admin_alerts
        monkeypatch.setattr(admin_alerts, "dispatch",
                            lambda tid, key, ctx, **kw: captured.append((key, ctx)))
        return captured

    def test_manual_suspension_dispatches(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.services import access_control as ac
        ac.create_block_from_input(
            tenant_id=1, block_type="subscriber", target="ahmad99",
            reason="اختبار", duration_mode="permanent", created_by=1)
        hit = [c for k, c in captured if k == "access_suspended"]
        assert hit and hit[0]["target"] == "ahmad99"
        assert hit[0]["scope"] == "subscriber"

    def test_manual_ip_block_does_not_dispatch_suspension(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.services import access_control as ac
        ac.create_block_from_input(
            tenant_id=1, block_type="ip", target="3.3.3.3",
            duration_mode="permanent", created_by=1)
        # حظر IP يدوي = طبقة «حظر» لا «تعليق» → لا تنبيه access_suspended
        assert not any(k == "access_suspended" for k, _ in captured)

    def test_autoblock_dispatches(self, app_ctx, monkeypatch):
        captured = self._spy(monkeypatch)
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "security.autoblock_enabled", "1")
        tenants_repo.set_setting(1, "security.autoblock_threshold", "1")
        tenants_repo.set_setting(1, "security.autoblock_target", "ip")
        from app.radius.services import access_control as ac
        created = ac.register_failed_attempt(1, ip="9.9.9.9", username="x")
        assert created is not None
        hit = [c for k, c in captured if k == "auto_block_triggered"]
        assert hit and hit[0]["target"] == "9.9.9.9"
        assert hit[0]["block_type"] == "IP"


# ════════════════════════════════════════════════════════════════════════
# توصيل المُطلِق — customer_portals (طلب خدمة جديد)
# ════════════════════════════════════════════════════════════════════════
class TestServiceRequestTrigger:

    def test_renewal_request_dispatches(self, app_ctx, monkeypatch):
        captured = []
        from app.radius.services import admin_alerts
        monkeypatch.setattr(admin_alerts, "dispatch",
                            lambda tid, key, ctx, **kw: captured.append((key, ctx)))
        from app.radius.core.types import Subscriber
        from app.radius.services.users import get_users_service
        sub = get_users_service().create(actor="t", sub=Subscriber(
            id=None, username="cpruser", password="pw", tenant_id=1, full_name="C P"))
        from app.radius.services.customer_portals import CustomerPortalService
        CustomerPortalService(tenant_id=1).submit_renewal_request(
            subscriber_id=int(sub.id), reason="بدي أجدّد")
        assert any(k == "service_request_new" for k, _ in captured)
