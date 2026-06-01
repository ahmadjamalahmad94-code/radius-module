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
    assert "إرسال SMS" in quick_panel
    assert "استعادة الكوتة اليومية" in quick_panel
    assert "إضافة كوتة" in quick_panel
    assert "إضافة رصيد نقدي" in quick_panel
    assert "دفعة نقدية" in quick_panel
    assert "إضافة سلفة" in quick_panel
    assert "فصل الاتصال" in quick_panel
    assert "طباعة آخر فاتورة" not in quick_panel
    assert "إنشاء عقد" not in quick_panel
    assert "ضغط البيانات" not in quick_panel


def test_reset_daily_quota_paid_records_credit_ledger_without_touching_balance(app):
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
            "used_bytes_out=2097152, balance=0 WHERE tenant_id=1 AND username='quota_reset_paid'"
        )
        get_users_service().reset_daily_quota(
            actor="tester", username="quota_reset_paid",
            charge_mode="paid", amount=3.0, currency="JOD", notes="paid reset",
        )
        updated = subscribers_repo.get_subscriber(1, "quota_reset_paid")
        entry = db().execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='quota_reset_paid' AND source_type='subscriber_daily_quota_reset'"
        ).fetchone()

        # counters cleared, balance unchanged (cash), ledger credited
        assert updated.used_seconds == 0 and updated.used_bytes_in == 0
        assert updated.balance == 0
        assert entry is not None
        assert entry["entry_type"] == "quota_topup"
        assert entry["direction"] == "credit"
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


def test_extend_time_paid_records_credit_without_touching_balance(app):
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

        db().execute("UPDATE subscribers SET balance=0 WHERE tenant_id=1 AND username='extend_paid'")
        get_users_service().extend_time(
            actor="tester", username="extend_paid", minutes=720,
            charge_mode="paid", amount=1.5, currency="JOD",
        )
        updated = subscribers_repo.get_subscriber(1, "extend_paid")
        entry = db().execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='extend_paid' AND source_type='subscriber_time_extension'"
        ).fetchone()

        assert updated.balance == 0  # cash paid → balance unchanged
        assert entry is not None and entry["entry_type"] == "time_extension" and entry["direction"] == "credit"


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
