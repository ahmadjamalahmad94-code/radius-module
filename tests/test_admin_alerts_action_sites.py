# -*- coding: utf-8 -*-
"""feat/admin-notif-wire-all — توصيل تنبيهات الإدارة عند مواقع الأفعال الحقيقية.

كل فعل إداريّ (إضافة وقت/رصيد/كوتا، استعادة كوتا، سلفة، تسجيل دفعة) يجب أن
يُطلِق ``admin_alerts.dispatch`` مرّة واحدة بمفتاحه الصحيح وسياق منسّق نظيف
(سطرٌ لكلّ حقيقة). نُراقب dispatch ونتحقّق من المفتاح + السياق + شكل التصيير.
شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "admin_alerts_action_sites.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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


def _spy(monkeypatch):
    """يستبدل admin_alerts.dispatch بجامع (key, ctx, dedup_key)."""
    captured = []
    from app.radius.services import admin_alerts
    monkeypatch.setattr(
        admin_alerts, "dispatch",
        lambda tid, key, ctx=None, **kw: captured.append(
            (key, ctx or {}, kw.get("dedup_key"))))
    return captured


def _make_subscriber(username="actuser", plan_id=None):
    from app.radius.core.types import Subscriber
    from app.radius.services.users import get_users_service
    return get_users_service().create(actor="seed", sub=Subscriber(
        id=None, username=username, password="pw", tenant_id=1,
        full_name="أحمد علي", mobile="0599000111", plan_id=plan_id))


def _only(captured, key):
    """يُعيد سياقات الأحداث ذات المفتاح key فقط (مُتجاهِلًا subscriber_new وغيره)."""
    return [(ctx, dedup) for k, ctx, dedup in captured if k == key]


# ════════════════════════════════════════════════════════════════════════
# المواصفات الجديدة موجودة + مجموعتها «المشتركون» + تصييرها نظيف
# ════════════════════════════════════════════════════════════════════════
NEW_KEYS = ["time_added", "credit_added", "quota_added", "quota_restored"]


class TestNewSpecs:
    def test_new_keys_present_in_subscribers_group(self):
        from app.radius.services import admin_alerts as aa
        by = {a.key: a for a in aa.ALERTS}
        for k in NEW_KEYS:
            assert k in by, f"المواصفة الناقصة: {k}"
            assert by[k].group == "subscribers", f"{k} ليست في مجموعة المشتركين"
            assert by[k].default_enabled is True, f"{k} يجب أن يكون ON افتراضيًّا"

    @pytest.mark.parametrize("key", NEW_KEYS + ["loan_granted", "payment_received"])
    def test_preview_clean_no_raw_placeholders(self, key):
        from app.radius.services import admin_alerts as aa
        prev = aa.preview(key)
        # لا حقول غير مملوءة في المتن (قبل التذييل)، وسطر فارغ بعد العنوان.
        body = prev.split("<i>🕐")[0]
        assert "{" not in body and "}" not in body, f"{key}: حقل خام في المتن"
        title = aa.get_spec(key).template.split("\n")[0]
        assert prev.startswith(title + "\n\n"), f"{key}: لا سطر فارغ بعد العنوان"

    def test_one_fact_per_line_bolded(self):
        from app.radius.services import admin_alerts as aa
        out = aa.render("credit_added", {
            "username": "actuser", "amount": "50.00 ₪",
            "new_balance": "120.00 ₪", "actor": "المدير"})
        assert "<b>المبلغ:</b> 50.00 ₪" in out
        assert "<b>الرصيد الجديد:</b> 120.00 ₪" in out
        assert "<b>المشترك:</b> <code>actuser</code>" in out


# ════════════════════════════════════════════════════════════════════════
# مواقع الأفعال — users.UsersService
# ════════════════════════════════════════════════════════════════════════
class TestUsersServiceTriggers:
    def test_extend_time_free_fires_time_added(self, app_ctx, monkeypatch):
        _make_subscriber("u_time")
        cap = _spy(monkeypatch)
        from app.radius.services.users import get_users_service
        get_users_service().extend_time(actor="المدير", username="u_time",
                                        minutes=2 * 24 * 60)
        hits = _only(cap, "time_added")
        assert len(hits) == 1
        ctx, dedup = hits[0]
        assert ctx["username"] == "u_time"
        assert "يوم" in ctx["duration"]
        assert ctx["new_expiry"] and ctx["new_expiry"] != "—"
        assert ctx["kind"] == "مجاني" and ctx["actor"] == "المدير"
        assert dedup and dedup.startswith("time_added:")
        # لا يُطلِق سلفة في النمط المجانيّ.
        assert not _only(cap, "loan_granted")

    def test_extend_time_debt_fires_loan_granted(self, app_ctx, monkeypatch):
        _make_subscriber("u_debt")
        cap = _spy(monkeypatch)
        from app.radius.services.users import get_users_service
        get_users_service().extend_time(actor="المدير", username="u_debt",
                                        minutes=600, charge_mode="debt",
                                        amount=15.0, currency="ILS")
        loans = _only(cap, "loan_granted")
        assert len(loans) == 1
        ctx, _ = loans[0]
        assert ctx["username"] == "u_debt"
        assert "دين" in ctx["status"]
        assert "15.00" in ctx["amount"]
        assert not _only(cap, "time_added")

    def test_add_cash_balance_fires_credit_added(self, app_ctx, monkeypatch):
        _make_subscriber("u_credit")
        cap = _spy(monkeypatch)
        from app.radius.services.users import get_users_service
        get_users_service().add_cash_balance(actor="المدير", username="u_credit",
                                             amount=50.0, currency="ILS")
        hits = _only(cap, "credit_added")
        assert len(hits) == 1
        ctx, _ = hits[0]
        assert ctx["username"] == "u_credit"
        assert "50.00" in ctx["amount"]
        assert "50.00" in ctx["new_balance"]  # رصيد جديد فعليّ
        assert ctx["actor"] == "المدير"

    def test_add_quota_fires_quota_added(self, app_ctx, monkeypatch):
        _make_subscriber("u_quota")
        cap = _spy(monkeypatch)
        from app.radius.services.users import get_users_service
        get_users_service().add_quota(actor="المدير", username="u_quota",
                                      quota_mb=5120)
        hits = _only(cap, "quota_added")
        assert len(hits) == 1
        ctx, _ = hits[0]
        assert ctx["username"] == "u_quota"
        assert "5120 م.ب" in ctx["quota"]
        assert "5120 م.ب" in ctx["new_total"]
        assert ctx["actor"] == "المدير"

    def test_reset_daily_quota_fires_quota_restored(self, app_ctx, monkeypatch):
        _make_subscriber("u_restore")
        cap = _spy(monkeypatch)
        from app.radius.services.users import get_users_service
        get_users_service().reset_daily_quota(actor="المدير", username="u_restore")
        hits = _only(cap, "quota_restored")
        assert len(hits) == 1
        ctx, _ = hits[0]
        assert ctx["username"] == "u_restore"
        assert "تصفير" in ctx["detail"]
        assert ctx["actor"] == "المدير"


# ════════════════════════════════════════════════════════════════════════
# مواقع الأفعال — accounting.AccountingService
# ════════════════════════════════════════════════════════════════════════
class TestAccountingTriggers:
    def test_create_payment_fires_payment_received(self, app_ctx, monkeypatch):
        _make_subscriber("u_pay")
        cap = _spy(monkeypatch)
        from app.radius.services.accounting import AccountingService
        AccountingService(tenant_id=1).create_payment(
            actor="المحصّل",
            body={"username": "u_pay", "amount": "20", "currency": "ILS",
                  "method": "cash"})
        hits = _only(cap, "payment_received")
        assert len(hits) == 1
        ctx, dedup = hits[0]
        assert ctx["username"] == "u_pay"
        assert "20.00" in ctx["amount"]
        assert ctx["method"] == "نقدًا"
        assert ctx["actor"] == "المحصّل"
        assert dedup and dedup.startswith("payment:")

    def test_create_loan_fires_loan_granted(self, app_ctx, monkeypatch):
        _make_subscriber("u_loan")
        cap = _spy(monkeypatch)
        from app.radius.services.accounting import AccountingService
        AccountingService(tenant_id=1).create_loan(
            actor="المدير",
            body={"username": "u_loan", "hours": 24})
        hits = _only(cap, "loan_granted")
        assert len(hits) == 1
        ctx, dedup = hits[0]
        assert ctx["username"] == "u_loan"
        assert "يوم" in ctx["duration"] or "ساعة" in ctx["duration"]
        assert ctx["actor"] == "المدير"
        assert "مجانية" in ctx["amount"]  # سلفة بلا قيمة ماليّة
        assert dedup and dedup.startswith("loan:")


# ════════════════════════════════════════════════════════════════════════
# لا يكسر الفعل أبدًا حتى لو انفجر المُرسِل (fire-and-forget)
# ════════════════════════════════════════════════════════════════════════
class TestDefensive:
    def test_dispatch_explosion_does_not_break_action(self, app_ctx, monkeypatch):
        _make_subscriber("u_safe")
        from app.radius.services import admin_alerts

        def _boom(*a, **k):
            raise RuntimeError("telegram down")

        monkeypatch.setattr(admin_alerts, "dispatch", _boom)
        from app.radius.services.users import get_users_service
        # يجب أن ينجح الفعل رغم انفجار المُرسِل.
        saved = get_users_service().add_cash_balance(
            actor="x", username="u_safe", amount=10.0)
        assert float(saved.balance or 0) >= 10.0
