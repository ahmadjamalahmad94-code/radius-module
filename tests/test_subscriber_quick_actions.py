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
