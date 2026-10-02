"""Zero-w1 #3 (round 6 L1) — simultaneous first logins no longer race past
the device limit.

Round 6: a card limited to 1 device was accepted from 8 devices on its first
login (30/30). The limit counted open radacct sessions; between Access-Accept
and Acct-Start nothing was open yet, so every concurrent request passed. Now the
device slot is claimed atomically (BEGIN IMMEDIATE) right before the Accept.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

import pytest

NAS_IP = "10.50.0.9"


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_zw1_race_")
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


def _mk_card(username, *, device_count=1, mode=""):
    from app.radius.core.types import AccessPlan, CardBatch
    from app.radius.db.connection import transaction
    from app.radius.db.repos import cards_repo, plans_repo
    plan = plans_repo.upsert_plan(AccessPlan(id=None, tenant_id=1,
                                             name="p-" + username, enabled=True))
    batch = cards_repo.create_batch(CardBatch(
        id=None, tenant_id=1, batch_code=f"B-{username}", plan_id=plan.id,
        count=1, device_count=device_count, device_limit_mode=mode))
    with transaction() as c:
        c.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
            " used, revoked, created_at) VALUES (1,?,?,?,?,0,0,datetime('now'))",
            (batch.id, username, "pw", plan.id))


def _auth(username, mac, **kw):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password="pw", tenant_id=1,
                                 calling_station_id=mac, nas_ip=NAS_IP, **kw))


def _race(app, username, n=8):
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i):
        with app.app_context():
            barrier.wait()
            try:
                results[i] = _auth(username, f"AA:BB:CC:00:00:{i:02X}")
            finally:
                from app.radius.db.connection import close_thread_conn
                close_thread_conn()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return results


@pytest.mark.parametrize("round_", range(3))
def test_eight_simultaneous_first_logins_one_device_card(app, round_):
    with app.app_context():
        _mk_card(f"race{round_}", device_count=1)
    res = _race(app, f"race{round_}")
    accepted = [r for r in res if r is not None and r.ok]
    rejected = [r for r in res if r is not None and not r.ok]
    assert len(accepted) == 1, [(r.ok, r.reason) for r in res if r]
    assert len(rejected) == 7 and all(r.reason == "concurrent_limit" for r in rejected)


def test_two_device_card_admits_exactly_two(app):
    with app.app_context():
        _mk_card("race2dev", device_count=2)
    res = _race(app, "race2dev")
    assert sum(1 for r in res if r and r.ok) == 2


def test_same_device_reauth_and_other_device_after_claim(app):
    with app.app_context():
        _mk_card("reauth1", device_count=1)
        assert _auth("reauth1", "AA:00:00:00:00:01").ok
        # the same device retrying (e.g. lost Accept) is not a second device
        assert _auth("reauth1", "AA:00:00:00:00:01").ok
        # another device inside the Accept→Acct-Start window is refused
        d = _auth("reauth1", "AA:00:00:00:00:02")
        assert not d.ok and d.reason == "concurrent_limit"


def test_test_auth_simulation_does_not_claim(app):
    with app.app_context():
        _mk_card("sim1", device_count=1)
        assert _auth("sim1", "AA:00:00:00:00:09", simulate=True).ok
        assert _auth("sim1", "AA:00:00:00:00:01").ok


def test_expired_claim_frees_the_slot(app):
    with app.app_context():
        _mk_card("ttl1", device_count=1)
        assert _auth("ttl1", "AA:00:00:00:00:01").ok
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("UPDATE device_limit_claims SET claimed_at='2000-01-01T00:00:00'")
        assert _auth("ttl1", "AA:00:00:00:00:02").ok


def test_claim_that_started_and_ended_does_not_block_the_next_device(app):
    """Phone logs in (claim), its session starts and stops; the laptop logging
    in right after must not wait for the claim TTL."""
    from datetime import datetime
    with app.app_context():
        _mk_card("swap1", device_count=1)
        assert _auth("swap1", "AA:00:00:00:00:01").ok
        from app.radius.db.connection import transaction
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with transaction() as c:
            c.execute(
                "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username,"
                " nasipaddress, callingstationid, acctstarttime, acctupdatetime,"
                " acctstoptime) VALUES (1,'sw1','u-sw1','swap1',?,?,?,?,?)",
                (NAS_IP, "AA:00:00:00:00:01", now, now, now))
        assert _auth("swap1", "AA:00:00:00:00:02").ok
