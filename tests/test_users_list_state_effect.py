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
    live=enabled with an open live radacct row · plain=nothing special.

    The viewing admin is created here and the subscribers are stamped with
    ``manager_id=<that admin>`` so they stay visible under the owner-scoping
    of the list even when the admin is a plain (non-owner) manager — the
    scope filter matches ``manager_id`` (see ``_owner_scope_sql``). The
    admin's username is stashed on the app for ``_login`` to authenticate."""
    now = _dt.datetime.utcnow()
    with app.app_context():
        from app.radius.db.repos import admins_repo
        u = f"rs_{uuid4().hex[:10]}"
        admin = admins_repo.create_admin(username=u, password="rs-pass",
                                         full_name="RS Tester",
                                         is_super_admin=True)
        aid = int(getattr(admin, "id", 0) or 0)
        from app.radius.db.connection import transaction
        with transaction() as c:
            def sub(username, *, status="enabled", expire_at=None):
                c.execute(
                    "INSERT INTO subscribers(tenant_id, username, password, "
                    "status, expire_at, manager_id, created_by, created_at) "
                    "VALUES (1,?,?,?,?,?,?,?)",
                    (username, "pw", status,
                     _iso(expire_at) if expire_at else None, aid, aid, _iso(now)))
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
    app._rs_login_user = u  # type: ignore[attr-defined]
    return app


def _login(app):
    client = app.test_client()
    u = getattr(app, "_rs_login_user", None)
    if not u:  # standalone use — mint a fresh super admin
        from app.radius.db.repos import admins_repo
        u = f"rs_{uuid4().hex[:10]}"
        with app.app_context():
            admins_repo.create_admin(username=u, password="rs-pass",
                                     full_name="RS Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "rs-pass"})
    assert res.status_code in {302, 303}
    return client


def _row_class(body: str, username: str) -> str:
    """The class attribute of the <tr> carrying data-username=<username>.

    Attribute order inside <tr> is not guaranteed — the row also carries
    ``data-rowctx`` (right-click context menu) and ``title`` — so we match
    the whole opening tag up to ``data-username`` and pull ``class`` out of
    it wherever it sits, rather than assuming class-then-data-username."""
    m = re.search(
        r"<tr\b[^>]*?\bdata-username=\"%s\"" % re.escape(username), body)
    assert m, f"row for {username} not found"
    cm = re.search(r"\bclass=\"([^\"]*)\"", m.group(0))
    return cm.group(1) if cm else ""


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
