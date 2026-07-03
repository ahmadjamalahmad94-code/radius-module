# -*- coding: utf-8 -*-
"""/admin/radius/online must render shell-first — no router probe on render.

The owner's recurring complaint: the «المتصلون الآن» page hung during render
until it finished probing every router's reachability; one slow/dead router
stalled the whole page load. Root cause: online_list() called
connected_live.refresh_and_reconcile() synchronously in the request path — it
probes each router sequentially at ~4s timeout each, so a dead router froze the
page until it timed out.

Fix: the GET does ZERO blocking network I/O. The probe + reconcile (+ temp-speed
revert-CoA) moved to the async /online/live-status endpoint, which the page
calls on load and polls. These tests pin that contract so it can't regress.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_shell_first_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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
    u = f"shell_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="shell-pass", full_name="Shell Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "shell-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def test_live_status_endpoint_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/online/live-status" in rules


def test_online_get_does_not_probe_routers(app, client, monkeypatch):
    """The heart of the fix: rendering /online must NOT call
    refresh_and_reconcile (the sequential per-router probe that hung the page).
    We spy on it and assert zero calls during the GET."""
    calls = []
    from app.radius.services import connected_live

    def _spy(*a, **k):
        calls.append((a, k))
        return {"probed": 0}

    monkeypatch.setattr(connected_live, "refresh_and_reconcile", _spy)

    _login(client)
    res = client.get("/admin/radius/online")
    assert res.status_code == 200
    assert calls == [], (
        "online_list must not probe routers in the request path — "
        f"refresh_and_reconcile was called {len(calls)} time(s)")


def test_online_get_survives_even_if_probe_would_hang(app, client, monkeypatch):
    """Belt-and-braces: even if the probe were catastrophically broken, the GET
    must still render (it doesn't touch it). Patch it to raise; page is 200."""
    from app.radius.services import connected_live

    def _boom(*a, **k):
        raise RuntimeError("router probe exploded / hung")

    monkeypatch.setattr(connected_live, "refresh_and_reconcile", _boom)

    _login(client)
    res = client.get("/admin/radius/online")
    assert res.status_code == 200


def test_live_status_does_the_probe(app, client, monkeypatch):
    """The probe must have MOVED to the async endpoint — assert live-status
    calls refresh_and_reconcile (so reachability still updates, just off the
    render path)."""
    calls = []
    from app.radius.services import connected_live

    def _spy(*a, **k):
        calls.append((a, k))
        return {"probed": 0}

    monkeypatch.setattr(connected_live, "refresh_and_reconcile", _spy)

    _login(client)
    res = client.get("/admin/radius/online/live-status")
    assert res.status_code == 200
    assert len(calls) == 1
    body = res.get_json()
    assert "unreachable_routers" in body


def test_online_page_wires_async_status(app, client):
    """The rendered shell must carry the async wiring: the live-status URL, the
    router-status loading chip, and the JS-managed unreachable banner."""
    _login(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    assert "data-online-live-url" in html
    assert "/admin/radius/online/live-status" in html
    assert "data-online-router-status" in html
    assert "data-online-unreachable-banner" in html
    # The loading indicator text is present (shell-first: shown until the poll
    # resolves).
    assert "جارٍ فحص حالة الراوترات" in html
