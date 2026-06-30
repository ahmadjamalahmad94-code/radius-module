from __future__ import annotations

from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = tmp_path / "subscriber_quick_actions.db"
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
        sess["admin_user"] = "quick_admin"
        sess["admin_name"] = "Quick Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "quick-csrf"


def _seed_plan(name: str, *, price: float, days: int = 30) -> int:
    from app.radius.db.connection import db

    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price,
            currency, enabled, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            1,
            name,
            days * 24 * 60,
            days,
            price,
            "JOD",
            1,
            datetime.utcnow().isoformat(),
            datetime.utcnow().isoformat(),
        ),
    )
    return int(cur.lastrowid)


def _seed_subscriber(username: str, *, plan_id: int, expire_at: datetime):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    return subscribers_repo.upsert_subscriber(
        Subscriber(
            id=None,
            tenant_id=1,
            username=username,
            password="secret",
            plan_id=plan_id,
            full_name="Quick User",
            mobile="0599000000",
            status="enabled",
            expire_at=expire_at,
        )
    )


def test_change_plan_to_cheaper_offer_can_compensate_days(app):
    with app.app_context():
        old_plan = _seed_plan("150 Monthly", price=150)
        new_plan = _seed_plan("100 Monthly", price=100)
        _seed_subscriber(
            "cheap_case",
            plan_id=old_plan,
            expire_at=datetime.utcnow() + timedelta(days=30),
        )

        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        result = get_users_service().change_plan(
            actor="tester",
            username="cheap_case",
            plan_id=new_plan,
            policy="lower_compensate",
        )
        updated = subscribers_repo.get_subscriber(1, "cheap_case")

        assert updated.plan_id == new_plan
        assert result["minute_delta"] > 14 * 24 * 60
        assert updated.expire_at > datetime.utcnow() + timedelta(days=44)
        assert updated.balance == 0


def test_change_plan_to_expensive_offer_records_debt_for_same_days(app):
    with app.app_context():
        old_plan = _seed_plan("100 Monthly", price=100)
        new_plan = _seed_plan("150 Monthly", price=150)
        _seed_subscriber(
            "debt_case",
            plan_id=old_plan,
            expire_at=datetime.utcnow() + timedelta(days=30),
        )

        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        result = get_users_service().change_plan(
            actor="tester",
            username="debt_case",
            plan_id=new_plan,
            policy="higher_debt",
        )
        updated = subscribers_repo.get_subscriber(1, "debt_case")
        debt = db().execute(
            """
            SELECT * FROM accounting_ledger_entries
            WHERE tenant_id=1 AND username='debt_case' AND entry_type='debt'
            """
        ).fetchone()

        assert updated.plan_id == new_plan
        assert result["debt_amount"] >= 49
        assert updated.balance <= -49
        assert debt is not None
        assert debt["source_type"] == "subscriber_plan_change"


