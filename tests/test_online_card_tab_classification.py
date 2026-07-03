# -*- coding: utf-8 -*-
"""BUG: cards showed in the /online «المشتركون» tab («الكروت مع المشتركين»).

Live case: card usernames 3172911 / 5698046 (migrated batch, >16k cards)
appeared in the SUBSCRIBERS tab. Root cause: the tab split was built from
``get_cards_service().list_cards(limit=10000)`` ordered ``id DESC`` — any
tenant with more than 10,000 cards silently dropped its OLDEST cards from the
card set, so those usernames fell through to the subscribers tab.

The split now uses ``live_sessions.resolve_real_types`` over just the online
usernames (uncapped IN-query on cards/subscribers), the same discriminator the
FIX-A real-only filter and the auth path use: a ``cards`` row wins; a
``subscribers`` mirror row with ``user_type='card'`` is a card too.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_tabclass_")
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
    return datetime.utcnow().isoformat() + "Z"


@pytest.fixture
def seeded(app, monkeypatch):
    """One subscriber (ahmad) + one card (3172911) that ALSO carries a mirror
    subscribers row with the default user_type — the migrated-card shape.
    The cards service is stubbed to return an EMPTY list, simulating the
    >10,000-cards tenant where the old capped lookup missed this card."""
    from types import SimpleNamespace

    with app.app_context():
        from app.radius.db.connection import transaction
        now = _now()
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, created_at) "
                "VALUES (1,?,?,?)", ("ahmad", "pw", now))
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, created_at) "
                "VALUES (1,?,?,?)", ("4 ميجا فري لانسر", "Hotspot", now)).lastrowid
            bid = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
                "created_at) VALUES (1,?,?,?,?)", ("mig-b", pid, 1, now)).lastrowid
            c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
                "created_at) VALUES (1,?,?,?,?,?)",
                (bid, "3172911", "pw", pid, now))
            # migrated-card mirror row: user_type left at its 'subscriber'
            # default — the cards row must still win the classification.
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, created_at) "
                "VALUES (1,?,?,?)", ("3172911", "pw", now))
            for user, sid in (("ahmad", "s-ahmad"), ("3172911", "s-card")):
                c.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, "
                    "username, nasipaddress, callingstationid, framedipaddress, "
                    "acctstarttime, acctupdatetime) VALUES (1,?,?,?,?,?,?,?,?)",
                    (sid, f"u-{sid}", user, "10.10.0.2", "AA:BB:CC:DD:EE:FF",
                     "10.20.30.254", now, now))

    # The >10k trap: the capped cards-service list does NOT contain the card.
    fake_service = SimpleNamespace(list_cards=lambda **kw: [])
    monkeypatch.setattr("app.radius.services.cards.get_cards_service",
                        lambda: fake_service)
    return app


def _login(app):
    from uuid import uuid4
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"tab_{uuid4().hex[:10]}"
        admins_repo.create_admin(username=u, password="tab-pass",
                                 full_name="Tab Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "tab-pass"})
    assert res.status_code in {302, 303}
    return client


def test_card_lands_only_in_cards_tab(seeded):
    body = _login(seeded).get(
        "/admin/radius/online?type=card").get_data(as_text=True)
    assert "3172911" in body, "card must appear in the «الكروت» tab"
    assert "ahmad" not in body, "subscriber must NOT appear in the cards tab"


def test_card_never_lands_in_subscribers_tab(seeded):
    """The live symptom: 3172911 showed under «المشتركون». Must not."""
    body = _login(seeded).get("/admin/radius/online").get_data(as_text=True)
    assert "ahmad" in body, "subscriber must appear in the subscribers tab"
    assert "3172911" not in body, \
        "a CARD username must never appear in the subscribers tab"


def test_resolver_classifies_mirror_row_card_as_card(seeded):
    """cards row wins even when the mirror subscribers row says 'subscriber';
    and a subscribers row with user_type='card' is a card without a cards row."""
    with seeded.app_context():
        from app.radius.db.connection import db
        from app.radius.services import live_sessions as ls
        db().execute(
            "INSERT INTO subscribers(tenant_id, username, password, user_type, "
            "created_at) VALUES (1,'mirror-card','pw','card',?)", (_now(),))
        kinds = ls.resolve_real_types(1, ["ahmad", "3172911", "mirror-card"])
    assert kinds["ahmad"] == "subscriber"
    assert kinds["3172911"] == "card"        # cards row wins over mirror default
    assert kinds["mirror-card"] == "card"    # user_type='card' mirror alone


def test_online_list_user_type_prefers_cards_row(seeded):
    """The adapter's per-row user_type must also say 'card' for the migrated
    shape (cards row + default-user_type mirror subscribers row)."""
    with seeded.app_context():
        from app.radius.integration.sqlite_adapter import SqliteAdapter
        by_user = {s.username: s.user_type
                   for s in SqliteAdapter().list_online_from_radacct(limit=50)}
    assert by_user["3172911"] == "card"
    assert by_user["ahmad"] == "subscriber"
