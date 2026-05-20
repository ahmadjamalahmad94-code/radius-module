"""R12.2 regression: /admin/radius/online must show subscribers ONLY,
and /admin/radius/online?type=card must show cards ONLY.

Pre-R12.2 both screens were mixed: a card username (e.g. "2044") would
show up in the "المشتركون المتصلون" list and confuse the admin. The
separation matches the existing /users vs /cards screens — each list
keeps to its own audience.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r122_")
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


def _seed_radacct(conn, *, username, session_id):
    """Open radacct row for `username` so list_online_from_radacct returns it."""
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, framedipaddress, callingstationid, acctstarttime)
        VALUES (?,?,?,?,?,?,?,?)
    """, (1, session_id, f"u-{session_id}", username,
           "10.10.0.2", "10.20.30.254", "AA:BB:CC:DD:EE:FF", now))


@pytest.fixture
def seeded(app, monkeypatch):
    """Two live sessions: ahmad is a regular subscriber, 2044 is a card.
    We monkeypatch the cards service to declare {2044} as the set of card
    usernames — this avoids fighting the cards table's FK chain (batches
    + plans) which is irrelevant to what we're testing (the route's
    filter logic).
    """
    from types import SimpleNamespace

    with app.app_context():
        from app.radius.db.connection import transaction

        with transaction() as c:
            _seed_radacct(c, username="ahmad", session_id="s-ahmad")
            _seed_radacct(c, username="2044", session_id="s-card")

    fake_cards = [SimpleNamespace(username="2044")]
    fake_service = SimpleNamespace(list_cards=lambda **kw: fake_cards)
    monkeypatch.setattr(
        "app.radius.services.cards.get_cards_service",
        lambda: fake_service)
    return app


def _logged_in(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
    return client


def test_default_view_excludes_cards(seeded):
    resp = _logged_in(seeded).get("/admin/radius/online")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "ahmad" in body, "default view must include subscribers"
    assert "2044" not in body, "default view must EXCLUDE cards (R12.2)"


def test_card_view_excludes_subscribers(seeded):
    resp = _logged_in(seeded).get("/admin/radius/online?type=card")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "2044" in body, "card view must include cards"
    assert "ahmad" not in body, "card view must EXCLUDE subscribers (R12.2)"
