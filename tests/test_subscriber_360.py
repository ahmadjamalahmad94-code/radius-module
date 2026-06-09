from __future__ import annotations

import os

import pytest

from app.radius.core.types import Subscriber
from app.radius.db.connection import db, reset_for_tests
from app.radius.db.helpers import json_load
from app.radius.db.repos import subscribers_repo
from app.radius.services.accounting import AccountingService
from app.radius.services.business_os_finance import WalletService
from app.radius.services.subscriber_360 import (
    LoanPolicyEngine,
    RenewalLifecycleCalculator,
    Subscriber360Service,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "subscriber_360.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "dev-token-please-change")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "subscriber_admin"
        sess["admin_name"] = "Subscriber Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "subscriber-csrf"


def _seed_subscriber() -> Subscriber:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Monthly 150", 30 * 24 * 60, 30, 150.0, "JOD"),
    )
    plan_id = cur.lastrowid
    return subscribers_repo.upsert_subscriber(
        Subscriber(
            id=None,
            tenant_id=1,
            username="s360-user",
            password="secret",
            plan_id=plan_id,
            full_name="Subscriber 360 User",
            mac_lock="AA:BB:CC:DD:EE:01",
            allowed_macs="AA:BB:CC:DD:EE:02",
            remark="VIP note",
        )
    )


def test_renewal_calculation_handles_partial_discount_debt_and_loan_days():
    calc = RenewalLifecycleCalculator()

    assert calc.calculate(plan_price=150, amount_paid=100, base_days=30).earned_days == 20
    assert calc.calculate(
        plan_price=150,
        amount_paid=100,
        discount_amount=50,
        base_days=30,
    ).earned_days == 30
    assert calc.calculate(
        plan_price=150,
        amount_paid=100,
        debt_amount=50,
        base_days=30,
    ).earned_days == 30
    assert calc.calculate(
        plan_price=150,
        amount_paid=150,
        base_days=30,
        loan_days_to_settle=3,
    ).earned_days == 27
    assert calc.calculate(plan_price=150, amount_paid=150, base_days=30).applied_to_radius is False


def test_loan_policy_sequence_limits_and_override():
    engine = LoanPolicyEngine()
    profile = {"enabled": True, "sequence_days": [2, 1], "count_limit": 2}

    assert engine.evaluate(profile_rule=profile, previous_loan_count=0)["next_days"] == 2
    assert engine.evaluate(profile_rule=profile, previous_loan_count=1)["next_days"] == 1
    assert engine.evaluate(profile_rule=profile, previous_loan_count=2)["allowed"] is False

    override = {"sequence_days": [3], "count_limit": 1, "approval_required": True}
    result = engine.evaluate(
        profile_rule=profile,
        subscriber_override=override,
        previous_loan_count=0,
    )
    assert result["next_days"] == 3
    assert result["approval_required"] is True


def test_subscriber_360_aggregates_financial_usage_devices_and_events(app):
    with app.app_context():
        sub = _seed_subscriber()
        wallet = WalletService().create_wallet(tenant_id=1, owner_type="subscriber", owner_id=sub.id)
        WalletService().credit(
            tenant_id=1,
            wallet_id=wallet["id"],
            amount="12.50",
            actor_type="admin",
            actor_id=1,
            reference_type="test",
        )
        AccountingService(1).create_payment(
            {
                "subscriber_id": sub.id,
                "amount": "100",
                "discount_amount": "50",
                "method": "cash",
                "dry_run": "1",
            },
            actor="cashier",
        )
        AccountingService(1).create_loan(
            {"subscriber_id": sub.id, "days": "2", "amount": "10"},
            actor="cashier",
        )
        db().execute(
            """
            INSERT INTO radacct(
              tenant_id, acctsessionid, username, nasipaddress, callingstationid,
              framedipaddress, acctstarttime, acctsessiontime, acctinputoctets,
              acctoutputoctets
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (1, "s1", sub.username, "10.0.0.1", "AA:BB:CC:DD:EE:03", "192.0.2.10", "2026-01-01", 60, 100, 200),
        )
        db().execute(
            "INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas) VALUES(?,?,?,?,?,?,?)",
            (1, sub.username, "masked-in-route", "Access-Accept", "2026-01-01", "", "nas-1"),
        )

        payload = Subscriber360Service(tenant_id=1).get_by_id(sub.id)

    assert payload["financial"]["total_paid"] == 100.0
    assert payload["financial"]["total_discount"] == 50.0
    assert payload["financial"]["wallet_balance"] == 12.5
    assert payload["usage"]["total_seconds"] == 60
    assert {item["mac"] for item in payload["devices"]} >= {
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
        "AA:BB:CC:DD:EE:03",
    }
    assert payload["login_events"][0]["reply"] == "Access-Accept"
    assert "pass" not in payload["login_events"][0]


def test_subscriber_360_routes_render_and_existing_profile_still_works(app):
    with app.app_context():
        sub = _seed_subscriber()

    with app.test_client() as client:
        _auth_session(client)
        list_res = client.get("/admin/radius/subscribers")
        detail_res = client.get(f"/admin/radius/subscribers/{sub.id}")
        alias_res = client.get(f"/admin/radius/users/{sub.username}/360")
        profile_res = client.get(f"/admin/radius/users/{sub.username}/profile")

    assert list_res.status_code == 200
    assert detail_res.status_code == 200
    assert alias_res.status_code == 200
    assert profile_res.status_code == 200
    html = detail_res.get_data(as_text=True)
    assert "ملف المشترك 360" in html
    assert "المالية" in html
    assert "الاستخدام والجلسات" in html
    assert "renewal-preview-form" in html


def test_subscriber_360_api_returns_safe_json(app):
    with app.app_context():
        sub = _seed_subscriber()
        db().execute(
            "INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas) VALUES(?,?,?,?,?,?,?)",
            (1, sub.username, "secret-from-radius", "Access-Accept", "2026-01-01", "", "nas-1"),
        )

    with app.test_client() as client:
        response = client.get(
            f"/api/v1/accounts/{sub.username}/360",
            headers={"Authorization": "Bearer dev-token-please-change"},
        )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["subscriber"]["username"] == sub.username
    assert "password" not in data["subscriber"]
    assert "pass" not in data["login_events"][0]
    assert "secret-from-radius" not in str(body)


def test_renewal_preview_records_event_without_radius_apply(app):
    with app.app_context():
        sub = _seed_subscriber()
        preview = Subscriber360Service(tenant_id=1).preview_renewal(
            subscriber_id=sub.id,
            amount_paid=100,
            discount_amount=50,
            actor="qa",
        )
        events = db().execute(
            "SELECT * FROM business_events WHERE tenant_id=1 AND target_type='subscriber' AND target_id=?",
            (sub.id,),
        ).fetchall()

    assert preview["earned_days"] == 30
    assert preview["applied_to_radius"] is False
    assert events
    metadata = json_load(events[0]["metadata_json"])
    assert metadata["renewal"]["applied_to_radius"] is False
