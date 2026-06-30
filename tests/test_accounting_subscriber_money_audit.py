"""Comprehensive money-movement audit for the SUBSCRIBER accounting paths.

These tests exercise the REAL services (no mocks of the unit under test) and
assert the THREE things the owner cares about for every money-moving op:

  1. the balance actually moves by exactly the charged amount,
  2. the correct ledger row(s) are written (entry_type / direction / amount),
  3. the value returned to the UI (``saved.balance``) reflects the NEW balance.

The headline regression: a «paid» add-on (quota restore / quota top-up / time
extension) MUST debit the charged amount from the subscriber balance. It must
never report «مدفوعة بقيمة X» while leaving the balance untouched.

Covered ops: reset_daily_quota, add_quota, extend_time, add_cash_balance,
apply_payment_to_balance, change_plan — across free / paid / debt, with
balance-after assertions, ledger-row assertions, zero/negative guards,
idempotency (repeated charges accumulate), and multi-tenant isolation.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


# ───────────────────────── fixtures / helpers ──────────────────────────────
@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = tmp_path / "subscriber_money_audit.db"
    monkeypatch.setenv("HOBERADIUS_DB_PATH", str(db_file))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(str(db_file))
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _auth_session(client) -> None:
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "money_admin"
        sess["admin_name"] = "Money Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "money-csrf"


def _seed_plan(name: str, *, price: float, days: int = 30, tenant_id: int = 1) -> int:
    from app.radius.db.connection import db

    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price,
            currency, enabled, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (tenant_id, name, days * 24 * 60, days, price, "JOD", 1,
         datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
    )
    return int(cur.lastrowid)


def _seed_subscriber(username: str, *, plan_id: int, balance: float = 0.0,
                     tenant_id: int = 1,
                     expire_at: datetime | None = None,
                     used_seconds: int = 0):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo

    sub = subscribers_repo.upsert_subscriber(
        Subscriber(
            id=None,
            tenant_id=tenant_id,
            username=username,
            password="secret",
            plan_id=plan_id,
            full_name="Money User",
            mobile="0599000000",
            status="enabled",
            expire_at=expire_at or (datetime.utcnow() + timedelta(days=7)),
        )
    )
    db().execute(
        "UPDATE subscribers SET balance=?, used_seconds=? WHERE tenant_id=? AND username=?",
        (float(balance), int(used_seconds), tenant_id, username),
    )
    return sub


def _get(username: str, tenant_id: int = 1):
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.get_subscriber(tenant_id, username)


def _ledger(username: str, *, source_type: str = "", tenant_id: int = 1):
    from app.radius.db.connection import db

    sql = ("SELECT * FROM accounting_ledger_entries WHERE tenant_id=? "
           "AND username=?")
    vals = [tenant_id, username]
    if source_type:
        sql += " AND source_type=?"
        vals.append(source_type)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def _svc():
    from app.radius.services.users import get_users_service

    return get_users_service()


# ════════════════════════════════════════════════════════════════════════
# GROUP A — reset_daily_quota
# ════════════════════════════════════════════════════════════════════════
RESET_AMOUNTS = [1.0, 2.5, 3.0, 5.0, 7.25, 10.0, 12.5, 20.0, 100.0]


@pytest.mark.parametrize("start,amount", [(20.0, a) for a in RESET_AMOUNTS])
def test_reset_paid_debits_balance_by_exact_amount(app, start, amount):
    with app.app_context():
        plan = _seed_plan(f"RP{amount}", price=20)
        _seed_subscriber("rp", plan_id=plan, balance=start, used_seconds=3600)
        saved = _svc().reset_daily_quota(actor="t", username="rp",
                                         charge_mode="paid", amount=amount,
                                         currency="JOD")
        assert _get("rp").balance == pytest.approx(start - amount)
        assert float(saved.balance) == pytest.approx(start - amount)
        assert _get("rp").used_seconds == 0


@pytest.mark.parametrize("amount", RESET_AMOUNTS)
def test_reset_paid_writes_debit_ledger_row(app, amount):
    with app.app_context():
        plan = _seed_plan(f"RPL{amount}", price=20)
        _seed_subscriber("rpl", plan_id=plan, balance=50.0)
        _svc().reset_daily_quota(actor="t", username="rpl",
                                 charge_mode="paid", amount=amount, currency="JOD")
        rows = _ledger("rpl", source_type="subscriber_daily_quota_reset")
        assert len(rows) == 1
        row = rows[0]
        assert row["entry_type"] == "quota_topup"
        assert row["direction"] == "debit"
        assert float(row["amount"]) == pytest.approx(amount)
        assert row["currency"] == "JOD"


@pytest.mark.parametrize("amount", RESET_AMOUNTS)
def test_reset_debt_debits_balance_and_writes_debt_row(app, amount):
    with app.app_context():
        plan = _seed_plan(f"RD{amount}", price=20)
        _seed_subscriber("rd", plan_id=plan, balance=0.0)
        _svc().reset_daily_quota(actor="t", username="rd",
                                 charge_mode="debt", amount=amount, currency="JOD")
        assert _get("rd").balance == pytest.approx(-amount)
        rows = _ledger("rd", source_type="subscriber_daily_quota_reset")
        assert rows and rows[0]["entry_type"] == "debt"
        assert rows[0]["direction"] == "debit"


def test_reset_free_moves_no_money_and_writes_no_ledger(app):
    with app.app_context():
        plan = _seed_plan("RF", price=20)
        _seed_subscriber("rf", plan_id=plan, balance=15.0, used_seconds=999)
        saved = _svc().reset_daily_quota(actor="t", username="rf")
        assert _get("rf").balance == pytest.approx(15.0)
        assert float(saved.balance) == pytest.approx(15.0)
        assert _get("rf").used_seconds == 0
        assert _ledger("rf", source_type="subscriber_daily_quota_reset") == []


def test_reset_paid_insufficient_balance_goes_negative(app):
    # The subscriber ledger permits negative balance (the debt model); a paid
    # charge larger than the balance pushes it negative rather than silently
    # doing nothing.
    with app.app_context():
        plan = _seed_plan("RN", price=20)
        _seed_subscriber("rn", plan_id=plan, balance=2.0)
        _svc().reset_daily_quota(actor="t", username="rn",
                                 charge_mode="paid", amount=5.0, currency="JOD")
        assert _get("rn").balance == pytest.approx(-3.0)


@pytest.mark.parametrize("mode", ["paid", "debt"])
@pytest.mark.parametrize("amount", [0.0, -1.0, -5.0])
def test_reset_rejects_non_positive_amount(app, mode, amount):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan(f"RZ{mode}{amount}", price=20)
        _seed_subscriber("rz", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().reset_daily_quota(actor="t", username="rz",
                                     charge_mode=mode, amount=amount)
        # balance untouched after a rejected charge
        assert _get("rz").balance == pytest.approx(10.0)


def test_reset_rejects_unknown_charge_mode(app):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan("RU", price=20)
        _seed_subscriber("ru", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().reset_daily_quota(actor="t", username="ru",
                                     charge_mode="bogus", amount=5.0)


def test_reset_paid_repeated_charges_accumulate(app):
    with app.app_context():
        plan = _seed_plan("RR", price=20)
        _seed_subscriber("rr", plan_id=plan, balance=30.0)
        for _ in range(3):
            _svc().reset_daily_quota(actor="t", username="rr",
                                     charge_mode="paid", amount=4.0, currency="JOD")
        assert _get("rr").balance == pytest.approx(30.0 - 12.0)
        assert len(_ledger("rr", source_type="subscriber_daily_quota_reset")) == 3


# ════════════════════════════════════════════════════════════════════════
# GROUP B — add_quota
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("amount", RESET_AMOUNTS)
def test_add_quota_paid_debits_balance(app, amount):
    with app.app_context():
        plan = _seed_plan(f"AQ{amount}", price=20)
        _seed_subscriber("aq", plan_id=plan, balance=80.0)
        saved = _svc().add_quota(actor="t", username="aq", quota_mb=500,
                                 charge_mode="paid", amount=amount, currency="JOD")
        assert _get("aq").balance == pytest.approx(80.0 - amount)
        assert float(saved.balance) == pytest.approx(80.0 - amount)
        rows = _ledger("aq", source_type="subscriber_quota_topup")
        assert rows and rows[0]["direction"] == "debit"
        assert rows[0]["entry_type"] == "quota_topup"


@pytest.mark.parametrize("amount", RESET_AMOUNTS)
def test_add_quota_debt_debits_balance(app, amount):
    with app.app_context():
        plan = _seed_plan(f"AQD{amount}", price=20)
        _seed_subscriber("aqd", plan_id=plan, balance=0.0)
        _svc().add_quota(actor="t", username="aqd", quota_mb=250,
                         charge_mode="debt", amount=amount, currency="JOD")
        assert _get("aqd").balance == pytest.approx(-amount)
        rows = _ledger("aqd", source_type="subscriber_quota_topup")
        assert rows and rows[0]["entry_type"] == "debt"


@pytest.mark.parametrize("target,col", [
    ("combined", "combined_quota_mb"),
    ("download", "download_quota_mb"),
    ("upload", "upload_quota_mb"),
])
def test_add_quota_target_increments_right_column_and_enables_limit(app, target, col):
    with app.app_context():
        plan = _seed_plan(f"AQT{target}", price=20)
        _seed_subscriber("aqt", plan_id=plan, balance=10.0)
        before = getattr(_get("aqt"), col) or 0
        _svc().add_quota(actor="t", username="aqt", quota_mb=300,
                         quota_target=target, charge_mode="free")
        after = _get("aqt")
        assert (getattr(after, col) or 0) == before + 300
        assert after.quota_limit_enabled


def test_add_quota_free_writes_no_ledger_and_keeps_balance(app):
    with app.app_context():
        plan = _seed_plan("AQF", price=20)
        _seed_subscriber("aqf", plan_id=plan, balance=12.0)
        _svc().add_quota(actor="t", username="aqf", quota_mb=100, charge_mode="free")
        assert _get("aqf").balance == pytest.approx(12.0)
        assert _ledger("aqf", source_type="subscriber_quota_topup") == []


@pytest.mark.parametrize("quota_mb", [0, -1, -500])
def test_add_quota_rejects_non_positive_quota(app, quota_mb):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan(f"AQZ{quota_mb}", price=20)
        _seed_subscriber("aqz", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().add_quota(actor="t", username="aqz", quota_mb=quota_mb,
                             charge_mode="paid", amount=5.0)


def test_add_quota_rejects_unknown_target(app):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan("AQUT", price=20)
        _seed_subscriber("aqut", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().add_quota(actor="t", username="aqut", quota_mb=100,
                             quota_target="sideways", charge_mode="free")


@pytest.mark.parametrize("mode", ["paid", "debt"])
def test_add_quota_rejects_non_positive_amount(app, mode):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan(f"AQNA{mode}", price=20)
        _seed_subscriber("aqna", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().add_quota(actor="t", username="aqna", quota_mb=100,
                             charge_mode=mode, amount=0.0)


# ════════════════════════════════════════════════════════════════════════
# GROUP C — extend_time
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("amount", RESET_AMOUNTS)
def test_extend_paid_debits_balance(app, amount):
    with app.app_context():
        plan = _seed_plan(f"ET{amount}", price=30)
        _seed_subscriber("et", plan_id=plan, balance=60.0)
        saved = _svc().extend_time(actor="t", username="et", minutes=720,
                                   charge_mode="paid", amount=amount, currency="JOD")
        assert _get("et").balance == pytest.approx(60.0 - amount)
        assert float(saved.balance) == pytest.approx(60.0 - amount)
        rows = _ledger("et", source_type="subscriber_time_extension")
        assert rows and rows[0]["direction"] == "debit"
        assert rows[0]["entry_type"] == "time_extension"


@pytest.mark.parametrize("amount", RESET_AMOUNTS)
def test_extend_debt_debits_balance(app, amount):
    with app.app_context():
        plan = _seed_plan(f"ETD{amount}", price=30)
        _seed_subscriber("etd", plan_id=plan, balance=0.0)
        _svc().extend_time(actor="t", username="etd", minutes=1440,
                           charge_mode="debt", amount=amount, currency="JOD")
        assert _get("etd").balance == pytest.approx(-amount)
        rows = _ledger("etd", source_type="subscriber_time_extension")
        assert rows and rows[0]["entry_type"] == "debt" and rows[0]["direction"] == "debit"


@pytest.mark.parametrize("minutes", [60, 720, 1440, 4320])
def test_extend_pushes_expiry_forward(app, minutes):
    with app.app_context():
        plan = _seed_plan(f"ETE{minutes}", price=30)
        start = datetime.utcnow() + timedelta(days=1)
        _seed_subscriber("ete", plan_id=plan, balance=50.0, expire_at=start)
        before = _get("ete").expire_at
        _svc().extend_time(actor="t", username="ete", minutes=minutes,
                           charge_mode="free")
        after = _get("ete").expire_at
        assert (after - before) >= timedelta(minutes=minutes - 1)


def test_extend_free_writes_no_ledger(app):
    with app.app_context():
        plan = _seed_plan("ETF", price=30)
        _seed_subscriber("etf", plan_id=plan, balance=9.0)
        _svc().extend_time(actor="t", username="etf", minutes=1440, charge_mode="free")
        assert _get("etf").balance == pytest.approx(9.0)
        assert _ledger("etf", source_type="subscriber_time_extension") == []


@pytest.mark.parametrize("minutes", [0, -1, -60])
def test_extend_rejects_non_positive_minutes(app, minutes):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan(f"ETZ{minutes}", price=30)
        _seed_subscriber("etz", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().extend_time(actor="t", username="etz", minutes=minutes,
                               charge_mode="free")


@pytest.mark.parametrize("mode", ["paid", "debt"])
def test_extend_rejects_non_positive_amount(app, mode):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan(f"ETNA{mode}", price=30)
        _seed_subscriber("etna", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().extend_time(actor="t", username="etna", minutes=720,
                               charge_mode=mode, amount=-2.0)


# ════════════════════════════════════════════════════════════════════════
# GROUP D — add_cash_balance (credit)
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("start,amount", [
    (0.0, 5.0), (10.0, 5.0), (100.0, 25.0), (3.5, 1.25), (0.0, 999.0),
])
def test_cash_balance_credits_exact_amount(app, start, amount):
    with app.app_context():
        plan = _seed_plan(f"CB{start}{amount}", price=20)
        _seed_subscriber("cb", plan_id=plan, balance=start)
        saved = _svc().add_cash_balance(actor="t", username="cb", amount=amount,
                                        currency="JOD")
        assert _get("cb").balance == pytest.approx(start + amount)
        assert float(saved.balance) == pytest.approx(start + amount)
        rows = _ledger("cb", source_type="subscriber_cash_balance")
        assert rows and rows[0]["direction"] == "credit"
        assert rows[0]["entry_type"] == "cash_balance"
        assert float(rows[0]["amount"]) == pytest.approx(amount)


def test_cash_balance_clears_negative_then_goes_positive(app):
    with app.app_context():
        plan = _seed_plan("CBN", price=20)
        _seed_subscriber("cbn", plan_id=plan, balance=-8.0)
        _svc().add_cash_balance(actor="t", username="cbn", amount=20.0, currency="JOD")
        assert _get("cbn").balance == pytest.approx(12.0)


@pytest.mark.parametrize("amount", [0.0, -1.0, -50.0])
def test_cash_balance_rejects_non_positive(app, amount):
    from app.radius.core.errors import RadiusValidationError

    with app.app_context():
        plan = _seed_plan(f"CBZ{amount}", price=20)
        _seed_subscriber("cbz", plan_id=plan, balance=10.0)
        with pytest.raises(RadiusValidationError):
            _svc().add_cash_balance(actor="t", username="cbz", amount=amount)


def test_cash_balance_net_of_settled_deduction(app):
    # gross 30, 10 used to settle loans → only 20 lands in the wallet.
    with app.app_context():
        plan = _seed_plan("CBD", price=20)
        _seed_subscriber("cbd", plan_id=plan, balance=0.0)
        saved = _svc().add_cash_balance(actor="t", username="cbd", amount=30.0,
                                        currency="JOD", settled_deduction=10.0)
        assert float(saved.balance) == pytest.approx(20.0)
        rows = _ledger("cbd", source_type="subscriber_cash_balance")
        assert rows and float(rows[0]["amount"]) == pytest.approx(20.0)


def test_cash_balance_fully_settled_credits_nothing(app):
    # gross == settled → net credit 0 → balance unchanged, no ledger row.
    with app.app_context():
        plan = _seed_plan("CBFS", price=20)
        _seed_subscriber("cbfs", plan_id=plan, balance=7.0)
        saved = _svc().add_cash_balance(actor="t", username="cbfs", amount=10.0,
                                        currency="JOD", settled_deduction=10.0)
        assert float(saved.balance) == pytest.approx(7.0)
        assert _ledger("cbfs", source_type="subscriber_cash_balance") == []


# ════════════════════════════════════════════════════════════════════════
# GROUP E — apply_payment_to_balance (settle debt)
# ════════════════════════════════════════════════════════════════════════
def test_apply_payment_settles_debt_capped_at_due(app):
    with app.app_context():
        plan = _seed_plan("AP", price=20)
        _seed_subscriber("ap", plan_id=plan, balance=-10.0)
        applied = _svc().apply_payment_to_balance(actor="t", username="ap", amount=25.0)
        # only 10 needed to reach zero; the rest is NOT over-applied here
        assert applied == pytest.approx(10.0)
        assert _get("ap").balance == pytest.approx(0.0)
        rows = _ledger("ap", source_type="payment_balance_settlement")
        assert rows and rows[0]["direction"] == "credit"
        assert rows[0]["entry_type"] == "debt_settlement"


def test_apply_payment_partial_settles_partial(app):
    with app.app_context():
        plan = _seed_plan("APP", price=20)
        _seed_subscriber("app", plan_id=plan, balance=-30.0)
        applied = _svc().apply_payment_to_balance(actor="t", username="app", amount=12.0)
        assert applied == pytest.approx(12.0)
        assert _get("app").balance == pytest.approx(-18.0)


def test_apply_payment_noop_when_balance_positive(app):
    with app.app_context():
        plan = _seed_plan("APN", price=20)
        _seed_subscriber("apn", plan_id=plan, balance=5.0)
        applied = _svc().apply_payment_to_balance(actor="t", username="apn", amount=10.0)
        assert applied == pytest.approx(0.0)
        assert _get("apn").balance == pytest.approx(5.0)
        assert _ledger("apn", source_type="payment_balance_settlement") == []


@pytest.mark.parametrize("amount", [0.0, -5.0])
def test_apply_payment_zero_or_negative_returns_zero(app, amount):
    with app.app_context():
        plan = _seed_plan(f"APZ{amount}", price=20)
        _seed_subscriber("apz", plan_id=plan, balance=-10.0)
        applied = _svc().apply_payment_to_balance(actor="t", username="apz", amount=amount)
        assert applied == pytest.approx(0.0)
        assert _get("apz").balance == pytest.approx(-10.0)


# ════════════════════════════════════════════════════════════════════════
# GROUP F — change_plan (debt on upgrade / compensate on downgrade)
# ════════════════════════════════════════════════════════════════════════
def test_change_plan_upgrade_records_debt(app):
    with app.app_context():
        old = _seed_plan("CP100", price=100)
        new = _seed_plan("CP150", price=150)
        _seed_subscriber("cp", plan_id=old, balance=0.0,
                         expire_at=datetime.utcnow() + timedelta(days=30))
        result = _svc().change_plan(actor="t", username="cp", plan_id=new,
                                    policy="higher_debt")
        assert result["debt_amount"] >= 49
        assert _get("cp").balance <= -49
        rows = _ledger("cp", source_type="subscriber_plan_change")
        assert rows and rows[0]["entry_type"] == "debt" and rows[0]["direction"] == "debit"


def test_change_plan_downgrade_compensates_days_no_debt(app):
    with app.app_context():
        old = _seed_plan("CPD150", price=150)
        new = _seed_plan("CPD100", price=100)
        _seed_subscriber("cpd", plan_id=old, balance=0.0,
                         expire_at=datetime.utcnow() + timedelta(days=30))
        result = _svc().change_plan(actor="t", username="cpd", plan_id=new,
                                    policy="lower_compensate")
        assert result["minute_delta"] > 0
        assert _get("cpd").balance == pytest.approx(0.0)


# ════════════════════════════════════════════════════════════════════════
# GROUP G — route-level (the toast must show the NEW balance)
# ════════════════════════════════════════════════════════════════════════
def test_quota_reset_route_paid_debits_balance(client, app):
    with app.app_context():
        plan = _seed_plan("RTRP", price=20)
        _seed_subscriber("rtrp", plan_id=plan, balance=20.0, used_seconds=3600)
    _auth_session(client)
    res = client.post("/admin/radius/users/rtrp/quota/reset-daily",
                      data={"_csrf_token": "money-csrf", "charge_mode": "paid",
                            "amount": "5", "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _get("rtrp").balance == pytest.approx(15.0)  # 20 − 5 (the headline bug)


def test_quota_topup_route_paid_debits_balance(client, app):
    with app.app_context():
        plan = _seed_plan("RTTP", price=20)
        _seed_subscriber("rttp", plan_id=plan, balance=40.0)
    _auth_session(client)
    res = client.post("/admin/radius/users/rttp/quota/topup",
                      data={"_csrf_token": "money-csrf", "quota_mb": "500",
                            "charge_mode": "paid", "amount": "8", "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _get("rttp").balance == pytest.approx(32.0)


def test_extend_route_paid_debits_balance(client, app):
    with app.app_context():
        plan = _seed_plan("RTEX", price=30)
        _seed_subscriber("rtex", plan_id=plan, balance=25.0)
    _auth_session(client)
    res = client.post("/admin/radius/users/rtex/extend",
                      data={"_csrf_token": "money-csrf", "minutes": "720",
                            "charge_mode": "paid", "amount": "4", "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _get("rtex").balance == pytest.approx(21.0)


def test_balance_add_route_credits_balance(client, app):
    with app.app_context():
        plan = _seed_plan("RTBA", price=20)
        _seed_subscriber("rtba", plan_id=plan, balance=10.0)
    _auth_session(client)
    res = client.post("/admin/radius/users/rtba/balance/add",
                      data={"_csrf_token": "money-csrf", "amount": "15",
                            "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _get("rtba").balance == pytest.approx(25.0)


def test_quota_reset_route_free_keeps_balance(client, app):
    with app.app_context():
        plan = _seed_plan("RTRF", price=20)
        _seed_subscriber("rtrf", plan_id=plan, balance=20.0, used_seconds=3600)
    _auth_session(client)
    res = client.post("/admin/radius/users/rtrf/quota/reset-daily",
                      data={"_csrf_token": "money-csrf", "charge_mode": "free"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _get("rtrf").balance == pytest.approx(20.0)
        assert _get("rtrf").used_seconds == 0


# ════════════════════════════════════════════════════════════════════════
# GROUP H — multi-tenant isolation
# ════════════════════════════════════════════════════════════════════════
def _ensure_second_tenant() -> int:
    """Create a second tenant and return its id (FK target for tenant-2 rows)."""
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import tenants_repo

    t = tenants_repo.create_tenant(Tenant(
        id=None, slug="t2-audit", name="Tenant Two", display_name="Tenant Two",
        currency="JOD",
    ))
    return int(t.id)


def _seed_tenant2_subscriber(username, balance, tenant_id):
    plan = _seed_plan(f"T2{username}", price=20, tenant_id=tenant_id)
    _seed_subscriber(username, plan_id=plan, balance=balance, tenant_id=tenant_id)


@pytest.mark.parametrize("op", ["reset", "quota", "extend", "cash"])
def test_money_ops_do_not_cross_tenants(app, op):
    with app.app_context():
        plan1 = _seed_plan(f"ISO1{op}", price=20)
        _seed_subscriber("shared", plan_id=plan1, balance=30.0, tenant_id=1)
        t2 = _ensure_second_tenant()
        _seed_tenant2_subscriber("shared", balance=30.0, tenant_id=t2)
        svc = _svc()
        if op == "reset":
            svc.reset_daily_quota(actor="t", username="shared",
                                  charge_mode="paid", amount=5.0, currency="JOD")
        elif op == "quota":
            svc.add_quota(actor="t", username="shared", quota_mb=100,
                          charge_mode="paid", amount=5.0, currency="JOD")
        elif op == "extend":
            svc.extend_time(actor="t", username="shared", minutes=60,
                            charge_mode="paid", amount=5.0, currency="JOD")
        else:
            svc.add_cash_balance(actor="t", username="shared", amount=5.0,
                                 currency="JOD")
        # tenant 1 moved; tenant 2's identically-named subscriber untouched
        assert _get("shared", tenant_id=t2).balance == pytest.approx(30.0)
        assert _ledger("shared", tenant_id=t2) == []


def test_ledger_rows_carry_correct_tenant_and_subscriber(app):
    with app.app_context():
        plan = _seed_plan("LT", price=20)
        sub = _seed_subscriber("lt", plan_id=plan, balance=20.0)
        _svc().reset_daily_quota(actor="auditor", username="lt",
                                 charge_mode="paid", amount=5.0, currency="JOD")
        rows = _ledger("lt", source_type="subscriber_daily_quota_reset")
        assert rows
        assert rows[0]["tenant_id"] == 1
        assert rows[0]["subscriber_id"] == sub.id
        assert rows[0]["operator"] == "auditor"


# ════════════════════════════════════════════════════════════════════════
# GROUP I — currency + actor attribution consistency
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("currency", ["JOD", "USD", "ILS", "EUR"])
def test_paid_charge_records_requested_currency(app, currency):
    with app.app_context():
        plan = _seed_plan(f"CUR{currency}", price=20)
        _seed_subscriber("cur", plan_id=plan, balance=50.0)
        _svc().add_quota(actor="t", username="cur", quota_mb=100,
                         charge_mode="paid", amount=3.0, currency=currency)
        rows = _ledger("cur", source_type="subscriber_quota_topup")
        assert rows and rows[0]["currency"] == currency.upper()[:8]


@pytest.mark.parametrize("actor", ["owner", "manager7", "system:audit"])
def test_ledger_records_actor_as_operator(app, actor):
    with app.app_context():
        plan = _seed_plan(f"ACT{actor}", price=20)
        _seed_subscriber("act", plan_id=plan, balance=50.0)
        _svc().extend_time(actor=actor, username="act", minutes=60,
                           charge_mode="paid", amount=2.0, currency="JOD")
        rows = _ledger("act", source_type="subscriber_time_extension")
        assert rows and rows[0]["operator"] == actor
