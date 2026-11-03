"""«محفظة المشترك» ميزةٌ تُفعَّل من الإعدادات — معطّلة افتراضيًّا (قرار المالك 2026-11-03).

السبب: «مدفوع — نقدًا» كان يخصم من رصيد المشترك، فالموظّف الذي قبض المبلغ
للتوّ يرى «رصيد المشترك (0.00) لا يكفي لخصم 200» ويظنّها خللًا. الآن:
  • الافتراضيّ دفع فوري: مجّاني / كاش / دين — «كاش» قُبض الآن ولا يمسّ الرصيد.
  • «محفظة» (paid = خصم من الرصيد) لا تظهر إلّا حين تُفعَّل.
  • «paid» من نسخة تطبيقٍ قديمة والمحفظة معطّلة ⇒ يُعامَل «كاش».
  • الدين يُسدَّد بزرّ «تسديد دين» ولا يتجاوز المبلغُ الدين.

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "wallet.db")
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


def _mk(username, *, balance=0.0, expire_at=None, price=30.0):
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,price,"
        " currency,speed_down_kbps,speed_up_kbps,quota_total_mb,created_at,updated_at)"
        " VALUES(1,'باقة',43200,30,?,'ILS',2048,2048,0,datetime('now'),datetime('now'))",
        (price,))
    exp = expire_at if expire_at is not None else datetime.utcnow() + timedelta(days=3)
    db().execute(
        "INSERT INTO subscribers(tenant_id,username,password,plan_id,status,user_type,"
        " expire_at,balance,created_at) VALUES(1,?,'pw',?,'enabled','user',?,?,datetime('now'))",
        (username, int(cur.lastrowid), exp.isoformat() + "Z", balance))


def _balance(username):
    return float(db().execute("SELECT balance FROM subscribers WHERE username=?",
                              (username,)).fetchone()["balance"] or 0)


def _ledger(username):
    rows = db().execute(
        "SELECT entry_type, direction, amount, source_type FROM accounting_ledger_entries "
        "WHERE username=? ORDER BY id", (username,)).fetchall()
    return [(r["entry_type"], r["direction"], round(float(r["amount"]), 2), r["source_type"])
            for r in rows]


def _wallet(on: bool):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "subscribers.wallet_enabled", "1" if on else "0")


def _svc():
    from app.radius.services.users import get_users_service
    return get_users_service()


# ── الإعداد ──

def test_wallet_is_off_by_default_and_turns_on(app_ctx):
    from app.radius.core.system_config import system_config, wallet_enabled
    assert wallet_enabled(1) is False
    assert system_config()["wallet_enabled"] is False
    _wallet(True)
    assert wallet_enabled(1) is True
    assert system_config()["wallet_enabled"] is True


def test_paid_from_an_old_client_means_cash_while_the_wallet_is_off(app_ctx):
    from app.radius.services.users import normalize_charge_mode
    assert normalize_charge_mode("paid", 1) == "cash"
    assert normalize_charge_mode(None, 1) == "free"
    assert normalize_charge_mode(" Debt ", 1) == "debt"
    assert normalize_charge_mode("cash", 1) == "cash"
    _wallet(True)
    assert normalize_charge_mode("paid", 1) == "paid"


# ── «كاش»: قُبض الآن، لا يمسّ الرصيد ──

def test_cash_extend_needs_no_balance_and_leaves_it_untouched(app_ctx):
    """🔴 الشكوى بعينها: رصيد 0 و«مدفوع» ⇒ «لا يكفي لخصم 200». الكاش يمرّ."""
    _mk("u0", balance=0.0)
    _svc().extend_time(actor="t", username="u0", minutes=1440, charge_mode="cash",
                       amount=200.0, currency="ILS")
    assert _balance("u0") == 0.0
    led = _ledger("u0")
    # إيداعٌ نقديّ ثمّ استهلاكه — كما «أضف رصيدًا ثمّ ادفع» تمامًا (التقارير لا تتغيّر).
    assert led == [("cash_balance", "credit", 200.0, "subscriber_cash_payment"),
                   ("time_extension", "debit", 200.0, "subscriber_time_extension")]


def test_cash_does_not_silently_settle_an_existing_debt(app_ctx):
    _mk("ud", balance=-50.0)
    _svc().extend_time(actor="t", username="ud", minutes=60, charge_mode="cash",
                       amount=10.0, currency="ILS")
    assert _balance("ud") == -50.0


def test_cash_set_expiry_rejected_leaves_no_money_behind(app_ctx):
    """رفضٌ بعد القبض (قفزة > سنة) ⇒ لا إيداع يبقى ولا رصيد يتغيّر (@atomic)."""
    from app.radius.core.errors import RadiusError
    _mk("ur", balance=0.0)
    with pytest.raises(RadiusError):
        _svc().set_expiry(actor="t", username="ur",
                          expire_at=datetime.utcnow() + timedelta(days=800),
                          charge_mode="cash", amount=50.0, currency="ILS")
    assert _balance("ur") == 0.0
    assert _ledger("ur") == []


def test_cash_amount_must_be_positive(app_ctx):
    from app.radius.core.errors import RadiusValidationError
    _mk("uz")
    with pytest.raises(RadiusValidationError):
        _svc().extend_time(actor="t", username="uz", minutes=60, charge_mode="cash",
                           amount=0, currency="ILS")


def test_wallet_paid_still_requires_the_balance(app_ctx):
    """المحفظة (paid) نفسها لم تتغيّر: رصيدٌ لا يكفي يُرفض."""
    from app.radius.core.errors import RadiusValidationError
    _wallet(True)
    _mk("uw", balance=5.0)
    with pytest.raises(RadiusValidationError):
        _svc().extend_time(actor="t", username="uw", minutes=60, charge_mode="paid",
                           amount=10.0, currency="ILS")
    _svc().extend_time(actor="t", username="uw", minutes=60, charge_mode="paid",
                       amount=5.0, currency="ILS")
    assert _balance("uw") == 0.0


# ── الويب ──

def _client(app_ctx):
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["tenant_id"] = 1
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["_csrf_token"] = "tok"
    return c


def test_web_paid_extend_with_wallet_off_records_cash(app_ctx):
    _mk("wp", balance=0.0)
    r = _client(app_ctx).post("/admin/radius/users/wp/extend",
                              data={"_csrf_token": "tok", "minutes": "60",
                                    "charge_mode": "paid", "amount": "12"})
    assert r.status_code in (302, 303)
    assert _balance("wp") == 0.0
    assert ("cash_balance", "credit", 12.0, "subscriber_cash_payment") in _ledger("wp")


def test_debt_settle_is_capped_at_the_debt(app_ctx):
    _mk("dd", balance=-30.0)
    r = _client(app_ctx).post("/admin/radius/users/dd/balance/add",
                              data={"_csrf_token": "tok", "purpose": "debt", "amount": "100"})
    assert r.status_code in (302, 303)
    assert "/users/dd/finance" in r.headers["Location"]
    assert _balance("dd") == 0.0          # 30 سُدّدت، لا رصيد موجب من الـ70 الباقية


def test_balance_add_with_wallet_off_only_settles_debt(app_ctx):
    _mk("nb", balance=0.0)
    _client(app_ctx).post("/admin/radius/users/nb/balance/add",
                          data={"_csrf_token": "tok", "amount": "40"})
    assert _balance("nb") == 0.0          # لا دين ⇒ لا شيء يُضاف
    _wallet(True)
    _client(app_ctx).post("/admin/radius/users/nb/balance/add",
                          data={"_csrf_token": "tok", "amount": "40"})
    assert _balance("nb") == 40.0         # المحفظة مفعّلة ⇒ إضافة رصيد كما كانت


def test_settings_page_saves_the_toggle(app_ctx):
    from app.radius.core.system_config import wallet_enabled
    c = _client(app_ctx)
    html = c.get("/admin/radius/settings").get_data(as_text=True)
    assert 'name="subscribers.wallet_enabled"' in html
    c.post("/admin/radius/settings", data={"_csrf_token": "tok",
                                           "subscribers.wallet_enabled": "1"})
    assert wallet_enabled(1) is True
    c.post("/admin/radius/settings", data={"_csrf_token": "tok",
                                           "subscribers.wallet_enabled": "yes"})
    assert wallet_enabled(1) is False     # قيمةٌ غير 1 ⇒ معطّلة


def test_users_list_modals_follow_the_toggle(app_ctx):
    _mk("lm")
    c = _client(app_ctx)
    html = c.get("/admin/radius/users").get_data(as_text=True)
    assert 'value="cash" checked' in html
    assert 'name="charge_mode" value="paid"' not in html
    _wallet(True)
    html = c.get("/admin/radius/users").get_data(as_text=True)
    assert 'name="charge_mode" value="paid"' in html


def test_pages_show_debt_not_balance_while_the_wallet_is_off(app_ctx):
    _mk("pg", balance=-25.0)
    sid = int(db().execute("SELECT id FROM subscribers WHERE username='pg'").fetchone()["id"])
    c = _client(app_ctx)
    fin = c.get("/admin/radius/users/pg/finance")
    assert fin.status_code == 200
    fin = fin.get_data(as_text=True)
    assert "تسديد دين" in fin and 'name="purpose" value="debt"' in fin
    assert "الرصيد الحالي" not in fin
    for url in ("/admin/radius/users/pg/profile", f"/admin/radius/subscribers/{sid}",
                "/admin/radius/plans/new"):
        r = c.get(url)
        assert r.status_code == 200, url
    assert 'value="balance"' not in c.get("/admin/radius/plans/new").get_data(as_text=True)
    _wallet(True)
    assert "الرصيد الحالي" in c.get("/admin/radius/users/pg/finance").get_data(as_text=True)
    assert 'value="balance"' in c.get("/admin/radius/plans/new").get_data(as_text=True)
