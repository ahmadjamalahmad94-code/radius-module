"""R6 (r6-ops, client20): «إضافة وقت» لمشتركٍ **بلا انتهاء** كانت تُحوّله إلى
مشتركٍ ينتهي بعد المدّة المضافة.

``extend_time`` يرسو على ``max(expire_at, now)`` و``expire_at`` الفارغ يعني
«غير محدود» — فإضافةُ ساعةٍ جعلت نهايتَه «الآن + ساعة»: يُقطع بعد ساعة، وإن كانت
الإضافةُ مدفوعةً/على الدين خُصم ثمنُها أيضًا. على client20: 100/100 (مجّانيّ) +
10/10 (على الدين). السلفةُ على المشترك نفسه تُبقيه غير محدود — فالتمديدُ الآن
يُرفض برسالةٍ واضحة ولا يمسّ شيئًا. شغّل هذا الملف وحده.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from app.radius.core.errors import RadiusValidationError


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "r6unl.db")
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


def _seed(username, expire_at, balance=10.0):
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,"
        " price,currency,created_at,updated_at) VALUES(1,'شهري',0,30,70,'ILS',"
        "datetime('now'),datetime('now'))")
    plan_id = int(cur.lastrowid)
    db().execute(
        "INSERT INTO subscribers(tenant_id,username,password,plan_id,status,"
        " user_type,expire_at,balance,created_at)"
        " VALUES(1,?,'pw1234',?,'enabled','subscriber',?,?,datetime('now'))",
        (username, plan_id, expire_at, balance))
    db().commit()


def _row(username):
    return db().execute("SELECT expire_at, balance FROM subscribers WHERE username=?",
                        (username,)).fetchone()


def _ledger(username):
    return db().execute("SELECT COUNT(*) n FROM accounting_ledger_entries WHERE username=?",
                        (username,)).fetchone()["n"]


@pytest.mark.parametrize("mode,amount", [("free", 0.0), ("debt", 1.0), ("paid", 1.0)])
def test_extend_rejects_unlimited_subscriber_and_changes_nothing(app_ctx, mode, amount):
    _seed("unl1", None)
    from app.radius.services.users import get_users_service
    with pytest.raises(RadiusValidationError):
        get_users_service().extend_time(actor="t", username="unl1", minutes=60,
                                        charge_mode=mode, amount=amount, currency="ILS")
    r = _row("unl1")
    assert r["expire_at"] is None          # still unlimited
    assert float(r["balance"]) == 10.0      # nothing charged
    assert _ledger("unl1") == 0


def test_action_path_rejects_before_the_spend_gate(app_ctx):
    """المسار الموحّد للويب والـAPI (subscriber_actions.extend_subscriber)."""
    _seed("unl2", None)
    from app.radius.services import subscriber_actions as sa
    caller = sa.ActionCaller(tenant_id=1, admin_id=None, is_super=True, actor="t")
    with pytest.raises(RadiusValidationError):
        sa.extend_subscriber(caller, "unl2", minutes=120, charge_mode="debt", amount=2.0,
                             currency="ILS")
    assert _row("unl2")["expire_at"] is None


def test_set_expiry_still_converts_unlimited_on_purpose(app_ctx):
    """تعيينُ تاريخٍ صريح هو الطريقُ المقصود لتحويله إلى اشتراكٍ محدّد."""
    _seed("unl3", None)
    from app.radius.services.users import get_users_service
    target = (datetime.utcnow() + timedelta(days=3)).replace(microsecond=0)
    saved = get_users_service().set_expiry(actor="t", username="unl3", expire_at=target)
    assert saved.expire_at == target


def test_dated_subscriber_still_extends(app_ctx):
    exp = (datetime.utcnow() + timedelta(days=2)).replace(microsecond=0)
    _seed("dated", exp.isoformat() + "Z")
    from app.radius.services.users import get_users_service
    saved = get_users_service().extend_time(actor="t", username="dated", minutes=60)
    assert saved.expire_at == exp + timedelta(minutes=60)
