# -*- coding: utf-8 -*-
"""Subscribers-list row state effect (owner request, effect not text):

  red   = disabled   · amber = expired
  blue  = expires within 3 days · green = connected right now (live session)

The route computes one state per row (priority: disabled > expired >
expiring > online — lifecycle beats the momentary connection) and the row
carries ``sub-state--<state>`` so CSS renders an edge accent + tint.
«Connected» uses the same live-radacct definition as «المتصلون الآن»
(live_sessions.live_usernames)."""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_rowstate_")
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


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat() + "Z"


@pytest.fixture
def seeded(app):
    """Five subscribers, one per state (+ one neutral):
    off=disabled · gone=expired · soon=expires in 2 days ·
    live=enabled with an open live radacct row · plain=nothing special."""
    now = _dt.datetime.utcnow()
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            def sub(username, *, status="enabled", expire_at=None):
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, password, "
                    "status, expire_at, created_at) VALUES (1,?,?,?,?,?)",
                    (username, "pw", status,
                     _iso(expire_at) if expire_at else None, _iso(now)))
            sub("off",   status="disabled")
            sub("gone",  status="expired")
            sub("soon",  expire_at=now + _dt.timedelta(days=2))
            sub("live",  expire_at=now + _dt.timedelta(days=30))
            sub("plain")
            c.execute(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                "username, nasipaddress, acctstarttime, acctupdatetime) "
                "VALUES (1,'st-live','st-live-u','live','10.10.0.2',?,?)",
                (_iso(now), _iso(now)))
    return app


def _login(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"rs_{uuid4().hex[:10]}"
        admins_repo.create_admin(username=u, password="rs-pass",
                                 full_name="RS Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "rs-pass"})
    assert res.status_code in {302, 303}
    return client


def _row_class(body: str, username: str) -> str:
    """The class attribute of the <tr> carrying data-username=<username>."""
    m = re.search(
        r"<tr\s+(?:class=\"([^\"]*)\"[^>]*?)?data-username=\"%s\"" % re.escape(username),
        body)
    assert m, f"row for {username} not found"
    return m.group(1) or ""


def test_each_state_gets_its_effect_class(seeded):
    body = _login(seeded).get("/admin/radius/subscribers").get_data(as_text=True)
    assert "sub-state--disabled" in _row_class(body, "off")
    assert "sub-state--expired" in _row_class(body, "gone")
    assert "sub-state--expiring" in _row_class(body, "soon")
    assert "sub-state--online" in _row_class(body, "live")
    assert "sub-state" not in _row_class(body, "plain")


def test_lifecycle_beats_online(seeded):
    """A DISABLED subscriber with a live session must show red, not green —
    the lifecycle state is what the operator must act on."""
    now = _dt.datetime.utcnow()
    with seeded.app_context():
        from app.radius.db.connection import db
        db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
            "username, nasipaddress, acctstarttime, acctupdatetime) "
            "VALUES (1,'st-off','st-off-u','off','10.10.0.2',?,?)",
            (_iso(now), _iso(now)))
    body = _login(seeded).get("/admin/radius/subscribers").get_data(as_text=True)
    cls = _row_class(body, "off")
    assert "sub-state--disabled" in cls
    assert "sub-state--online" not in cls


def test_state_effect_css_ships_with_the_page(seeded):
    body = _login(seeded).get("/admin/radius/subscribers").get_data(as_text=True)
    assert "sub-state--disabled" in body and "sub-state-pulse" in body
