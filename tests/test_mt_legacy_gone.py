"""N2 — every legacy /admin/radius/mt/* route returns 410 Gone.

The endpoint *names* stay registered (so url_for() in older
templates keeps resolving) but every handler renders the
deprecation page now. Bookmarks / external links break loudly
instead of silently redirecting to a different concept.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_n2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # Phase M env so the wizard URLs resolve in the 410 template.
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "X" * 43 + "=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "1.2.3.4:51820")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    username = f"n2_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username, password="n2-pass",
        full_name="N2 Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "n2-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    """Mint a CSRF token via any GET, then read it from session."""
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


# ─── GET endpoints ───────────────────────────────────────────────


def test_mt_list_returns_410(app, client):
    _login(client)
    res = client.get("/admin/radius/mt")
    assert res.status_code == 410
    html = res.get_data(as_text=True)
    assert "لم تعد متاحة" in html
    # Links to the supported replacements are present.
    assert "/admin/radius/mt/setup" in html
    assert "/admin/radius/mt/operations" in html


def test_mt_new_returns_410(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/new")
    assert res.status_code == 410


def test_mt_edit_returns_410(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/1/edit")
    assert res.status_code == 410


# ─── POST endpoints ──────────────────────────────────────────────


def test_mt_create_returns_410(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt",
        data={"_csrf_token": token, "name": "x", "host": "1.1.1.1"},
    )
    assert res.status_code == 410


def test_mt_update_returns_410(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1",
        data={"_csrf_token": token, "name": "y"},
    )
    assert res.status_code == 410


def test_mt_delete_returns_410(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/2/delete", data={"_csrf_token": token},
    )
    assert res.status_code == 410


def test_mt_test_returns_410(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/2/test", data={"_csrf_token": token},
    )
    assert res.status_code == 410


# ─── url_for still resolves (endpoint names preserved) ───────────


def test_url_for_mt_list_still_resolves(app):
    """If we'd deleted the endpoint names, every template doing
    url_for('radius.mt_list') would 500. The N2 design keeps
    them alive — they just point to the 410 page."""
    with app.test_request_context():
        from flask import url_for
        assert url_for("radius.mt_list") == "/admin/radius/mt"
        assert url_for("radius.mt_new") == "/admin/radius/mt/new"
        # cid=42 is fine — it just routes to the gone page
        assert url_for("radius.mt_edit", cid=42) == "/admin/radius/mt/42/edit"


# ─── No write to mikrotik_configs from the deprecated handlers ──


def test_mt_create_does_not_write_to_legacy_table(app, client):
    """The whole point of N2: even if someone POSTs to the
    deprecated URL, no row gets created in mikrotik_configs.

    Post-N3 the table itself is gone. The behaviour we still
    want to pin is: the request returns 410 (not 500), and
    nothing was written.
    """
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt",
        data={
            "_csrf_token": token,
            "name": "should-not-appear",
            "host": "10.99.99.99",
        },
    )
    assert res.status_code == 410
    with app.app_context():
        from app.radius.db.connection import db
        # Post-N3 the table is gone — sqlite_master is the canonical
        # check.
        tbl = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='mikrotik_configs'"
        ).fetchone()
    assert tbl is None, "mikrotik_configs should be dropped by N3"
