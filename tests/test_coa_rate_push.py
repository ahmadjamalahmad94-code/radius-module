"""R9.3 regression: speed changes propagate to MikroTik via CoA without
needing MT API.

The user reported that editing a subscriber's plan/speed in the UI never
reaches MikroTik. The old path went through the MT API sync queue, which
fails when port 8728 is firewalled. R9.3 adds a CoA Change-of-Authorization
hop after upsert_account that applies the new rate immediately on the
user's active session — using UDP/3799 (already mapped) and the existing
nas_devices secret (per R9.1).

Coverage:
  1. change_user_rate rejects empty rate.
  2. change_user_rate rejects when no active session (and says so clearly
     in the message — subsequent reauth will pick up the new rate).
  3. change_user_rate reaches send_coa with the right args.
  4. _push_coa_rate_if_active is a no-op when subscriber has no plan.
  5. _push_coa_rate_if_active no-ops when plan has no speed defined.
  6. _push_coa_rate_if_active computes the rate string from the plan
     and pushes it via change_user_rate.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r93_")
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


def _seed_active(conn, *, tenant_id=1, username, nas_ip):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, acctstarttime)
        VALUES (?,?,?,?,?,?)
    """, (tenant_id, "s-rate-1", "uniq", username, nas_ip, now))


def _seed_nas(conn, *, tenant_id=1, name, address, secret):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO nas_devices
            (tenant_id, name, address, secret, vendor, nas_type, enabled, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (tenant_id, name, address, secret, "mikrotik", "hotspot", 1, now))


# ─────────── change_user_rate ───────────

def test_change_user_rate_rejects_empty_rate(app):
    with app.app_context():
        from app.radius.integration.radius_coa import change_user_rate
        res = change_user_rate(1, "ahmad", new_rate_limit="")
        assert res.ok is False
        assert res.code_name == "empty_rate"


def test_change_user_rate_when_no_active_session_returns_clear_message(app):
    with app.app_context():
        from app.radius.integration.radius_coa import change_user_rate
        res = change_user_rate(1, "no-such-user", new_rate_limit="2M/2M")
        assert res.ok is False
        assert res.code_name == "no_active_session"
        # message hints at what happens next (reauth picks it up)
        assert "الجلسة التالية" in res.reply_message


def test_change_user_rate_reaches_send_coa(app, monkeypatch):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.integration import radius_coa

        with transaction() as c:
            _seed_nas(c, name="mt", address="10.0.0.1", secret="topsecret")
            _seed_active(c, username="ahmad", nas_ip="10.0.0.1")

        captured = {}
        def _fake_send_coa(*, nas_ip, nas_secret, username, session_id,
                            new_rate_limit, port=3799, timeout=5.0):
            captured.update(dict(nas_ip=nas_ip, nas_secret=nas_secret,
                                  username=username, session_id=session_id,
                                  new_rate_limit=new_rate_limit))
            return radius_coa.CoaResult(ok=True, code=44, code_name="CoA-ACK",
                                         reply_message="ok")
        monkeypatch.setattr(radius_coa, "send_coa", _fake_send_coa)

        res = radius_coa.change_user_rate(1, "ahmad", new_rate_limit="5M/5M")
        assert res.ok is True
        assert captured == {
            "nas_ip": "10.0.0.1",
            "nas_secret": "topsecret",
            "username": "ahmad",
            "session_id": "s-rate-1",
            "new_rate_limit": "5M/5M",
        }


# ─────────── _push_coa_rate_if_active ───────────

def test_push_coa_skipped_when_no_plan(app, monkeypatch):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.integration import sqlite_adapter

        called = {"n": 0}
        def _fake(*a, **k):
            called["n"] += 1
            from app.radius.integration.radius_coa import CoaResult
            return CoaResult(ok=True, code=0, code_name="noop", reply_message="")
        monkeypatch.setattr(
            "app.radius.integration.radius_coa.change_user_rate", _fake)

        sub = Subscriber(id=1, tenant_id=1, username="x", password="p",
                         plan_id=None)
        sqlite_adapter._push_coa_rate_if_active(sub)
        assert called["n"] == 0


def test_push_coa_skipped_when_plan_has_no_speed(app, monkeypatch):
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo
        from app.radius.integration import sqlite_adapter

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="No-Rate", enabled=True,
            speed_down_kbps=0, speed_up_kbps=0, burst_raw="",
        ))

        called = {"n": 0}
        def _fake(*a, **k):
            called["n"] += 1
            from app.radius.integration.radius_coa import CoaResult
            return CoaResult(ok=True, code=0, code_name="noop", reply_message="")
        monkeypatch.setattr(
            "app.radius.integration.radius_coa.change_user_rate", _fake)

        sub = Subscriber(id=1, tenant_id=1, username="x", password="p",
                         plan_id=plan.id)
        sqlite_adapter._push_coa_rate_if_active(sub)
        assert called["n"] == 0


def test_push_coa_computes_rate_string_from_plan(app, monkeypatch):
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo
        from app.radius.integration import sqlite_adapter

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Fast", enabled=True,
            speed_up_kbps=2048, speed_down_kbps=10240,
        ))

        captured = {}
        def _fake(tenant_id, username, *, new_rate_limit):
            captured.update(dict(tenant_id=tenant_id, username=username,
                                  rate=new_rate_limit))
            from app.radius.integration.radius_coa import CoaResult
            return CoaResult(ok=False, code=0, code_name="no_active_session",
                              reply_message="")
        monkeypatch.setattr(
            "app.radius.integration.radius_coa.change_user_rate", _fake)

        sub = Subscriber(id=1, tenant_id=1, username="ali", password="p",
                         plan_id=plan.id)
        sqlite_adapter._push_coa_rate_if_active(sub)
        assert captured == {
            "tenant_id": 1, "username": "ali", "rate": "2048k/10240k",
        }


def test_push_coa_uses_burst_raw_when_set(app, monkeypatch):
    """If plan.burst_raw is set, it takes priority over speed_up/down_kbps —
    same precedence as _build_accept_attrs (policy_engine)."""
    with app.app_context():
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.db.repos import plans_repo
        from app.radius.integration import sqlite_adapter

        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, tenant_id=1, name="Burst", enabled=True,
            speed_up_kbps=1024, speed_down_kbps=4096,
            burst_raw="1M/4M 2M/8M 3M/12M 30/60",
        ))

        captured = {}
        def _fake(tenant_id, username, *, new_rate_limit):
            captured["rate"] = new_rate_limit
            from app.radius.integration.radius_coa import CoaResult
            return CoaResult(ok=True, code=44, code_name="CoA-ACK",
                              reply_message="")
        monkeypatch.setattr(
            "app.radius.integration.radius_coa.change_user_rate", _fake)

        sub = Subscriber(id=1, tenant_id=1, username="ali", password="p",
                         plan_id=plan.id)
        sqlite_adapter._push_coa_rate_if_active(sub)
        assert captured["rate"] == "1M/4M 2M/8M 3M/12M 30/60"
