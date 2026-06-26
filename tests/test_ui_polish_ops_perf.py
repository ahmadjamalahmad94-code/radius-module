# -*- coding: utf-8 -*-
"""UI polish + operations perf follow-ups.

  * /mt/operations renders its SHELL without a synchronous connection check;
    the radacct «متصل» count comes lazily from /mt/operations/live.
  * «الوصول البعيد» nav tab shows a green indicator ONLY when a remote
    (WinBox) session is currently open.
  * onboarding code card uses real window-dots (not a clipped box-shadow) +
    the custom scrollbar utility.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_polish_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "187.77.70.18")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.50.0.0/24")
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


def _login(client):
    from app.radius.db.repos import admins_repo
    u = f"a_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw", full_name="A",
                             is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "pw"}, follow_redirects=False)
    assert res.status_code in {302, 303}


def _nas(app, *, nas_id, name, address, vpn_peer=""):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor, nas_type,
                     enabled, created_at, connection_mode, api_user,
                     api_password, vpn_peer_address)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'vpn', 'hr', 'pw', ?)""",
                (nas_id, name, address, now, vpn_peer))


def _session(app, *, nasip):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow()
        with transaction() as c:
            c.execute(
                """INSERT INTO radacct (tenant_id, username, nasipaddress,
                    acctstarttime, acctupdatetime, acctstoptime)
                   VALUES (1, 'u', ?, ?, ?, NULL)""",
                (nasip, (now - timedelta(minutes=2)).isoformat() + "Z",
                 (now - timedelta(seconds=20)).isoformat() + "Z"))


def _remote(app, *, router_id, minutes=20, always_on=0):
    """Open a remote-access session expiring `minutes` from now (or persistent)."""
    with app.app_context():
        from app.radius.db.repos import router_remote_sessions_repo as sr
        exp = "" if always_on else (
            datetime.utcnow() + timedelta(minutes=minutes)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        sr.create_session(
            tenant_id=1, router_id=router_id, service="winbox",
            public_port=51000 + router_id, tunnel_ip="10.50.0.2", dst_port=8291,
            source_ip="198.51.100.9", opened_by="admin", expires_at=exp,
            always_on=always_on)


# ─── operations perf: lazy connection check ──────────────────────────

def test_operations_shell_has_no_sync_connection_check(app, client):
    _nas(app, nas_id=1, name="r1", address="203.0.113.1")
    with app.app_context():
        _login(client)
        html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    # shell wires the lazy seed endpoint + rows start in the «جارٍ الفحص…» skeleton
    assert "data-mt-live-url=" in html
    assert "/admin/radius/mt/operations/live" in html
    assert "جارٍ الفحص" in html
    # connected KPI starts unknown («—»), not a server-computed number
    assert "—" in html


def test_operations_live_endpoint_counts_radacct(app, client):
    _nas(app, nas_id=1, name="tun", address="198.51.100.7", vpn_peer="10.10.0.5")
    _nas(app, nas_id=2, name="dir", address="203.0.113.9")
    _nas(app, nas_id=3, name="idle", address="203.0.113.10")
    _session(app, nasip="10.10.0.5")     # router 1 online (via tunnel IP)
    _session(app, nasip="203.0.113.9")   # router 2 online (via public IP)
    with app.app_context():
        _login(client)
        body = client.get("/admin/radius/mt/operations/live").get_json()
    assert body["ok"] is True
    assert body["connected"] == 2
    assert body["routers"]["1"]["online"] is True
    assert body["routers"]["2"]["online"] is True
    assert body["routers"]["3"]["online"] is False


# ─── remote-access nav indicator ─────────────────────────────────────

def test_nav_shows_green_indicator_when_remote_session_active(app, client):
    _nas(app, nas_id=1, name="r1", address="203.0.113.1")
    _remote(app, router_id=1, minutes=20)
    with app.app_context():
        _login(client)
        html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert "data-mt-remote-badge" in html          # the badge element is rendered
    assert 'data-mt-remote-active="1"' in html


def test_nav_no_indicator_when_no_active_session(app, client):
    _nas(app, nas_id=1, name="r1", address="203.0.113.1")
    with app.app_context():
        _login(client)
        html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert "data-mt-remote-badge" not in html      # no badge element when idle
    assert 'data-mt-remote-active="0"' in html


def test_active_session_count_excludes_expired(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        # one valid, one persistent, one already-expired
        sr.create_session(tenant_id=1, router_id=1, service="winbox",
                          public_port=51001, tunnel_ip="10.50.0.2", dst_port=8291,
                          source_ip="x", opened_by="a",
                          expires_at=(datetime.utcnow() + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        sr.create_session(tenant_id=1, router_id=2, service="winbox",
                          public_port=51002, tunnel_ip="10.50.0.3", dst_port=8291,
                          source_ip="x", opened_by="a", expires_at="", always_on=1)
        sr.create_session(tenant_id=1, router_id=3, service="winbox",
                          public_port=51003, tunnel_ip="10.50.0.4", dst_port=8291,
                          source_ip="x", opened_by="a",
                          expires_at=(datetime.utcnow() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert ra.active_session_count(1) == 2     # valid + persistent, not the expired one


# ─── onboarding code card: no clipped dots + custom scrollbar ────────

def test_onboarding_card_dots_and_scrollbar(app, client):
    from app.radius.db.connection import db
    with app.app_context():
        _login(client)
        token_pg = client.get("/admin/radius/mt/setup")
        with client.session_transaction() as s:
            tok = s["_csrf_token"]
        client.post("/admin/radius/mt/setup",
                    data={"_csrf_token": tok, "name": "CCR-OB",
                          "ros_version": "6", "v6_mode": "sstp_mgmt",
                          "server_ip": "187.77.70.18"})
        nid = int(db().execute(
            "SELECT id FROM nas_devices WHERE name='CCR-OB'").fetchone()["id"])
        html = client.get(
            f"/admin/radius/mt/{nid}/onboarding-script").get_data(as_text=True)
    # real 3-dot window header (not the clipped box-shadow hack)
    assert 'class="ob-dots"' in html
    assert "<i></i><i></i><i></i>" in html
    # custom on-brand scrollbar applied to the code surface
    assert "ob-code hb-scroll hb-scroll--dark" in html
