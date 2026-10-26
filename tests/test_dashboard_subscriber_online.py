"""Dashboard «المشتركون → متصلون الآن» must count subscribers only.

Farah Net (2026-10-26): the subscribers card showed 146 «connected now» while
the tenant had 82 subscribers — the card was fed the instance-wide online
total (cards + subscribers). ``subs["online_subscribers"]`` now carries the
subscriber-only number; ``subs["online"]`` stays the total (API + devices card).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_subonline_")
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


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _sub(conn, username, user_type="subscriber"):
    conn.execute(
        "INSERT INTO subscribers (tenant_id, username, password, user_type, status, created_at) "
        "VALUES (1, ?, 'p', ?, 'enabled', ?)", (username, user_type, _now()))


def _session(conn, username, sid, *, open_=True):
    conn.execute(
        "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, acctstarttime, acctstoptime) VALUES (1, ?, ?, ?, '10.0.0.1', ?, ?)",
        (sid, f"u-{sid}", username, _now(), None if open_ else _now()))


def _seed(conn):
    plan_id = conn.execute(
        "INSERT INTO access_plans (tenant_id, name, created_at) VALUES (1, 'P', ?)",
        (_now(),)).lastrowid
    batch_id = conn.execute(
        "INSERT INTO card_batches (tenant_id, batch_code, package_name, plan_id, count, "
        "generated, used, created_at) VALUES (1, 'B1', 'Cards', ?, 2, 2, 1, ?)",
        (plan_id, _now())).lastrowid
    # two online subscribers — one of them on two devices (two open rows)
    _sub(conn, "sub_a")
    _sub(conn, "sub_b")
    _sub(conn, "sub_off")
    _session(conn, "sub_a", "1")
    _session(conn, "sub_a", "2")
    _session(conn, "sub_b", "3")
    _session(conn, "sub_off", "4", open_=False)
    # an online card (real cards row + its subscribers mirror row)
    conn.execute(
        "INSERT INTO cards (tenant_id, batch_id, username, password, plan_id, used, created_at) "
        "VALUES (1, ?, 'card_1', 'x', ?, 1, ?)", (batch_id, plan_id, _now()))
    _sub(conn, "card_1", user_type="card")
    _session(conn, "card_1", "5")
    # a card known only as a mirror row
    _sub(conn, "card_mirror", user_type="card")
    _session(conn, "card_mirror", "6")


def test_subscriber_online_excludes_cards_and_dedups_devices(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import dashboard_metrics as dm
        with transaction() as conn:
            _seed(conn)
        assert dm.get_subscriber_online_count(1) == 2


def test_build_metrics_carries_subscriber_only_number(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services import dashboard_metrics as dm
        with transaction() as conn:
            _seed(conn)
        subs = dm.build_dashboard_metrics(1)["subscribers"]
        assert subs["online_subscribers"] == 2
        # the instance-wide total keeps its meaning for the API / devices card
        assert "online" in subs
