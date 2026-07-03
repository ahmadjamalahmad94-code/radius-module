# -*- coding: utf-8 -*-
"""/online: the router-status chip and the «راوتر غير متصل» banner must never
contradict each other.

Live regression (screenshot-confirmed): a GREEN chip «كل الراوترات متصلة» and
the RED unreachable banner were visible AT THE SAME TIME. Root cause: the
banner carried an inline style="display:flex" — an inline display rule beats
the UA's [hidden]{display:none}, so the hidden attribute (set by the server
render and by the async poll's applyUnreachable()) never actually hid the
banner. The chip (fed from the same /online/live-status payload) was right;
the banner was a zombie. Same CSS trap as the dashboard card's .mt-empty.

Fix under test: the banner's layout lives in a CSS class (.ol-unreach-banner)
with an explicit [hidden]{display:none !important} kill-rule, and the element
carries NO inline display. Data-wise both chip and banner are driven by the
one unreachable_routers list from /online/live-status.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_chipbanner_")
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
    u = f"chip_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="chip-pass", full_name="Chip Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "chip-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


IP_UP = "203.0.113.10"
IP_DOWN = "203.0.113.11"


def _seed_routers(app) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            for i, (name, ip) in enumerate(
                    [("rtr-up", IP_UP), ("rtr-down", IP_DOWN)], start=1):
                c.execute(
                    """INSERT INTO nas_devices
                        (id, tenant_id, name, address, secret, vendor,
                         nas_type, enabled, created_at, connection_mode)
                       VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot', 1,
                               ?, 'direct')""",
                    (i, name, ip, now),
                )


def _mark(app, *, up: list[str] = (), down: list[str] = ()) -> None:
    with app.app_context():
        from app.radius.services import nas_liveness
        for ip in up:
            nas_liveness.record_reachable(1, ip, active_count=0)
        for ip in down:
            nas_liveness.record_unreachable(1, ip)


def _no_probe(app, monkeypatch):
    """live-status probes routers (refresh_and_reconcile) — no-op it so tests
    read the seeded liveness state instead of timing out on fake IPs."""
    from app.radius.services import connected_live
    monkeypatch.setattr(connected_live, "refresh_and_reconcile",
                        lambda *a, **k: {"probed": 0})


def _banner_tag(html: str) -> str:
    m = re.search(r"<div[^>]*data-online-unreachable-banner[^>]*>", html)
    assert m, "unreachable banner element missing from /online"
    return m.group(0)


# ───────────────────── one source of truth: the JSON both consume ────────────

def test_live_status_reports_the_unreachable_router(app, client, monkeypatch):
    _seed_routers(app)
    _mark(app, up=[IP_UP], down=[IP_DOWN])
    _no_probe(app, monkeypatch)
    _login(client)
    body = client.get("/admin/radius/online/live-status").get_json()
    assert body["unreachable_routers"] == ["rtr-down"]


def test_live_status_empty_when_all_reachable(app, client, monkeypatch):
    _seed_routers(app)
    _mark(app, up=[IP_UP, IP_DOWN])
    _no_probe(app, monkeypatch)
    _login(client)
    body = client.get("/admin/radius/online/live-status").get_json()
    assert body["unreachable_routers"] == []


# ───────────────────── server-rendered initial state ─────────────────────────

def test_mixed_reachability_renders_banner_visible(app, client, monkeypatch):
    """One up + one down → the banner must render WITHOUT the hidden attr."""
    _seed_routers(app)
    _mark(app, up=[IP_UP], down=[IP_DOWN])
    _no_probe(app, monkeypatch)
    _login(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    tag = _banner_tag(html)
    assert "hidden" not in tag
    assert "rtr-down" in html


def test_all_reachable_renders_banner_hidden(app, client, monkeypatch):
    """All up → the banner must carry hidden (and the kill-rule makes that
    actually invisible — no green-chip-plus-banner contradiction)."""
    _seed_routers(app)
    _mark(app, up=[IP_UP, IP_DOWN])
    _no_probe(app, monkeypatch)
    _login(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    tag = _banner_tag(html)
    assert "hidden" in tag


# ───────────────────── the CSS regression pin itself ─────────────────────────

def test_banner_has_no_inline_display_and_kill_rule_exists(app, client):
    """The exact regression: an inline display:flex on the banner overrode the
    hidden attribute so it could never hide. The element must carry NO inline
    style, use the class, and the [hidden] kill-rule must ship on the page."""
    _login(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    tag = _banner_tag(html)
    assert "style=" not in tag, "banner must not carry inline styles (display beats hidden)"
    assert "ol-unreach-banner" in tag
    assert ".ol-unreach-banner[hidden]{display:none !important}" in html


def test_chip_and_banner_driven_by_same_poll_payload(app, client):
    """Structural pin: the page JS must set BOTH the chip state and the banner
    visibility inside one function fed by the live-status unreachable list —
    not from two different signals."""
    _login(client)
    html = client.get("/admin/radius/online").get_data(as_text=True)
    # applyUnreachable() is the single consumer that flips both.
    i = html.find("function applyUnreachable")
    assert i != -1
    block = html[i:i + 700]
    assert "banner.hidden" in block
    assert "setChip" in block
