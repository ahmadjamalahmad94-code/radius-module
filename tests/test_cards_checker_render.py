# -*- coding: utf-8 -*-
"""Card-checker page render regression — the found-card path must never 500.

Production traceback (live server): rendering /admin/radius/cards/checker for
an EXISTING card raised ``TypeError: 'NoneType' object is not callable`` from
``{{ hub.megahero( ... _("رجوع") ... ) }}``. Root cause: the found-card branch
ran ``{% set _ = _hero_kpis.extend([...]) %}`` — ``list.extend`` returns None,
so the throwaway assignment CLOBBERED gettext ``_`` for the rest of the render;
the next ``_("...")`` call invoked None. (The repo-wide lesson: never
``{% set _ = ... %}`` in a template — name it ``_x``.) The not-found path never
executed that set, which is why only real cards 500'd.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ccr_")
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


def _now() -> str:
    return _dt.datetime.utcnow().isoformat() + "Z"


def _login(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"ccr_{uuid4().hex[:10]}"
        admins_repo.create_admin(username=u, password="ccr-pass",
                                 full_name="CC Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "ccr-pass"})
    assert res.status_code in {302, 303}
    return client


def _seed_card(app, username="3172911"):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _now()
        with transaction() as c:
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, "
                "created_at) VALUES (1,?,?,?)",
                ("4 ميجا فري لانسر", "Hotspot", now)).lastrowid
            bid = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, "
                "count, created_at) VALUES (1,?,?,?,?)",
                ("ccr-b", pid, 1, now)).lastrowid
            c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, "
                "plan_id, created_at) VALUES (1,?,?,?,?,?)",
                (bid, username, "pw", pid, now))
            # Accounting history (one closed, one open) so the KPI strip and
            # the sessions table — the paths that ran the poisoned set — render.
            c.execute(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                "username, nasipaddress, callingstationid, acctstarttime, "
                "acctupdatetime, acctstoptime, acctsessiontime, "
                "acctinputoctets, acctoutputoctets) "
                "VALUES (1,'ccr-s1','ccr-u1',?,?,?,?,?,?,3600,1000,2000)",
                (username, "10.10.0.2", "AA:BB:CC:DD:EE:01", now, now, now))
            c.execute(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                "username, nasipaddress, callingstationid, acctstarttime, "
                "acctupdatetime, acctstoptime) "
                "VALUES (1,'ccr-s2','ccr-u2',?,?,?,?,?,NULL)",
                (username, "10.10.0.2", "AA:BB:CC:DD:EE:02", now, now))


def test_checker_existing_card_renders_200(app):
    """The live-crash scenario: query for a REAL card → 200, never 500."""
    _seed_card(app)
    client = _login(app)
    res = client.get("/admin/radius/cards/checker?query=3172911")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "3172911" in body
    # The exact string whose gettext call was invoking None must be rendered.
    assert "رجوع" in body


def test_checker_not_found_still_renders_200(app):
    client = _login(app)
    res = client.get("/admin/radius/cards/checker?query=no-such-card")
    assert res.status_code == 200


def test_checker_template_never_clobbers_gettext_underscore():
    """Guard the whole template: no ``{% set _ = ... %}`` may reappear —
    it silently replaces gettext with None for the rest of the render."""
    path = os.path.join(os.path.dirname(__file__), "..", "app", "templates",
                        "radius", "cards_checker_v2.html")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "{% set _ = " not in src
    assert "{% set _ =" not in src