def test_send_sms_queues_manual_sms_for_selected_subscriber(app):
    with app.app_context():
        plan = _seed_plan("SMS Plan", price=20)
        sub = _seed_subscriber(
            "sms_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )

        from app.radius.db.connection import db
        from app.radius.services.users import get_users_service

        result = get_users_service().send_sms(
            actor="tester",
            username="sms_case",
            message="Hello from quick actions",
        )
        notification = db().execute(
            """
            SELECT * FROM message_notifications
            WHERE tenant_id=1 AND recipient_id=? AND channel='sms'
            """,
            (sub.id,),
        ).fetchone()
        delivery = db().execute(
            """
            SELECT d.* FROM message_deliveries d
            JOIN message_notifications n ON n.id=d.notification_id
            WHERE n.tenant_id=1 AND n.recipient_id=? AND n.channel='sms'
            """,
            (sub.id,),
        ).fetchone()

        assert result["queued_count"] == 1
        assert notification is not None
        assert notification["body"] == "Hello from quick actions"
        assert delivery is not None


def test_reset_daily_quota_clears_subscriber_usage_counters(app):
    with app.app_context():
        plan = _seed_plan("Quota Reset Plan", price=20)
        _seed_subscriber(
            "quota_reset_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )

        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        db().execute(
            """
            UPDATE subscribers
            SET used_seconds=3600, used_bytes_in=1048576, used_bytes_out=2097152
            WHERE tenant_id=1 AND username='quota_reset_case'
            """
        )
        get_users_service().reset_daily_quota(actor="tester", username="quota_reset_case")
        updated = subscribers_repo.get_subscriber(1, "quota_reset_case")

        assert updated.used_seconds == 0
        assert updated.used_bytes_in == 0
        assert updated.used_bytes_out == 0


def test_add_quota_can_record_debt_and_enable_quota_limit(app):
    with app.app_context():
        plan = _seed_plan("Quota Topup Plan", price=20)
        _seed_subscriber(
            "quota_debt_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )

        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        saved = get_users_service().add_quota(
            actor="tester",
            username="quota_debt_case",
            quota_mb=512,
            quota_target="combined",
            charge_mode="debt",
            amount=5.5,
            currency="JOD",
            notes="quick debt quota",
        )
        updated = subscribers_repo.get_subscriber(1, "quota_debt_case")
        debt = db().execute(
            """
            SELECT * FROM accounting_ledger_entries
            WHERE tenant_id=1 AND username='quota_debt_case' AND entry_type='debt'
            """
        ).fetchone()

        assert saved.combined_quota_mb == 512
        assert updated.quota_limit_enabled is True
        assert updated.balance == -5.5
        assert debt is not None
        assert debt["source_type"] == "subscriber_quota_topup"


def test_add_cash_balance_increases_balance_and_records_ledger(app):
    with app.app_context():
        plan = _seed_plan("Cash Balance Plan", price=20)
        _seed_subscriber(
            "cash_balance_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )

        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        get_users_service().add_cash_balance(
            actor="tester",
            username="cash_balance_case",
            amount=12.25,
            currency="JOD",
            notes="quick cash balance",
        )
        updated = subscribers_repo.get_subscriber(1, "cash_balance_case")
        ledger = db().execute(
            """
            SELECT * FROM accounting_ledger_entries
            WHERE tenant_id=1 AND username='cash_balance_case' AND entry_type='cash_balance'
            """
        ).fetchone()

        assert updated.balance == 12.25
        assert ledger is not None
        assert ledger["source_type"] == "subscriber_cash_balance"


def test_add_cash_balance_credits_net_of_settled_loans(app):
    """«إضافة رصيد» with a loan deduction credits the wallet with amount − settled,
    and records the cash_balance ledger at the NET figure."""
    with app.app_context():
        plan = _seed_plan("Net Balance Plan", price=100)
        _seed_subscriber(
            "net_balance_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )

        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        saved = get_users_service().add_cash_balance(
            actor="tester",
            username="net_balance_case",
            amount=50.0,
            currency="JOD",
            settled_deduction=9.0,
        )
        assert saved.balance == 41.0  # 50 received − 9 used to settle a loan
        updated = subscribers_repo.get_subscriber(1, "net_balance_case")
        assert updated.balance == 41.0
        ledger = db().execute(
            """
            SELECT amount FROM accounting_ledger_entries
            WHERE tenant_id=1 AND username='net_balance_case' AND entry_type='cash_balance'
            """
        ).fetchone()
        assert ledger is not None and float(ledger["amount"]) == 41.0


def test_balance_add_route_settles_open_loan_and_credits_net(client, app):
    """End-to-end: POST balance/add with a «خصم» loan choice settles that loan and
    credits the wallet with the remainder (رصيد المحفظة مضاف بعد الخصومات)."""
    with app.app_context():
        plan = _seed_plan("Route Net Plan", price=100)
        _seed_subscriber(
            "route_net_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )
        from app.radius.services.accounting import AccountingService

        loan = AccountingService(tenant_id=1).create_loan(
            {"username": "route_net_case", "days": "3", "amount": "30", "currency": "JOD"},
            actor="tester",
        )
        loan_id = int(loan["id"])

    _auth_session(client)
    res = client.post(
        "/admin/radius/users/route_net_case/balance/add",
        data={
            "_csrf_token": "quick-csrf",
            "amount": "50",
            "currency": "JOD",
            "loan_actions": f'[{{"loan_id": {loan_id}, "action": "settle"}}]',
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}

    with app.app_context():
        from app.radius.db.repos import accounting_repo, subscribers_repo

        updated = subscribers_repo.get_subscriber(1, "route_net_case")
        assert updated.balance == 20.0  # 50 − 30 settled
        settled_loan = accounting_repo.get_loan(1, loan_id)
        assert settled_loan["status"] == "settled"


def test_apply_payment_to_balance_clears_debt_capped_at_due(app):
    """apply_payment_to_balance credits a NEGATIVE balance toward zero, never past
    it, and is a no-op once the debt is gone."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        plan = _seed_plan("Debt Settle Plan", price=100)
        _seed_subscriber(
            "debt_settle_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )
        with transaction() as conn:
            conn.execute(
                "UPDATE subscribers SET balance = -14.53 WHERE tenant_id=1 AND username='debt_settle_case'"
            )

        svc = get_users_service()
        applied = svc.apply_payment_to_balance(
            actor="tester", username="debt_settle_case", amount=20.0
        )
        assert applied == 14.53  # capped to the debt, not the full 20
        assert round(subscribers_repo.get_subscriber(1, "debt_settle_case").balance, 2) == 0.0
        # debt cleared → further attempts settle nothing
        assert svc.apply_payment_to_balance(
            actor="tester", username="debt_settle_case", amount=5.0
        ) == 0.0


def test_payment_route_settles_negative_balance_and_reduces_time(client, app):
    """End-to-end: a payment with settle_balance=1 diverts part of the cash to clear
    the negative-balance debt (رصيد سالب) and only the remainder buys time."""
    with app.app_context():
        from app.radius.db.connection import transaction

        plan = _seed_plan("Pay Debt Plan", price=100)  # 30 days @ 100
        _seed_subscriber(
            "pay_debt_case",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )
        with transaction() as conn:
            conn.execute(
                "UPDATE subscribers SET balance = -30 WHERE tenant_id=1 AND username='pay_debt_case'"
            )

    _auth_session(client)
    res = client.post(
        "/admin/radius/users/pay_debt_case/payments",
        data={
            "_csrf_token": "quick-csrf",
            "amount": "50",
            "currency": "JOD",
            "method": "cash",
            "settle_balance": "1",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}

    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo

        updated = subscribers_repo.get_subscriber(1, "pay_debt_case")
        assert round(updated.balance, 2) == 0.0  # 30 of the 50 cleared the debt

        # full 50 still recorded as a payment (income), plus a netting debt_settlement
        pay = db().execute(
            "SELECT amount FROM accounting_ledger_entries WHERE tenant_id=1 AND username='pay_debt_case' AND entry_type='payment'"
        ).fetchone()
        assert pay is not None and float(pay["amount"]) == 50.0
        settle = db().execute(
            "SELECT amount FROM accounting_ledger_entries WHERE tenant_id=1 AND username='pay_debt_case' AND entry_type='debt_settlement'"
        ).fetchone()
        assert settle is not None and round(float(settle["amount"]), 2) == 30.0


def test_subscribers_page_exposes_only_implemented_quick_actions(client, app):
    with app.app_context():
        plan = _seed_plan("Quick Plan", price=120)
        _seed_subscriber(
            "quick_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=10),
        )
    _auth_session(client)

    res = client.get("/admin/radius/subscribers")
    html = res.get_data(as_text=True)
    quick_panel = html.split('data-users-quick-panel', 1)[1].split('data-usq-modal="plan"', 1)[0]

    assert res.status_code == 200
    assert "تغيير العرض" in quick_panel
    assert "إضافة وقت" in quick_panel
    assert "إرسال رسالة قصيرة" in quick_panel
    assert "استعادة الكوتة اليومية" in quick_panel
    assert "إضافة كوتة" in quick_panel
    # «إضافة رصيد نقدي» consolidated away — only the account-applying «دفعة نقدية» remains.
    assert "إضافة رصيد نقدي" not in quick_panel
    assert "دفعة نقدية" in quick_panel
    assert "إضافة سلفة" in quick_panel
    assert "فصل الاتصال" in quick_panel
    assert "طباعة آخر فاتورة" not in quick_panel
    assert "إنشاء عقد" not in quick_panel
    assert "ضغط البيانات" not in quick_panel


def test_reset_daily_quota_paid_debits_balance_and_records_charge(app):
    """REGRESSION (owner-confirmed): a PAID daily-quota restore MUST debit the
    charged amount from the subscriber balance — it must never report «مدفوعة
    بقيمة X» while leaving the balance untouched. Starting from 20.00, a paid
    restore of 3.00 leaves 17.00, with a debit ledger row for the charge."""
    with app.app_context():
        plan = _seed_plan("Quota Reset Paid Plan", price=20)
        _seed_subscriber(
            "quota_reset_paid",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        db().execute(
            "UPDATE subscribers SET used_seconds=3600, used_bytes_in=1048576, "
            "used_bytes_out=2097152, balance=20 WHERE tenant_id=1 AND username='quota_reset_paid'"
        )
        saved = get_users_service().reset_daily_quota(
            actor="tester", username="quota_reset_paid",
            charge_mode="paid", amount=3.0, currency="JOD", notes="paid reset",
        )
        updated = subscribers_repo.get_subscriber(1, "quota_reset_paid")
        entry = db().execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='quota_reset_paid' AND source_type='subscriber_daily_quota_reset'"
        ).fetchone()

        # counters cleared, balance DEBITED by the charge, ledger row is a debit
        assert updated.used_seconds == 0 and updated.used_bytes_in == 0
        assert updated.balance == 17.0          # 20.00 − 3.00
        assert float(saved.balance) == 17.0     # returned object reflects the new balance
        assert entry is not None
        assert entry["entry_type"] == "quota_topup"
        assert entry["direction"] == "debit"
        assert float(entry["amount"]) == 3.0


def test_reset_daily_quota_debt_reduces_balance_and_records_debt(app):
    with app.app_context():
        plan = _seed_plan("Quota Reset Debt Plan", price=20)
        _seed_subscriber(
            "quota_reset_debt",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        db().execute(
            "UPDATE subscribers SET used_seconds=120, balance=0 "
            "WHERE tenant_id=1 AND username='quota_reset_debt'"
        )
        get_users_service().reset_daily_quota(
            actor="tester", username="quota_reset_debt",
            charge_mode="debt", amount=4.5, currency="JOD",
        )
        updated = subscribers_repo.get_subscriber(1, "quota_reset_debt")
        debt = db().execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='quota_reset_debt' AND source_type='subscriber_daily_quota_reset'"
        ).fetchone()

        assert updated.used_seconds == 0
        assert updated.balance == -4.5
        assert debt is not None
        assert debt["entry_type"] == "debt"
        assert debt["direction"] == "debit"


def test_quota_reset_route_reads_charge_mode(client, app):
    with app.app_context():
        plan = _seed_plan("Quota Reset Route Plan", price=20)
        _seed_subscriber(
            "quota_reset_route",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )
    _auth_session(client)
    res = client.post(
        "/admin/radius/users/quota_reset_route/quota/reset-daily",
        data={"_csrf_token": "quick-csrf", "charge_mode": "debt", "amount": "6"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        updated = subscribers_repo.get_subscriber(1, "quota_reset_route")
        assert float(updated.balance) == -6.0


def test_extend_time_debt_reduces_balance_and_records_ledger(app):
    with app.app_context():
        plan = _seed_plan("Extend Debt Plan", price=30)
        _seed_subscriber(
            "extend_debt",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=2),
        )
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        db().execute("UPDATE subscribers SET balance=0 WHERE tenant_id=1 AND username='extend_debt'")
        get_users_service().extend_time(
            actor="tester", username="extend_debt", minutes=1440,
            charge_mode="debt", amount=2.5, currency="JOD",
        )
        updated = subscribers_repo.get_subscriber(1, "extend_debt")
        debt = db().execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='extend_debt' AND source_type='subscriber_time_extension'"
        ).fetchone()

        assert updated.balance == -2.5
        assert debt is not None and debt["entry_type"] == "debt" and debt["direction"] == "debit"


def test_extend_time_paid_debits_balance_and_records_charge(app):
    """REGRESSION (owner-confirmed): a PAID time extension MUST debit the charged
    amount from the subscriber balance. Starting from 10.00, a paid extension of
    1.50 leaves 8.50 with a debit ledger row."""
    with app.app_context():
        plan = _seed_plan("Extend Paid Plan", price=30)
        _seed_subscriber(
            "extend_paid",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=2),
        )
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        db().execute("UPDATE subscribers SET balance=10 WHERE tenant_id=1 AND username='extend_paid'")
        saved = get_users_service().extend_time(
            actor="tester", username="extend_paid", minutes=720,
            charge_mode="paid", amount=1.5, currency="JOD",
        )
        updated = subscribers_repo.get_subscriber(1, "extend_paid")
        entry = db().execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='extend_paid' AND source_type='subscriber_time_extension'"
        ).fetchone()

        assert updated.balance == 8.5          # 10.00 − 1.50
        assert float(saved.balance) == 8.5
        assert entry is not None and entry["entry_type"] == "time_extension" and entry["direction"] == "debit"


def test_extend_time_free_is_backward_compatible(app):
    with app.app_context():
        plan = _seed_plan("Extend Free Plan", price=30)
        _seed_subscriber(
            "extend_free",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=2),
        )
        from app.radius.db.connection import db
        from app.radius.services.users import get_users_service

        get_users_service().extend_time(actor="tester", username="extend_free", minutes=1440)
        entry = db().execute(
            "SELECT COUNT(*) AS c FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='extend_free' AND source_type='subscriber_time_extension'"
        ).fetchone()
        assert int(entry["c"]) == 0  # free extend posts no ledger entry


def test_add_time_modal_has_pricing_and_billing(client, app):
    with app.app_context():
        plan = _seed_plan("Extend UI Plan", price=30)
        _seed_subscriber(
            "extend_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=2),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    extend_modal = html.split('data-usq-modal="extend"', 1)[1].split('data-usq-modal="sms"', 1)[0]
    assert 'name="charge_mode"' in extend_modal
    assert "data-usq-extend-amount" in extend_modal
    assert "مدفوع — دين" in extend_modal


def test_plan_minutes_falls_back_to_validity_for_quota_plans(client, app):
    # A quota/data plan has duration_minutes=0 but validity_days>0. The row's
    # time-basis (data-plan-minutes) must fall back to validity_days×1440 so the
    # price↔time math (loan/add-time/coverage) computes instead of yielding 0.
    with app.app_context():
        from app.radius.db.connection import db

        cur = db().execute(
            """
            INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                     price, currency, enabled, created_at, updated_at)
            VALUES(1, '5GB Quota', 0, 30, 3, 'ILS', 1, ?, ?)
            """,
            (datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
        )
        pid = int(cur.lastrowid)
        _seed_subscriber(
            "quota_plan_user",
            plan_id=pid,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    row = html.split('data-username="quota_plan_user"', 1)[1].split("</tr>", 1)[0]
    assert 'data-plan-minutes="43200"' in row  # 30 days × 1440 (validity fallback)


def test_add_time_modal_is_days_only_no_hours(client, app):
    with app.app_context():
        plan = _seed_plan("Days Only Plan", price=30)
        _seed_subscriber(
            "daysonly_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    extend_modal = html.split('data-usq-modal="extend"', 1)[1].split('data-usq-modal="sms"', 1)[0]
    assert "data-usq-days" in extend_modal       # days input kept
    assert "data-usq-hours" not in extend_modal  # hours input removed


def test_loan_modal_ajax_success_returns_json_stays_on_page(client, app):
    with app.app_context():
        plan = _seed_plan("Loan AJAX Plan", price=30)
        _seed_subscriber(
            "loan_ajax_ok",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    res = client.post(
        "/admin/radius/users/loan_ajax_ok/loans",
        data={"_csrf_token": "quick-csrf", "days": "3", "price_from_days": "1", "_loan_kind": "debt"},
        headers={"X-Requested-With": "fetch"},
    )
    # AJAX → JSON (no redirect to the loans page)
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True and body.get("message")


def test_loan_modal_ajax_error_returns_json_400(client, app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MAX_LOAN_HOURS", "1")
    with app.app_context():
        plan = _seed_plan("Loan AJAX Err Plan", price=30)
        _seed_subscriber(
            "loan_ajax_err",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    res = client.post(
        "/admin/radius/users/loan_ajax_err/loans",
        data={"_csrf_token": "quick-csrf", "days": "5"},  # 5 days >> 1h cap → validation error
        headers={"X-Requested-With": "fetch"},
    )
    assert res.status_code == 400
    body = res.get_json()
    assert body["ok"] is False and body.get("error")


def test_loan_modal_has_inline_message_element(client, app):
    with app.app_context():
        plan = _seed_plan("Loan Msg Plan", price=30)
        _seed_subscriber(
            "loan_msg_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert "data-usq-loan-msg" in html  # inline success/error target (no navigation)


def test_sms_modal_has_ready_templates(client, app):
    with app.app_context():
        plan = _seed_plan("SMS Tpl Plan", price=30)
        _seed_subscriber(
            "sms_tpl_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    sms_modal = html.split('data-usq-modal="sms"', 1)[1].split('data-usq-modal="quota"', 1)[0]
    assert "data-sms-tpl" in sms_modal     # clickable ready templates
    assert "قوالب جاهزة" in sms_modal
    assert "تذكير انتهاء" in sms_modal


def test_quota_modal_uses_mb_gb_unit_picker(client, app):
    with app.app_context():
        plan = _seed_plan("Quota Picker Plan", price=30)
        _seed_subscriber(
            "quota_picker_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    quota_modal = html.split('data-usq-modal="quota"', 1)[1].split('data-usq-modal="quota-reset"', 1)[0]
    assert 'class="ui-unit"' in quota_modal   # canonical value+unit picker
    assert "GB" in quota_modal                 # MB/GB/TB selectable
    assert 'name="quota_mb"' in quota_modal    # still submits base MB


def test_payment_and_loan_modals_are_real_no_preview(client, app):
    with app.app_context():
        plan = _seed_plan("Real Exec Plan", price=30)
        _seed_subscriber(
            "real_exec_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    # «رسمي»: rounding dropdown + dry-run preview removed from the quick-action modals
    assert 'name="rounding_mode"' not in html
    assert 'name="dry_run"' not in html
    # payment + loan both apply for real (hidden apply_to_radius=1)
    assert html.count('name="apply_to_radius" value="1"') >= 2


def test_open_loans_endpoint_is_wired(client, app):
    with app.app_context():
        plan = _seed_plan("Open Loans Plan", price=30)
        _seed_subscriber(
            "openloans_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    res = client.get("/admin/radius/users/openloans_ui/open-loans")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert isinstance(data["loans"], list)


def test_payment_modal_has_interactive_loan_settlement(client, app):
    with app.app_context():
        plan = _seed_plan("Payment UI Plan", price=30)
        _seed_subscriber(
            "payment_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    pay_modal = html.split('data-usq-modal="payment"', 1)[1].split('data-usq-modal="loan"', 1)[0]
    assert "data-usq-loans-box" in pay_modal      # interactive open-loans container
    assert 'name="loan_actions"' in pay_modal     # hidden field the route reads
    assert "data-usq-coverage" in pay_modal       # live «إجمالي الأيام» line


def test_quota_reset_uses_floating_modal_not_native_confirm(client, app):
    with app.app_context():
        plan = _seed_plan("Quota Reset UI Plan", price=20)
        _seed_subscriber(
            "quota_reset_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=7),
        )
    _auth_session(client)
    res = client.get("/admin/radius/subscribers")
    html = res.get_data(as_text=True)

    assert res.status_code == 200
    # the floating modal exists and both triggers open it
    assert 'data-usq-modal="quota-reset"' in html
    assert 'data-usq-open="quota-reset"' in html
    assert 'data-urow-open="quota-reset"' in html
    # daily quota is surfaced + the free/paid/debt billing choice is present
    assert "الكوتة اليومية" in html
    assert 'name="charge_mode"' in html
    # the old native-confirm form for daily-quota-reset is gone
    assert 'data-usq-confirm="استعادة الكوتة اليومية' not in html
    assert 'data-urow-confirm="استعادة الكوتة اليومية' not in html


# ── «إضافة رصيد نقدي» modal fixes (selected-count, sub-day estimate, wallet-only) ──

def _seed_subday_plan(name: str, *, price: float, minutes: int) -> int:
    """A sub-day plan (e.g. «نصف ساعة» = 30min) — duration in minutes, no day window."""
    from app.radius.db.connection import db

    cur = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                 price, currency, enabled, created_at, updated_at)
        VALUES(1, ?, ?, 0, ?, 'ILS', 1, ?, ?)
        """,
        (name, minutes, price, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
    )
    return int(cur.lastrowid)


def test_balance_multi_count_box_has_hidden_css_guard(client, app):
    """Regression: `.usq-field`/`.usq-current` carry `display:flex/grid`, which
    overrides the bare `hidden` attribute — leaving «المشتركون المحدَّدون (0)»
    visible in single mode. The scoped `[hidden]{display:none}` rules must exist
    so the JS `.hidden` toggle actually hides/shows the right box."""
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert ".usq-field[hidden]{ display:none; }" in html
    assert ".usq-current[hidden]{ display:none; }" in html


def test_balance_estimate_uses_duration_units_not_days_only(client, app):
    """The renewal estimate must express the plan period in the right unit
    (periods/hours/minutes) so a «نصف ساعة» plan no longer rounds to «~0 يوم».
    The old days-only formula `(minutes / 1440)` must be gone, replaced by the
    arDuration/arPeriods helpers."""
    with app.app_context():
        plan = _seed_subday_plan("نصف ساعة", price=50, minutes=30)
        _seed_subscriber(
            "ahmad94",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=1),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    # The sub-day plan exposes its real minute basis for the estimate math.
    row = html.split('data-username="ahmad94"', 1)[1].split("</tr>", 1)[0]
    assert 'data-plan-minutes="30"' in row
    # New duration-aware helpers present; misleading days-only math removed.
    assert "function arDuration(" in html
    assert "function arPeriods(" in html
    assert "(credited / price) * (minutes / 1440)" not in html
    assert "نصف ساعة" in html  # arDuration nice-case label


def test_balance_helper_text_is_wallet_only(client, app):
    """Bug 3: adding cash credits the WALLET only — it must NOT promise renewal.
    The helper must say so explicitly and drop the old auto-renew wording."""
    with app.app_context():
        plan = _seed_plan("Wallet Only Plan", price=20)
        _seed_subscriber(
            "wallet_only_ui",
            plan_id=plan,
            expire_at=datetime.utcnow() + timedelta(days=5),
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert "لا يجدّد الاشتراك ولا يمدّد تاريخ الانتهاء تلقائيًا" in html
    # old misleading static hint is gone
    assert "يُستخدم عند التجديد التلقائي أو لتغطية الباقة." not in html


def test_add_cash_balance_does_not_change_expiry(app):
    """Bug 3 behavior: credit lands in the wallet; the subscription expiry is
    untouched (renewal consumes the balance separately)."""
    with app.app_context():
        from app.radius.db.repos import subscribers_repo
        from app.radius.services.users import get_users_service

        expire = datetime.utcnow() + timedelta(days=3)
        plan = _seed_plan("Expiry Untouched Plan", price=30)
        _seed_subscriber("expiry_untouched", plan_id=plan, expire_at=expire)
        before = subscribers_repo.get_subscriber(1, "expiry_untouched")

        get_users_service().add_cash_balance(
            actor="tester", username="expiry_untouched", amount=60.0, currency="ILS",
        )
        after = subscribers_repo.get_subscriber(1, "expiry_untouched")
        assert float(after.balance or 0) == 60.0
        # expiry must be identical — wallet credit never extends the subscription
        assert after.expire_at == before.expire_at


def test_balance_add_bulk_credits_each_selected_subscriber_full_amount(client, app):
    """The bulk path credits the full amount to EACH selected subscriber's wallet
    on its own (not split), reusing the canonical add_cash_balance path."""
    with app.app_context():
        plan = _seed_plan("Bulk Credit Plan", price=40)
        for name in ("bulk_a", "bulk_b", "bulk_c"):
            _seed_subscriber(
                name, plan_id=plan, expire_at=datetime.utcnow() + timedelta(days=5)
            )
    _auth_session(client)
    res = client.post(
        "/admin/radius/users/balance/add-bulk",
        data={
            "_csrf_token": "quick-csrf",
            "amount": "25",
            "currency": "ILS",
            "usernames": ["bulk_a", "bulk_b", "bulk_c"],
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        for name in ("bulk_a", "bulk_b", "bulk_c"):
            sub = subscribers_repo.get_subscriber(1, name)
            assert float(sub.balance or 0) == 25.0  # full amount each, not split


# ── Broadened scope: shared estimate helper across modals + consolidation ──────

def test_period_estimate_helper_is_shared_across_modals(client, app):
    """Both «إضافة رصيد» and «تسجيل دفعة» route their money→time estimate through
    the single coverageCover() helper, so neither shows «≈ 0 يوم» for a sub-day
    plan. The old per-modal days-only formulas must be gone."""
    with app.app_context():
        plan = _seed_subday_plan("نصف ساعة", price=50, minutes=30)
        _seed_subscriber(
            "shared_est", plan_id=plan, expire_at=datetime.utcnow() + timedelta(days=1)
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert html.count("function coverageCover(") == 1     # one shared source
    # both estimate consumers route through the shared helper
    assert html.count("coverageCover(") >= 3              # 1 def + balance + payment
    # old per-modal days-only estimate formulas removed (balance + payment)
    assert "(credited / price) * (minutes / 1440)" not in html
    assert "(timeAmount / price) * (planMin / 1440)" not in html
    # the kept payment modal's static coverage placeholder now speaks of the
    # duration ADDED TO THE ACCOUNT (not «عدد الأيام»)
    assert "أدخل المبلغ لعرض المدّة التي يُضيفها للحساب." in html


def test_cash_balance_action_hidden_and_payment_kept(client, app):
    """Consolidation: «إضافة رصيد نقدي» entry points removed from the UI; the
    account-applying «تسجيل دفعة نقدية» remains (quick-panel + per-row), and the
    kept action is wired to APPLY to RADIUS (extend the expiry)."""
    with app.app_context():
        plan = _seed_plan("Consolidate Plan", price=30)
        _seed_subscriber(
            "consol_ui", plan_id=plan, expire_at=datetime.utcnow() + timedelta(days=5)
        )
    _auth_session(client)
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert 'data-usq-open="balance"' not in html       # quick-panel entry gone
    assert 'data-urow-open="balance"' not in html       # per-row entry gone
    assert 'data-usq-open="payment"' in html            # payment kept
    assert 'data-urow-open="payment"' in html
    assert 'name="apply_to_radius" value="1"' in html   # kept action extends


def test_payment_apply_to_radius_extends_subscription(app):
    """The kept action actually applies: a payment with apply_to_radius converts
    cash → earned minutes and extends the subscription (unlike wallet-only credit)."""
    with app.app_context():
        from app.radius.services.accounting import AccountingService

        plan = _seed_plan("Extend Pay Plan", price=30, days=30)
        _seed_subscriber(
            "pay_extend", plan_id=plan, expire_at=datetime.utcnow() + timedelta(days=2)
        )
        res = AccountingService(tenant_id=1).create_payment(
            {"username": "pay_extend", "amount": "30", "currency": "JOD",
             "apply_to_radius": True},
            actor="tester",
        )
        assert res["proportional_activation"]["earned_minutes"] > 0
        assert res["activation_result"]["applied_to_radius"] is True
