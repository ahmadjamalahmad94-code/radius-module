"""feat/currency-propagation — تحقّق قوي أنّ ضبط العملة مرّة يعمّم على كل
عرض مبلغ/سعر/فاتورة في اللوحة.

المرجع الموحّد: ``system_config.default_currency()`` + فلتر ``money`` +
سياق ``cfg`` (كلّها تقرأ ``billing.currency`` لكل مستأجر عبر g.tenant_id).

يغطّي:
  • المصدر المركزي: تغيير الإعداد يغيّر default_currency/cfg/format_money.
  • الفلتر الذي تستخدمه كل القوالب يعمّم العملة (SAR ثم ILS).
  • per-tenant (سياق مفتاح المتجر): مستأجران بعملتين مختلفتين.
  • المبلغ المخزَّن بعملته الأصلية لا يُعاد تسميته (format_money(x, "ILS")).
  • تصيير صفحة حقيقية (فاتورة جديدة) يعكس العملة المضبوطة.
  • حارس انحدار: لا رمز عملة مثبَّت ولا «تكرار عملة» في قوالب عرض المال.

شغّل الملف وحده.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# الرموز القانونية (من system_config.CURRENCY_SYMBOLS).
SYM = {"JOD": "د.أ", "ILS": "₪", "USD": "$", "SAR": "ر.س", "IQD": "د.ع",
       "EGP": "ج.م", "AED": "د.إ", "EUR": "€", "TRY": "₺"}


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "currency.db")
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


def _set_currency(tenant_id, code):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(tenant_id, "billing.currency", code)


def _with_tenant(tid):
    from flask import g
    g.tenant_id = tid


# ════════════════════════════════════════════════════════════════════════
# (1) المصدر المركزي
# ════════════════════════════════════════════════════════════════════════
class TestCentralSource:

    def test_default_currency_reads_setting_live(self, app_ctx):
        from app.radius.core.system_config import default_currency
        _with_tenant(1)
        _set_currency(1, "SAR")
        assert default_currency() == "SAR"
        # تغييرها يعمّم فورًا — بلا إعادة تشغيل.
        _set_currency(1, "ILS")
        assert default_currency() == "ILS"

    def test_cfg_symbol_follows_currency(self, app_ctx):
        from app.radius.core.system_config import system_config
        _with_tenant(1)
        _set_currency(1, "SAR")
        assert system_config()["currency_symbol"] == SYM["SAR"]
        _set_currency(1, "EGP")
        assert system_config()["currency_symbol"] == SYM["EGP"]

    def test_format_money_uses_tenant_currency(self, app_ctx):
        from app.radius.core.system_config import format_money
        _with_tenant(1)
        _set_currency(1, "SAR")
        out = format_money(50)
        assert SYM["SAR"] in out and SYM["JOD"] not in out
        _set_currency(1, "ILS")
        assert SYM["ILS"] in format_money(50)


# ════════════════════════════════════════════════════════════════════════
# (2) الفلتر الذي تستخدمه كل القوالب يعمّم العملة
# ════════════════════════════════════════════════════════════════════════
class TestFilterPropagation:

    def _render(self, app_ctx, src):
        return app_ctx.jinja_env.from_string(src).render()

    def test_money_filter_follows_currency(self, app_ctx):
        _with_tenant(1)
        _set_currency(1, "SAR")
        assert SYM["SAR"] in self._render(app_ctx, "{{ 50|money }}")
        # تغيير العملة يعمّم على نفس الفلتر المستخدم في كل صفحة.
        _set_currency(1, "ILS")
        assert SYM["ILS"] in self._render(app_ctx, "{{ 50|money }}")

    def test_explicit_currency_not_relabeled(self, app_ctx):
        """مبلغ مخزَّن بعملته الأصلية (تاريخي) لا يُعاد تسميته بعملة المستأجر."""
        _with_tenant(1)
        _set_currency(1, "SAR")          # عملة المستأجر SAR
        out = self._render(app_ctx, "{{ 50|money('ILS') }}")
        assert SYM["ILS"] in out and SYM["SAR"] not in out


# ════════════════════════════════════════════════════════════════════════
# (3) per-tenant — سياق مفتاح المتجر (كل مستأجر عملته)
# ════════════════════════════════════════════════════════════════════════
class TestPerTenant:

    def test_two_tenants_distinct_currencies(self, app_ctx):
        from app.radius.core.system_config import format_money
        from app.radius.core.tenant import Tenant
        from app.radius.db.repos import tenants_repo
        t2 = tenants_repo.create_tenant(Tenant(id=None, slug="t2", name="مستأجر ٢"))
        _set_currency(1, "SAR")
        _set_currency(t2.id, "ILS")
        _with_tenant(1)
        assert SYM["SAR"] in format_money(10)
        _with_tenant(t2.id)
        assert SYM["ILS"] in format_money(10) and SYM["SAR"] not in format_money(10)


# ════════════════════════════════════════════════════════════════════════
# (4) تصيير صفحة حقيقية يعكس العملة المضبوطة
# ════════════════════════════════════════════════════════════════════════
class TestPageRender:

    def _client(self, app_ctx):
        c = app_ctx.test_client()
        with c.session_transaction() as s:
            s["tenant_id"] = 1
            s["admin_id"] = 1
            s["is_super_admin"] = True
            s["_csrf_token"] = "tok"
        return c

    def test_billing_page_amounts_follow_currency(self, app_ctx):
        """صفحة الفوترة الفعليّة (المؤشّرات تستخدم فلتر money) تعكس العملة."""
        _set_currency(1, "SAR")
        html = self._client(app_ctx).get(
            "/admin/radius/finance/billing?tab=invoices").get_data(as_text=True)
        assert SYM["SAR"] in html        # مبالغ الصفحة برمز عملة المستأجر
        assert SYM["JOD"] not in html    # لا رمز عملة قديم/خاطئ

    def test_currency_change_propagates_to_billing_page(self, app_ctx):
        """تغيير العملة مرّة → تنعكس على نفس الصفحة (تعميم حيّ)."""
        c = self._client(app_ctx)
        _set_currency(1, "SAR")
        h1 = c.get("/admin/radius/finance/billing?tab=invoices").get_data(as_text=True)
        assert SYM["SAR"] in h1
        _set_currency(1, "ILS")
        h2 = c.get("/admin/radius/finance/billing?tab=invoices").get_data(as_text=True)
        assert SYM["ILS"] in h2 and SYM["SAR"] not in h2


# ════════════════════════════════════════════════════════════════════════
# (5) حارس انحدار — لا رمز مثبَّت ولا «تكرار عملة» في قوالب عرض المال
# ════════════════════════════════════════════════════════════════════════
class TestRegressionGuards:

    # ملفات يُسمح فيها بسرد رموز العملات: منتقيات العملة (إعدادات/قنوات) +
    # دليل توضيحي ثابت. ليست عرض مبلغ حقيقي.
    _ALLOWED = {
        "settings_page.html",            # منتقي عملة النظام
        "payment_collection_settings.html",  # منتقي عملة قناة الاستلام
        "finance_collection.html",       # منتقي عملة القناة (قائمة كاملة)
        "communications_guide.html",     # مثال دليل ثابت (رسالة بوت)
    }
    _SYMS = ["₪", "د.أ", "ر.س", "ج.م", "د.إ"]  # رموز أجنبية في سياق عرض

    def _templates(self):
        roots = [Path("app/templates"), Path("app/radius/templates")]
        for root in roots:
            if root.exists():
                yield from root.rglob("*.html")

    def test_no_money_filter_double_render(self):
        """لا «{{ x|money }} {{ y_currency }}» — الفلتر يضيف الرمز فلا يُكرَّر."""
        pat = re.compile(r"\|\s*money[^}]*\}\}\s*(?:<[^>]+>\s*)?\{\{\s*[\w.]*currency")
        offenders = []
        for f in self._templates():
            txt = f.read_text(encoding="utf-8", errors="ignore")
            if pat.search(txt):
                offenders.append(f.as_posix())
        assert not offenders, f"تكرار عملة بعد فلتر money في: {offenders}"

    def test_no_hardcoded_symbol_in_money_templates(self):
        """لا رمز عملة مثبَّت في قوالب العرض (عدا المنتقيات/الدليل)."""
        offenders = []
        for f in self._templates():
            if f.name in self._ALLOWED:
                continue
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith("//") or s.startswith("/*") or s.startswith("*"):
                    continue  # تعليق JS/CSS وصفي — ليس عرضًا
                if any(sym in line for sym in self._SYMS):
                    offenders.append(f"{f.name}: {s[:90]}")
        assert not offenders, "رموز عملة مثبَّتة في عرض المال:\n" + "\n".join(offenders)
