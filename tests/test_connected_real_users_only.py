# -*- coding: utf-8 -*-
"""FIX A — the «connected now» view/counters must contain ONLY sessions we
authenticated via RADIUS (a real subscriber or card in this tenant).

The router shows more than our RADIUS users: MikroTik hotspot mac-cookie
sessions (`T-<MAC>`, "trying to log in by mac-cookie") and the built-in trial
(`Default service` / `مؤقت`) are local to the router — they never got an
Access-Accept from us. mt_reconciler materializes them into radacct so they stay
CoA-targetable, but they must never count or show as connected RADIUS users.

This locks the data layer:
  * list_online_from_radacct (the /online list) excludes them and keeps the
    subscriber vs card split intact.
  * live_sessions.resolve_real_types / tenant_active_count(real_only=True)
    resolve + count only real users.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_real_only_")
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


def _radacct(c, *, username, sid, ip="10.10.0.2", ptype="ethernet"):
    now = _now()
    c.execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, nasporttype, framedipaddress, callingstationid, "
        "acctstarttime, acctupdatetime) VALUES(1,?,?,?,?,?,?,?,?,?)",
        (sid, f"u-{sid}", username, ip, ptype, "10.20.30.254",
         "AA:BB:CC:DD:EE:FF", now, now),
    )


@pytest.fixture
def seeded(app):
    """One real subscriber (ahmad), one real card (2044), one mac-cookie
    (T-<MAC>) and one trial (Default service) — all live open radacct rows."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _now()
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, created_at) "
                "VALUES (1,?,?,?)", ("ahmad", "pw", now))
            plan_id = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, created_at) "
                "VALUES (1,?,?,?)", ("Card Plan", "Hotspot", now)).lastrowid
            batch_id = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, created_at) "
                "VALUES (1,?,?,?,?)", ("b1", plan_id, 1, now)).lastrowid
            c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, created_at) "
                "VALUES (1,?,?,?,?,?)", (batch_id, "2044", "pw", plan_id, now))
            _radacct(c, username="ahmad", sid="s-ahmad")
            _radacct(c, username="2044", sid="s-2044")
            _radacct(c, username="T-AA:BB:CC:DD:EE:FF", sid="s-cookie")
            _radacct(c, username="Default service", sid="s-trial")
    return app


def test_list_excludes_router_local_and_trial(seeded):
    """list_online_from_radacct returns ONLY the real subscriber + card."""
    with seeded.app_context():
        from app.radius.integration.sqlite_adapter import SqliteAdapter
        rows = SqliteAdapter().list_online_from_radacct(limit=50)
        names = sorted(s.username for s in rows)
    assert names == ["2044", "ahmad"]
    assert "T-AA:BB:CC:DD:EE:FF" not in names
    assert "Default service" not in names


def test_list_classifies_subscriber_vs_card(seeded):
    """The «المشتركون»/«الكروت» toggle relies on user_type: ahmad=subscriber,
    2044=card."""
    with seeded.app_context():
        from app.radius.integration.sqlite_adapter import SqliteAdapter
        by_user = {s.username: s.user_type
                   for s in SqliteAdapter().list_online_from_radacct(limit=50)}
    assert by_user["ahmad"] == "subscriber"
    assert by_user["2044"] == "card"


def test_resolve_real_types_maps_only_real_users(seeded):
    with seeded.app_context():
        from app.radius.services import live_sessions as ls
        real = ls.resolve_real_types(
            1, ["ahmad", "2044", "T-AA:BB:CC:DD:EE:FF", "Default service", "مؤقت"])
    assert real == {"ahmad": "subscriber", "2044": "card"}


def test_tenant_active_count_real_only_excludes_trial(seeded):
    with seeded.app_context():
        from app.radius.services import live_sessions as ls
        # Raw count sees all four open rows; real-only sees just the two.
        assert ls.tenant_active_count(1) == 4
        assert ls.tenant_active_count(1, real_only=True) == 2


def test_count_real_sessions_from_router_rows(seeded):
    """Feeds the «connected now» chip from a raw router active-session list."""
    with seeded.app_context():
        from app.radius.services import live_sessions as ls
        router_rows = [
            {"username": "ahmad"}, {"username": "2044"},
            {"username": "T-AA:BB:CC:DD:EE:FF"}, {"username": "Default service"},
        ]
        assert ls.count_real_sessions(1, [r["username"] for r in router_rows]) == 2
