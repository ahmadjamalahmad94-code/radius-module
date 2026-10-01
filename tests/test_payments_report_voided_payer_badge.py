"""«دفعات المستفيدين»: مَن أُلغيت كلُّ دفعاته يظهر بشارة «ملغاة» ولا يُعَدّ دافعًا.

**قرارُ المالك 2026-10-01 (الخيار ج):** كان التقريرُ يَعدّ الدافعَ الملغاةَ كلُّ
دفعاته (52 والفعليّون 47)، فاقترح وكيلٌ إخفاءَه فأضاع أثرَ المراجعة. الحلُّ الوسط:
- **يبقى ظاهرًا** في القائمة (دفعَ ثمّ أُلغيت دفعتُه — أثرٌ لا يُمحى)،
- **بشارة «ملغاة»**،
- و**لا يُحسب** في «عدد الدافعين» (``payers``) بل في ``voided_payers``.
والإلغاءُ **الجزئيّ** (بقيت له دفعةٌ فعليّة) ليس «ملغاة».

شغّلْ هذا الملفَّ وحدَه."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "voidpayer.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield flask_app


def db():
    from app.radius.db.connection import db as live
    return live()


def _pay(sub_id: int, username: str, amount: float) -> int:
    cur = db().execute(
        "INSERT INTO accounting_ledger_entries(tenant_id,entry_type,direction,status,"
        " amount,currency,subscriber_id,username,created_at)"
        " VALUES(1,'payment','credit','posted',?,'ILS',?,?,datetime('now'))",
        (amount, sub_id, username))
    return int(cur.lastrowid)


def _void(entry_id: int, *_ignored) -> None:
    """الإلغاءُ عبر المسارِ الحقيقيّ (قيدٌ عكسيّ واحد؛ الأصلُ يبقى posted)."""
    db().commit()
    from app.radius.db.repos import accounting_repo as ar
    ar.void_ledger_entry(tenant_id=1, entry_id=entry_id, actor="test", reason="t")


def _seed():
    for sid, u in ((1, "paid_ok"), (2, "all_voided"), (3, "partial")):
        db().execute("INSERT INTO subscribers(id,tenant_id,username,password,status,"
                     " user_type,created_at) VALUES(?,1,?,'x','enabled','subscriber',"
                     " datetime('now'))", (sid, u))
    # دافعٌ فعليّ — دفعتان قائمتان
    _pay(1, "paid_ok", 50)
    _pay(1, "paid_ok", 30)
    # دافعٌ **أُلغيت كلُّ دفعاته** — «ملغاة»
    e = _pay(2, "all_voided", 40)
    _void(e, 2, "all_voided", 40)
    # إلغاءٌ **جزئيّ** — بقيت له دفعةٌ فعليّة ⇒ ليس «ملغاة»
    e1 = _pay(3, "partial", 20)
    _pay(3, "partial", 10)
    _void(e1, 3, "partial", 20)
    db().commit()


def test_fully_voided_payer_stays_visible_flagged_and_uncounted(app_ctx):
    _seed()
    from app.radius.db.repos import accounting_repo as ar
    rows = {r["username"]: r for r in ar.subscriber_payment_report(1)}

    # يبقى ظاهرًا — أثرُ المراجعةِ لا يُمحى
    assert "all_voided" in rows, "الدافعُ الملغاةُ دفعاتُه اختفى — ضاع أثرُ المراجعة"
    assert rows["all_voided"]["voided"] is True
    assert rows["all_voided"]["count"] == 0 and rows["all_voided"]["total"] == 0.0
    # الإلغاءُ الجزئيُّ ليس «ملغاة»
    assert rows["partial"]["voided"] is False and rows["partial"]["count"] == 1
    assert rows["paid_ok"]["voided"] is False

    totals = ar.subscriber_payment_totals(1)
    assert totals["payers"] == 2, "«عدد الدافعين» يَعدّ مَن أُلغيت كلُّ دفعاته"
    assert totals["voided_payers"] == 1
    # الاتّساق: عددُ الدافعين = صفوفُ القائمةِ غيرُ الملغاة بالضبط
    assert totals["payers"] == sum(1 for r in rows.values() if not r["voided"])


def test_report_page_shows_badge_and_no_raw_internal_columns(app_ctx):
    _seed()
    from app.radius.db.repos import admins_repo
    admins_repo.create_admin(username="rep_owner", password="pw", full_name="مالك",
                             is_super_admin=True,
                             role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
    db().execute("UPDATE admins SET is_co_owner=1 WHERE username='rep_owner'")
    db().commit()
    c = app_ctx.test_client()
    assert c.post("/admin/radius/login", data={"username": "rep_owner", "password": "pw"},
                  follow_redirects=False).status_code in (302, 303)

    r = c.get("/admin/radius/finance/reports?type=subscriber_payments",
              follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    assert "ملغاة" in html, "شارةُ «ملغاة» غائبة"
    for raw in (">by_currency<", ">mixed_currency<", ">voided<", "[{'currency'", ">False<", ">True<"):
        i = html.find(raw)
        assert raw not in html, f"مفتاحٌ داخليٌّ يظهر خامًّا للمستخدم: {raw} … {html[max(0, i-120):i+20]!r}"


def test_export_carries_the_voided_mark_in_the_username(app_ctx):
    _seed()
    from app.radius.services.accounting import AccountingService
    csv_text = AccountingService(tenant_id=1).report_csv(report_type="subscriber_payments")
    assert "all_voided (ملغاة)" in csv_text
    assert "partial (ملغاة)" not in csv_text and "paid_ok (ملغاة)" not in csv_text
    assert "voided" not in csv_text.splitlines()[0], "عمودٌ خامّ في رأس التصدير"
