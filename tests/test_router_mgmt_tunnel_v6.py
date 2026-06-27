# -*- coding: utf-8 -*-
"""v6 router MANAGEMENT tunnel over SSTP/PPTP — mirror of the v7 WireGuard
onboarding. Covers: transport selection (v6→SSTP/PPTP), tunnel credential
generated + RADIUS-authable (radcheck/radreply), stable Framed-IP assigned +
persisted (092 columns), sstp-client/pptp-client script correct + points at
the accel host, CoA targets the tunnel IP, and the v7 WG path unchanged.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import re
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_v6tun_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "v6-tunnel-secret")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    yield application
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _auth(client):
    with client.session_transaction() as s:
        s.update(admin_id=1, admin_user="t", admin_name="tester",
                 is_super_admin=True, tenant_id=1)


def _csrf(client):
    client.get("/admin/radius/mt/setup")
    with client.session_transaction() as s:
        return s["_csrf_token"]


# ════════════ 1) transport selection (version → tunnel) ════════════
def test_v6_selects_sstp_mgmt_transport(app):
    from app.radius.services import routeros_caps as caps
    assert caps.recommended_management_tunnel("6") == "sstp_mgmt"
    assert caps.recommended_management_tunnel("6.49.7") == "sstp_mgmt"
    assert caps.supports_sstp_mgmt("6") is True
    assert caps.supports_wireguard("6") is False
    # v7 still WireGuard (unchanged)
    assert caps.recommended_management_tunnel("7") == "wireguard"


# ════════════ 2) credential generated + RADIUS-authable ════════════
def test_provision_writes_radius_authable_user(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        res = rmt.provision_tunnel("MT-Alpha", transport="sstp", tenant_id=1)
        assert res.tunnel_username == "rtr-MT-Alpha"
        assert res.tunnel_password and len(res.tunnel_password) >= 16
        # radcheck holds the password (auth) — the canonical RADIUS store
        chk = {(r["attribute"], r["op"]): r["value"]
               for r in fr.list_user_check(1, res.tunnel_username)}
        assert chk[("Cleartext-Password", ":=")] == res.tunnel_password
        # radreply pins the stable Framed-IP
        rep = {(r["attribute"], r["op"]): r["value"]
               for r in fr.list_user_reply(1, res.tunnel_username)}
        assert rep[("Framed-IP-Address", ":=")] == str(res.tunnel_ip)


def test_tunnel_user_not_in_subscriber_list(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import subscribers_repo as sr
        rmt.provision_tunnel("MT-Beta", transport="sstp", tenant_id=1)
        # The tunnel account has NO subscribers row → invisible in the list.
        subs = sr.list_subscribers(1)
        names = [s["username"] for s in subs]
        assert "rtr-MT-Beta" not in names
        assert subs == [] or all(not n.startswith("rtr-") for n in names)


# ════════════ 3) stable Framed-IP assigned + persisted ════════════
def test_stable_ip_allocation_skips_server_and_used(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        cfg = rmt.load_config()
        # server IP is reserved (first host); first router gets the next one
        first = rmt.allocate_tunnel_ip(1, cfg=cfg, used_ips=set())
        assert str(first) != str(cfg.server_ip)
        # once an IP is "used", allocation hands out a different one
        second = rmt.allocate_tunnel_ip(1, cfg=cfg, used_ips={first})
        assert second != first and str(second) != str(cfg.server_ip)


def test_two_routers_get_distinct_persisted_ips(app):
    client = app.test_client()
    _auth(client)
    for name in ("MT-One", "MT-Two"):
        token = _csrf(client)
        client.post("/admin/radius/mt/setup", data={
            "name": name, "ros_version": "6", "v6_mode": "sstp_mgmt",
            "_csrf_token": token})
    with app.app_context():
        from app.radius.db.connection import db
        rows = db().execute(
            "SELECT name, address, management_remote_address, management_tunnel_type "
            "FROM nas_devices WHERE management_tunnel_type='sstp_mgmt' ORDER BY id"
        ).fetchall()
        ips = [r["management_remote_address"] for r in rows]
        assert len(ips) == 2 and ips[0] != ips[1]          # distinct + stable
        assert all(r["address"] == r["management_remote_address"] for r in rows)


# ════════════ 4) script renderers correct ════════════
def test_sstp_mgmt_block_correct():
    from app.radius.services.mt_provisioner import render_sstp_mgmt_block
    blk = render_sstp_mgmt_block(
        nas_name="MT-Alpha", accel_host="187.77.70.18",
        username="rtr-mt-alpha", password="Pw_123", port=443)
    assert "/interface sstp-client add" in blk
    assert "connect-to=187.77.70.18" in blk and "port=443" in blk
    assert 'user="rtr-mt-alpha"' in blk and 'password="Pw_123"' in blk
    assert "verify-server-certificate=no" in blk        # self-signed cert
    # self-signed cert reached by IP → address-from-cert re-check must be off
    # (the live ccr5 flapping cause: 49 Link Downs with the default =yes).
    assert "verify-server-address-from-certificate=no" in blk
    assert "add-default-route=no" in blk                # management-only
    # idempotent: remove our prior mgmt client (by name) BEFORE the add, and the
    # remove precedes the add so re-pasting converges to one client.
    assert "/interface sstp-client remove [find name=hr-sstp-mgmt]" in blk
    assert (blk.index("/interface sstp-client remove")
            < blk.index("/interface sstp-client add"))
    # profile=default (NOT default-encryption): SSTP is already TLS; PPP MPPE
    # on top broke the link in the live ccr4 incident (ccp/short-write).
    cmd = [ln for ln in blk.splitlines()
           if ln.startswith("/interface sstp-client add")][0]
    assert "profile=default " in cmd
    assert "default-encryption" not in cmd
    m = re.search(r"keepalive-timeout=(\d+)", cmd)
    assert m and 20 <= int(m.group(1)) <= 120


def test_pptp_mgmt_block_correct():
    from app.radius.services.mt_provisioner import render_pptp_mgmt_block
    blk = render_pptp_mgmt_block(
        nas_name="MT-Alpha", accel_host="187.77.70.18",
        username="rtr-mt-alpha", password="Pw_123")
    assert "/interface pptp-client add" in blk
    assert "connect-to=187.77.70.18" in blk
    assert 'user="rtr-mt-alpha"' in blk
    assert "add-default-route=no" in blk
    # idempotent: remove our prior mgmt client (by name) BEFORE the add.
    assert "/interface pptp-client remove [find name=hr-pptp-mgmt]" in blk
    assert (blk.index("/interface pptp-client remove")
            < blk.index("/interface pptp-client add"))
    # PPTP has no TLS server cert → no verify-server-* on the command (the SSTP
    # flap cause doesn't exist here). keepalive present for parity.
    cmd = [ln for ln in blk.splitlines()
           if ln.startswith("/interface pptp-client add")][0]
    assert "verify-server" not in cmd
    assert re.search(r"keepalive-timeout=\d+", cmd)


def test_mgmt_block_injection_rejected():
    from app.radius.services.mt_provisioner import render_sstp_mgmt_block
    from app.radius.services.data_connection import DataConnectionError
    for bad in ['a"b', "a\nb", "a;b"]:
        with pytest.raises(DataConnectionError):
            render_sstp_mgmt_block(nas_name="x", accel_host="187.77.70.18",
                                   username=bad, password="ok")


def test_tunnel_block_prepended_to_v6_script():
    from app.radius.services.mt_provisioner import (
        render_routeros_script, render_sstp_mgmt_block)
    blk = render_sstp_mgmt_block(
        nas_name="MT", accel_host="187.77.70.18",
        username="rtr-mt", password="pw")
    script = render_routeros_script(
        nas_name="MT", api_user="hr-x", api_password="p", radius_secret="s",
        server_ip="10.50.0.1", ros_version="6", tunnel_block=blk)
    # tunnel comes BEFORE the RADIUS block
    assert script.index("sstp-client") < script.index("/radius add")
    assert "address=10.50.0.1" in script        # router dials RADIUS over tunnel


# ════════════ 5) route: end-to-end onboarding ════════════
def _onboard(client, name, mode):
    token = _csrf(client)
    return client.post("/admin/radius/mt/setup", data={
        "name": name, "ros_version": "6", "v6_mode": mode,
        "_csrf_token": token}, follow_redirects=False)


def test_route_v6_sstp_onboarding_full(app):
    client = app.test_client()
    _auth(client)
    res = _onboard(client, "MT-Branch", "sstp_mgmt")
    assert res.status_code == 302
    with app.app_context():
        from app.radius.db.connection import db
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE name='MT-Branch'").fetchone())
        assert row["management_tunnel_type"] == "sstp_mgmt"
        assert row["connection_mode"] == "vpn"
        # the fixed tunnel IP is the address + vpn_peer + management_remote
        ip = row["address"]
        assert ip and row["vpn_peer_address"] == ip
        assert row["management_remote_address"] == ip
        assert row["management_tunnel_interface_name"] == "hr-sstp-mgmt"
        # FreeRADIUS client row exists for the tunnel IP (CoA secret lookup)
        nas = db().execute(
            "SELECT secret FROM nas WHERE tenant_id=1 AND nasname=?", (ip,)
        ).fetchone()
        assert nas is not None
    # script page renders the sstp-client + RADIUS-over-tunnel
    html = client.get(res.headers["Location"]).get_data(as_text=True)
    assert "sstp-client" in html and "187.77.70.18" in html
    assert "verify-server-certificate=no" in html
    assert "address=10.50.0.1" in html          # mgmt server IP inside tunnel


def test_route_v6_pptp_onboarding(app):
    client = app.test_client()
    _auth(client)
    res = _onboard(client, "MT-PPTP", "pptp_mgmt")
    assert res.status_code == 302
    with app.app_context():
        from app.radius.db.connection import db
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE name='MT-PPTP'").fetchone())
        assert row["management_tunnel_type"] == "pptp_mgmt"
        assert row["management_tunnel_interface_name"] == "hr-pptp-mgmt"
    html = client.get(res.headers["Location"]).get_data(as_text=True)
    assert "pptp-client" in html and "187.77.70.18" in html


def test_route_v6_direct_coerced_to_tunnel_and_address_ignored(app):
    """The manual-address / direct path was removed. A stale v6_mode='direct'
    (or any unknown value) coerces to the SSTP tunnel, and any posted
    'address' is ignored — the address is always the auto-assigned tunnel IP."""
    client = app.test_client()
    _auth(client)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/setup", data={
        "name": "MT-Legacy", "ros_version": "6", "v6_mode": "direct",
        "address": "203.0.113.9",  # must be ignored
        "_csrf_token": token}, follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.connection import db
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE name='MT-Legacy'").fetchone())
        # Tunnel created; address is the auto-assigned pool IP, NOT 203.0.113.9
        assert row["management_tunnel_type"] == "sstp_mgmt"
        assert row["address"] != "203.0.113.9"
        assert row["address"].startswith("10.50.")
        assert row["connection_mode"] == "vpn"


# ════════════ 6) CoA targets the tunnel IP ════════════
def test_coa_resolves_to_tunnel_ip(app):
    client = app.test_client()
    _auth(client)
    _onboard(client, "MT-CoA", "sstp_mgmt")
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.nas_connection import resolve_connection_address
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE name='MT-CoA'").fetchone())
        ip = row["address"]
        # CoA dial target for this v6 router == its stable tunnel IP
        assert resolve_connection_address(row) == ip
        assert ip.startswith("10.50.")


def test_find_nas_for_session_returns_tunnel_ip(app):
    """A live session from the router (source IP = tunnel IP) resolves to the
    tunnel IP + the NAS secret, so CoA reaches the router over the tunnel."""
    from datetime import datetime
    client = app.test_client()
    _auth(client)
    _onboard(client, "MT-Sess", "sstp_mgmt")
    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.integration.radius_coa import find_nas_for_session
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE name='MT-Sess'").fetchone())
        tunnel_ip = row["address"]
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, "
                "username, nasipaddress, acctstarttime) VALUES (?,?,?,?,?,?)",
                (1, "s-1", "u-1", "subscriber-x", tunnel_ip, now))
        info = find_nas_for_session(1, "subscriber-x")
        assert info is not None
        assert info["nas_ip"] == tunnel_ip       # CoA target = tunnel IP
        assert info["nas_secret"]                # secret found for CoA auth


# ════════════ 7) v7 WireGuard path unchanged ════════════
def test_v7_wireguard_path_unchanged(app, monkeypatch):
    # Isolate the WG peers dir so provision_peer writes to a throwaway path
    # (the default is a real, non-tmp directory that would collide on re-run).
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR",
                       tempfile.mkdtemp(prefix="hr_v6tun_wg_"))
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "U" * 43 + "=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "187.77.70.18:51820")
    client = app.test_client()
    _auth(client)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/setup", data={
        "name": "MT-V7", "ros_version": "7", "server_ip": "187.77.70.18",
        "_csrf_token": token}, follow_redirects=False)
    assert res.status_code == 302
    with app.app_context():
        from app.radius.db.connection import db
        row = dict(db().execute(
            "SELECT * FROM nas_devices WHERE name='MT-V7'").fetchone())
        # v7 → WireGuard, NOT an sstp/pptp mgmt tunnel
        assert row["connection_mode"] == "vpn"
        assert row["management_tunnel_type"] in ("none", "", None)
        assert row["vpn_public_key"]            # WG key recorded
    html = client.get(res.headers["Location"]).get_data(as_text=True)
    assert "wireguard" in html.lower()
    assert "sstp-client" not in html            # no v6 block leaked into v7


# ════════════ 8) deprovision ════════════
def test_deprovision_removes_radius_user(app):
    with app.app_context():
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.repos import freeradius_repo as fr
        res = rmt.provision_tunnel("MT-Del", transport="sstp", tenant_id=1)
        assert fr.list_user_check(1, res.tunnel_username)
        assert rmt.deprovision_tunnel("MT-Del", tenant_id=1) is True
        assert fr.list_user_check(1, res.tunnel_username) == []
