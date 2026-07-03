# -*- coding: utf-8 -*-
"""Bug #1 (residual) — first-login validity must honour SUB-DAY budgets.

``card_batch_flags._materialize_first_login_validity`` used to read the budget
in whole DAYS only, so a from-first-connect batch with a 3-hour / 10-minute
budget produced ``days=0`` and NEVER stamped an expiry — the card then had no
enforcement at all. It now computes the budget in seconds through the shared
``card_accounting`` helper, so a sub-day window materialises correctly at first
login.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_cbf_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed(app, *, username, time_value, time_unit, count_from_first_connect=1):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _dt.datetime.utcnow().isoformat()
        with transaction() as c:
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, created_at) "
                "VALUES (1,?,?,?)", ("4 ميجا فري لانسر", "Hotspot", now)).lastrowid
            bid = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
                "package_name, count_from_first_connect, time_value, time_unit, "
                "source_type, created_at) VALUES (1,?,?,?,?,?,?,?,?,?)",
                ("b-" + username, pid, 1, "امواج البحر", count_from_first_connect,
                 time_value, time_unit, "imported", now)).lastrowid
            c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
                "created_at) VALUES (1,?,?,?,?,?)",
                (bid, username, "pw", pid, now))
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, created_at) "
                "VALUES (1,?,?,?)", (username, "pw", now))


def _materialize(app, username, now):
    with app.app_context():
        from app.radius.db.repos import cards_repo
        from app.radius.services import card_batch_flags as cbf
        card = cards_repo.get_card_by_username(1, username)
        batch = cards_repo.get_batch(1, card.batch_id)
        cbf._materialize_first_login_validity(1, username, card, batch, now)
        row = cards_repo.get_card_by_username(1, username)
        return row.expire_at


def test_three_hour_budget_materialises_at_first_login(app):
    _seed(app, username="5698046", time_value=3, time_unit="hours")
    now = _dt.datetime(2026, 7, 3, 15, 25, 0)
    expire = _materialize(app, "5698046", now)
    assert expire is not None, "3h budget must stamp an expiry (was days-only → 0)"
    assert abs((expire - _dt.datetime(2026, 7, 3, 18, 25, 0)).total_seconds()) < 2


def test_ten_minute_budget_materialises(app):
    _seed(app, username="10min-1", time_value=10, time_unit="minutes")
    now = _dt.datetime(2026, 7, 3, 15, 25, 0)
    expire = _materialize(app, "10min-1", now)
    assert expire is not None
    assert abs((expire - _dt.datetime(2026, 7, 3, 15, 35, 0)).total_seconds()) < 2


def test_by_seconds_batch_is_not_materialised(app):
    """count_from_first_connect OFF → no wall-clock expiry is stamped."""
    _seed(app, username="bysec-1", time_value=3, time_unit="hours",
          count_from_first_connect=0)
    now = _dt.datetime(2026, 7, 3, 15, 25, 0)
    expire = _materialize(app, "bysec-1", now)
    assert expire is None
