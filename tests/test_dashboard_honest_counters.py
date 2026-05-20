"""R10.2 regression: Dashboard KPIs must be honest.

Pre-R10.2 two numbers lied:

 1. `total_subscribers` counted every row in `subscribers` regardless of
    user_type — so the 2020 card-mirror rows inflated it from 37 to
    2057.
 2. `revenue_today` / `revenue_month` summed plan prices for arbitrary
    subscriber slices (`subs[-20:]` / all subs). Both numbers had no
    relationship to actual money moved — pure fiction.

After R10.2:
 - subscriber counters filter `user_type='subscriber'` at the SQL layer
   (the filter that already exists on list_accounts since R9.0).
 - revenue numbers come from `payment_transactions` with
   `status='posted'`, partitioned by `created_at` (today / this month).

The tests below seed both tables with controlled values and check the
exact numbers that come back from DashboardService.snapshot().
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r102_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _seed_sub(conn, *, username, user_type):
    conn.execute("""
        INSERT INTO subscribers
            (tenant_id, username, password, user_type, status, created_at)
        VALUES (?,?,?,?,?,?)
    """, (1, username, "p", user_type, "enabled", _now_iso()))
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _seed_payment(conn, *, subscriber_id, amount, when, status="posted"):
    conn.execute("""
        INSERT INTO payment_transactions
            (tenant_id, subscriber_id, username, amount, currency, method,
             status, plan_price, effective_price, earned_minutes,
             rounding_mode, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (1, subscriber_id, "x", amount, "JOD", "cash", status,
           amount, amount, 0, "floor", when))


# ─────────── subscriber counters ───────────

def test_total_subscribers_excludes_cards(app):
    """R10.2: 2 real subscribers + 5 card-mirror rows → total = 2, not 7."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.dashboard import get_dashboard_service

        with transaction() as c:
            _seed_sub(c, username="ahmad",  user_type="subscriber")
            _seed_sub(c, username="ali",    user_type="subscriber")
            for code in ("1001", "1002", "1003", "1004", "1005"):
                _seed_sub(c, username=code, user_type="card")

        snap = get_dashboard_service().snapshot()
        assert snap.total_subscribers == 2, \
            "total_subscribers must count only user_type='subscriber'"
        assert snap.enabled_subscribers == 2


# ─────────── revenue totals ───────────

def test_revenue_today_sums_posted_payments_only_today(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.dashboard import get_dashboard_service

        with transaction() as c:
            sid = _seed_sub(c, username="ahmad", user_type="subscriber")
            # 2 payments today (one posted, one voided)
            _seed_payment(c, subscriber_id=sid, amount=10.0,
                          when=_now_iso(), status="posted")
            _seed_payment(c, subscriber_id=sid, amount=999.0,
                          when=_now_iso(), status="voided")
            # 1 posted payment 5 days ago — counts for month, not today
            old = (datetime.utcnow() - timedelta(days=5)).isoformat() + "Z"
            _seed_payment(c, subscriber_id=sid, amount=20.0,
                          when=old, status="posted")

        snap = get_dashboard_service().snapshot()
        assert snap.revenue_today == 10.0, \
            "revenue_today must include only today's posted payments"


def test_revenue_month_sums_posted_payments_this_month(app):
    """Anything created since the 1st of this month counts; older does not."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.dashboard import get_dashboard_service

        # build a date in last month (start_of_month - 1 day)
        first_of_this_month = datetime.utcnow().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = (first_of_this_month - timedelta(days=1)).isoformat() + "Z"

        with transaction() as c:
            sid = _seed_sub(c, username="ahmad", user_type="subscriber")
            _seed_payment(c, subscriber_id=sid, amount=15.0,
                          when=_now_iso(), status="posted")
            _seed_payment(c, subscriber_id=sid, amount=999.0,
                          when=last_month, status="posted")

        snap = get_dashboard_service().snapshot()
        assert snap.revenue_month == 15.0, \
            "revenue_month must exclude payments from previous months"


def test_revenue_zero_when_no_payments(app):
    """Empty payment_transactions → both revenues are 0.0, not None / crash."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.dashboard import get_dashboard_service

        with transaction() as c:
            _seed_sub(c, username="ahmad", user_type="subscriber")

        snap = get_dashboard_service().snapshot()
        assert snap.revenue_today == 0.0
        assert snap.revenue_month == 0.0
