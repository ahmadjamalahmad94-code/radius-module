from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "usage_counters.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_USAGE_COUNTERS_NOW", "2026-05-25T12:00:00Z")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _insert_session(username="ali", *, day="2026-05-25", inb=100, outb=200, nas="198.51.100.10"):
    db().execute(
        """
        INSERT INTO radacct (
          tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
          acctstarttime, acctinputoctets, acctoutputoctets, acctsessiontime
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{username}-{day}-{inb}",
            f"u-{username}-{day}-{inb}",
            username,
            nas,
            f"{day}T12:00:00Z",
            inb,
            outb,
            60,
        ),
    )


def _seed_plan_subscriber():
    plan_id = db().execute(
        """
        INSERT INTO access_plans (tenant_id, name, speed_down_kbps, speed_up_kbps, price, created_at)
        VALUES (1, 'Gold', 10000, 2000, 5, '2026-05-25T00:00:00Z')
        """
    ).lastrowid
    db().execute(
        """
        INSERT INTO subscribers (tenant_id, username, password, plan_id, status, created_at)
        VALUES (1, 'ali', 'x', ?, 'enabled', '2026-05-25T00:00:00Z')
        """,
        (plan_id,),
    )
    return int(plan_id)


def test_counters_from_sessions(app_db):
    from app.radius.services.usage_counters import UsageCountersService

    _insert_session(inb=100, outb=200)
    _insert_session(inb=50, outb=70, nas="198.51.100.11")

    summary = UsageCountersService().tenant_summary(tenant_id=1, window="daily")

    assert summary["sessions"] == 2
    assert summary["total_bytes"] == 420
    assert len(summary["by_nas"]) == 2


def test_daily_window_behavior(app_db):
    from app.radius.services.usage_counters import UsageCountersService

    _insert_session(day="2026-05-25", inb=100, outb=0)
    _insert_session(day="2026-05-24", inb=900, outb=0)

    summary = UsageCountersService().subscriber_summary(tenant_id=1, username="ali", window="daily")

    assert summary["total_bytes"] == 100


def test_monthly_window_behavior(app_db):
    from app.radius.services.usage_counters import UsageCountersService

    _insert_session(day="2026-05-01", inb=100, outb=0)
    _insert_session(day="2026-04-30", inb=900, outb=0)

    summary = UsageCountersService().subscriber_summary(tenant_id=1, username="ali", window="monthly")

    assert summary["total_bytes"] == 100


def test_plan_usage_summary(app_db):
    from app.radius.services.usage_counters import UsageCountersService

    plan_id = _seed_plan_subscriber()
    _insert_session(username="ali", inb=11, outb=22)

    summary = UsageCountersService().plan_summary(tenant_id=1, plan_id=plan_id, window="daily")

    assert summary["plan_id"] == plan_id
    assert summary["total_bytes"] == 33


def test_quota_warning_and_block_are_advisory(app_db):
    from app.radius.services.usage_counters import UsageCountersService

    _insert_session(username="ali", inb=80, outb=0)
    service = UsageCountersService()

    warning = service.quota_decision(tenant_id=1, username="ali", limit_bytes=100)
    block = service.quota_decision(tenant_id=1, username="ali", limit_bytes=80)

    assert warning["decision"] == "warn"
    assert warning["enforced"] is False
    assert block["decision"] == "block"
    assert block["enforced"] is False


def test_usage_and_quota_routes(client):
    _seed_plan_subscriber()
    _insert_session(username="ali", inb=10, outb=20)

    tenant = client.get("/api/v1/accounting/usage/tenant", headers=AUTH)
    assert tenant.get_json()["data"]["total_bytes"] == 30

    subscriber = client.get("/api/v1/accounting/usage/subscribers/ali", headers=AUTH)
    assert subscriber.get_json()["data"]["total_bytes"] == 30

    quota = client.post(
        "/api/v1/accounting/quota/check",
        json={"username": "ali", "limit_bytes": 20},
        headers=AUTH,
    )
    assert quota.get_json()["data"]["decision"] == "block"
    assert quota.get_json()["data"]["enforced"] is False

    missing_username = client.post(
        "/api/v1/accounting/quota/check",
        json={"limit_bytes": 20},
        headers=AUTH,
    )
    assert missing_username.status_code == 422
    assert missing_username.get_json()["error"]["message"] == "اسم المستخدم مطلوب."

    bad_limit = client.post(
        "/api/v1/accounting/quota/check",
        json={"username": "ali", "limit_bytes": "bad"},
        headers=AUTH,
    )
    assert bad_limit.status_code == 422
    assert bad_limit.get_json()["error"]["message"] == "قيمة limit_bytes يجب أن تكون رقمًا صحيحًا."
