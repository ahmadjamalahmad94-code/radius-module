"""Imported cards must NOT inflate the «المشتركون» (subscriber) count.

Regression for the fresh-VPS bug: the owner imported ONLY cards (batch import)
and added zero subscribers, yet «المشتركون» showed ~4209 everywhere. Imported/
generated cards mirror into the `subscribers` table with user_type='card' (see
cards service), and the raw COUNT(*) FROM subscribers queries tallied them as
subscribers. Every subscriber count must exclude card-type records and match
«قائمة المشتركين»; cards belong only under «الكروت والحِزم».
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_subcount_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _plan_id() -> int:
    from app.radius.db.connection import db
    cur = db().execute(
        """INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                    price, currency, created_at, updated_at)
           VALUES(1,'P',480,1,5.0,'ILS',datetime('now'),datetime('now'))""")
    return int(cur.lastrowid)


def _seed_imported_cards(n: int, plan_id: int) -> None:
    """Reproduce an import: N cards + their user_type='card' subscriber mirrors."""
    from app.radius.db.connection import db
    conn = db()
    bcur = conn.execute(
        """INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id,
             count, generated, created_by, status, source_type, created_at)
           VALUES(1,'IMP-1','Imported',?,?,?,'owner','active','imported',datetime('now'))""",
        (plan_id, n, n))
    batch_id = int(bcur.lastrowid)
    for i in range(n):
        u = f"card{i:05d}"
        conn.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, created_at)"
            " VALUES(1,?,?,?,?,0,datetime('now'))",
            (batch_id, u, f"{i:06d}", plan_id))
        # the RADIUS mirror row the cards service creates on import sync
        conn.execute(
            "INSERT INTO subscribers(tenant_id, username, password, user_type, plan_id,"
            " status, card_batch_id, created_by, created_at)"
            " VALUES(1,?,?, 'card', ?, 'enabled', ?, 'owner', datetime('now'))",
            (u, f"{i:06d}", plan_id, batch_id))


def _add_real_subscriber(username: str, plan_id: int, *, status: str = "enabled") -> None:
    from app.radius.db.connection import db
    db().execute(
        "INSERT INTO subscribers(tenant_id, username, password, user_type, plan_id,"
        " status, created_by, created_at)"
        " VALUES(1,?,?, 'subscriber', ?, ?, 'admin', datetime('now'))",
        (username, "pw", plan_id, status))


def test_only_imported_cards_report_zero_subscribers(app):
    N = 50
    with app.app_context():
        plan = _plan_id()
        _seed_imported_cards(N, plan)

        from app.radius.db.repos import subscribers_repo
        from app.radius.services import dashboard_metrics
        from app.radius.services.dashboard_reports import DashboardReportsService
        from app.radius.services.data_reset import DataResetService
        from app.radius.db.connection import db

        # sanity: the subscribers table really does hold N card-mirror rows
        raw = db().execute("SELECT COUNT(*) c FROM subscribers WHERE tenant_id=1").fetchone()["c"]
        assert int(raw) == N

        # (1) network/dashboard overview KPI
        counts = dashboard_metrics.get_subscriber_counts(1)
        assert counts["total"] == 0
        assert counts["active"] == 0

        # (2) users_list stats source
        assert subscribers_repo.count_subscribers(1, user_type="subscriber") == 0
        assert subscribers_repo.subscribers_status_counts(
            1, user_type="subscriber")["total"] == 0

        # (3) executive summary
        summary = DashboardReportsService(tenant_id=1).executive_summary()
        assert summary["subscribers"]["total"] == 0
        assert summary["cards"]["total"] == N

        # (4) data-reset category counts: cards under الكروت, subscribers = 0
        rep = DataResetService().summarize(
            tenant_id=1, keys=["subscribers", "cards"], current_admin_id=1)
        cat = {c["key"]: c["count"] for c in rep["categories"]}
        assert cat["subscribers"] == 0, "cards must not be counted as subscribers"
        assert cat["cards"] == N


def test_real_subscribers_still_counted_and_cards_excluded(app):
    N = 30
    with app.app_context():
        plan = _plan_id()
        _seed_imported_cards(N, plan)
        _add_real_subscriber("real_a", plan, status="enabled")
        _add_real_subscriber("real_b", plan, status="enabled")
        _add_real_subscriber("real_c", plan, status="disabled")

        from app.radius.db.repos import subscribers_repo
        from app.radius.services import dashboard_metrics
        from app.radius.services.data_reset import DataResetService

        counts = dashboard_metrics.get_subscriber_counts(1)
        assert counts["total"] == 3          # only the 3 real subscribers
        assert counts["active"] == 2         # 2 enabled
        assert counts["disabled"] == 1

        assert subscribers_repo.count_subscribers(1, user_type="subscriber") == 3

        rep = DataResetService().summarize(
            tenant_id=1, keys=["subscribers", "cards"], current_admin_id=1)
        cat = {c["key"]: c["count"] for c in rep["categories"]}
        assert cat["subscribers"] == 3       # real subscribers only
        assert cat["cards"] == N             # cards counted once, under cards
