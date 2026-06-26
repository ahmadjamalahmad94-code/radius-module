# -*- coding: utf-8 -*-
"""WireGuard management-tunnel page (v7 parallel to the SSTP/PPTP page).

Covers the new route + service end-to-end:
  * GET  /mt/wg-peers                renders on the unified design system
  * lists ONLY v7 WireGuard-managed routers (v6 SSTP/PPTP excluded)
  * honest empty + "server not configured" states (no faked data)
  * POST /mt/wg-peers/regenerate     rotates keys on a configured WG server
  * POST /mt/wg-peers/remove         removes the host peer file
  * permission/route registration parity with the SSTP page

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_wg_ui_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    # WG server env: configured so regenerate is exercisable. Peers dir is a
    # fresh temp dir (writable) so provision/deprovision actually run.
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", tempfile.mkdtemp())
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "U" * 43 + "=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "187.77.70.18:51820")
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
                      data={"username": u, "password": "pw"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _csrf(client, url="/admin/radius/mt/wg-peers"):
    client.get(url)
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _make_router(name, *, ros="7", mode="vpn", mtype="", pubkey="", ip=""):
    """Insert a NAS row directly (the v7 WG wizard path needs host WG state we
    don't have in CI) and stamp the WG/v6 columns the page filters on."""
    from app.radius.core.types import NasDevice
    from app.radius.services.devices import get_nas_devices_service
    from app.radius.db.connection import transaction
    dev = NasDevice(
        id=None,
        name=name, address=(ip or "10.10.0.50"), secret="s3cr3t-radius",
        vendor="mikrotik", nas_type="hotspot", api_port=8728,
        api_user="hobe-api", api_password="x", api_use_tls=False,
        enabled=True, monitoring_enabled=True,
    )
    saved = get_nas_devices_service().create(actor="test", device=dev)
    with transaction() as c:
        c.execute(
            "UPDATE nas_devices SET ros_version=?, connection_mode=?, "
            "       management_tunnel_type=?, vpn_public_key=?, vpn_peer_address=? "
            "WHERE id=?",
            (ros, mode, mtype, pubkey, (ip or "10.10.0.50"), saved.id),
        )
    return int(saved.id)


def test_routes_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    for ep in ("radius.mt_wg_peers", "radius.mt_wg_peer_regenerate",
               "radius.mt_wg_peer_remove"):
        assert ep in rules


def test_page_uses_design_system_and_empty_state(app, client):
    with app.app_context():
        _login(client)
        html = client.get("/admin/radius/mt/wg-peers").get_data(as_text=True)
        assert "uds-hero" in html and "uds-hero-kpis" in html
        # honest empty state, never a faked table
        assert "hub-empty" in html
        # sibling nav tab present
        assert "اتصالات WireGuard" in html


def test_lists_wg_router_real_fields(app, client):
    with app.app_context():
        _login(client)
        pub = "ABCdef0123456789ABCdef0123456789ABCdef01234="
        _make_router("CCR-WG-1", ros="7", mode="vpn", pubkey=pub, ip="10.10.0.7")
        html = client.get("/admin/radius/mt/wg-peers").get_data(as_text=True)
        assert "CCR-WG-1" in html          # router name
        assert "10.10.0.7" in html          # tunnel IP (real)
        assert pub[:10] in html             # public key (shortened, real)
        assert 'data-uds-table' in html
        # KPI strip rendered
        assert "Peer على الخادم" in html


def test_v6_sstp_router_excluded(app, client):
    with app.app_context():
        _login(client)
        # a v6 SSTP row also has connection_mode='vpn' — must NOT appear here
        _make_router("CCR-SSTP", ros="6", mode="vpn", mtype="sstp_mgmt",
                     pubkey="", ip="10.50.0.9")
        html = client.get("/admin/radius/mt/wg-peers").get_data(as_text=True)
        assert "CCR-SSTP" not in html
        assert "hub-empty" in html  # no WG routers → empty state


def test_regenerate_rotates_key_and_reveals_once(app, client):
    with app.app_context():
        _login(client)
        pub = "OLDkey0123456789OLDkey0123456789OLDkey01234="
        nas_id = _make_router("CCR-WG-2", ros="7", mode="vpn", pubkey=pub,
                              ip="10.10.0.8")
        token = _csrf(client)
        res = client.post("/admin/radius/mt/wg-peers/regenerate",
                          data={"_csrf_token": token, "nas_id": str(nas_id)},
                          follow_redirects=False)
        # redirects to the setup-script page (one-time private-key reveal)
        assert res.status_code in {302, 303}
        assert f"/mt/{nas_id}/script" in res.headers.get("Location", "")
        # the stored public key actually changed (rotated on the server)
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT vpn_public_key FROM nas_devices WHERE id=?", (nas_id,)
        ).fetchone()
        assert row["vpn_public_key"] and row["vpn_public_key"] != pub
        # the rendered script reveals the fresh WG block (private key once)
        html = client.get(f"/admin/radius/mt/{nas_id}/script").get_data(as_text=True)
        assert "wireguard" in html.lower() or "PrivateKey" in html


def test_remove_peer_clears_key(app, client):
    with app.app_context():
        _login(client)
        # provision a real peer first via regenerate, then remove it
        nas_id = _make_router("CCR-WG-3", ros="7", mode="vpn",
                              pubkey="seed", ip="10.10.0.11")
        token = _csrf(client)
        client.post("/admin/radius/mt/wg-peers/regenerate",
                    data={"_csrf_token": token, "nas_id": str(nas_id)},
                    follow_redirects=False)
        token = _csrf(client)
        res = client.post("/admin/radius/mt/wg-peers/remove",
                          data={"_csrf_token": token, "nas_id": str(nas_id)},
                          follow_redirects=True)
        assert res.status_code == 200
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT vpn_public_key FROM nas_devices WHERE id=?", (nas_id,)
        ).fetchone()
        assert (row["vpn_public_key"] or "") == ""   # cleared


def test_mutation_rejects_non_wg_and_unknown(app, client):
    with app.app_context():
        _login(client)
        v6 = _make_router("CCR-SSTP2", ros="6", mode="vpn", mtype="sstp_mgmt",
                          ip="10.50.0.12")
        token = _csrf(client)
        # a v6 router id is not a WG peer → 404
        res = client.post("/admin/radius/mt/wg-peers/regenerate",
                          data={"_csrf_token": token, "nas_id": str(v6)})
        assert res.status_code == 404
        # unknown id → 404
        token = _csrf(client)
        res = client.post("/admin/radius/mt/wg-peers/remove",
                          data={"_csrf_token": token, "nas_id": "99999"})
        assert res.status_code == 404
