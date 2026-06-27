# -*- coding: utf-8 -*-
"""WinBox remote access over a WireGuard management tunnel (v7).

Regression guard for the owner-reported bug: «لما ركبت وير جريد وأنشأت اتصال،
وين بوكس ما اشتغل» — the «تفعيل WinBox» action failed for a WireGuard-managed
router because the forward target + router-side ACL were SSTP-only.

Locks in the three fixes the panel controls:
  1. router_tunnel_ip resolves the router's WG tunnel IP for a WG-managed
     router (and never a stale SSTP column), with a WG-specific error hint.
  2. open_session targets that WG tunnel IP (proxy_pass to the WG /32).
  3. render_wg_block opens WinBox/API/web to the WG gateway ONLY and permits
     the WG interface in the input firewall — so the forward connects, still
     restricted to the tunnel (never the WAN).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


# ════════════ pure-function: render_wg_block opens WinBox to the WG gateway ═══

def test_wg_block_locks_winbox_to_combined_mgmt_acl():
    """The router-side WG block binds WinBox/API/web to a COMBINED allow-list:
    the WG tunnel subnet AND the SSTP/RADIUS gateway /32 — so re-pasting this
    block never clobbers management over the SSTP tunnel (`/ip service set
    address=` REPLACES, not appends). WG subnet leads (this script's own path).
    Strictly tunnel-only — never the WAN. The firewall rule stays WG-subnet-only
    (it is bound to the WG interface)."""
    from app.radius.services import mt_provisioner as prov
    block = prov.render_wg_block(
        nas_name="ccr7", router_private_key="k", server_pubkey="p",
        server_endpoint="198.51.100.1:13231", allowed_subnet="10.10.0.0/24",
        router_tunnel_ip="10.10.0.5/24", ros_version="7",
        mgmt_server_ip="10.10.0.1", wg_iface="hr-wg",
        sstp_gateway_ip="10.50.0.1",
    )
    # WinBox/API/web carry BOTH gateways (WG subnet + SSTP gateway /32).
    assert "/ip service set winbox address=10.10.0.0/24,10.50.0.1/32" in block
    assert "/ip service set api address=10.10.0.0/24,10.50.0.1/32" in block
    assert "/ip service set www address=10.10.0.0/24,10.50.0.1/32" in block
    assert "0.0.0.0/0" not in block               # never the WAN
    # Input firewall accepts the WG mgmt path; bound to the iface → WG subnet only.
    assert "chain=input" in block and "in-interface=hr-wg" in block
    assert "src-address=10.10.0.0/24" in block
    assert "src-address=10.10.0.0/24,10.50.0.1/32" not in block
    assert 'comment="hr-wg-mgmt"' in block
    assert "destination=0" in block


def test_wg_block_is_idempotent_wipes_before_add():
    """Re-pasting must converge to ONE clean state: the block removes ALL peers
    and addresses on the interface (clearing the setup wizard's peer + any prior
    paste) BEFORE re-adding, and only creates the interface if missing (so the
    router's private key is preserved across re-pastes). This is the fix for the
    duplicate-peer crypto-routing break that closed WinBox."""
    from app.radius.services import mt_provisioner as prov
    block = prov.render_wg_block(
        nas_name="ccr7", router_private_key="k", server_pubkey="p",
        server_endpoint="198.51.100.1:13231", allowed_subnet="10.10.0.0/24",
        router_tunnel_ip="10.10.0.5/24", ros_version="7", wg_iface="hr-wg",
    )
    # remove-before-add for EVERY object the block owns
    assert '/ip firewall filter remove [find comment="hr-wg-mgmt"]' in block
    assert '/interface/wireguard/peers remove [find interface="hr-wg"]' in block
    assert '/ip address remove [find interface="hr-wg"]' in block
    # interface created only if missing (preserves the on-router private key)
    assert ':if ([:len [/interface wireguard find name="hr-wg"]]=0) do={' in block
    # …and exactly one add of each after the wipe
    assert block.count("/interface/wireguard/peers add") == 1
    assert block.count("/ip/address add interface=hr-wg") == 1


def test_wg_block_gateway_defaults_to_subnet_first_host():
    """When mgmt_server_ip / sstp_gateway_ip are omitted they still resolve (WG
    gateway → subnet's first host; SSTP gateway → env/default) and the ACL is
    the combined list — never blank, never WAN-open."""
    from app.radius.services import mt_provisioner as prov
    block = prov.render_wg_block(
        nas_name="r", router_private_key="k", server_pubkey="p",
        server_endpoint="1.2.3.4:13231", allowed_subnet="10.20.0.0/24",
        router_tunnel_ip="10.20.0.9/24", ros_version="7",
    )
    # WG subnet present + the default SSTP gateway (10.50.0.1) appended.
    assert "/ip service set winbox address=10.20.0.0/24,10.50.0.1/32" in block


def test_wg_block_rejects_bad_gateway_ip():
    """A non-IP mgmt_server_ip raises (injection-safe — only a real IP can ever
    reach the service/firewall restriction line)."""
    from app.radius.services import mt_provisioner as prov
    with pytest.raises(ValueError):
        prov.render_wg_block(
            nas_name="r", router_private_key="k", server_pubkey="p",
            server_endpoint="1.2.3.4:13231", allowed_subnet="10.10.0.0/24",
            router_tunnel_ip="10.10.0.5/24", ros_version="7",
            mgmt_server_ip="winbox; /system reset",
        )


# ════════════ DB-backed: tunnel-IP resolution + forward target ════════════

@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_wgwb_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_NGINX_STREAM_DIR", tempfile.mkdtemp())
    monkeypatch.setenv("HOBERADIUS_PUBLIC_IP", "203.0.113.1")
    monkeypatch.setenv("HOBERADIUS_REMOTE_ACCESS_ENABLED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            # A WireGuard-managed router (v7): connection_mode='vpn', a WG /32 in
            # vpn_peer_address, NO management_tunnel_type, NO SSTP framed-IP.
            c.execute(
                "INSERT INTO nas_devices (tenant_id,name,address,secret,vendor,"
                " nas_type,enabled,connection_mode,vpn_peer_address,"
                " vpn_public_key,vpn_interface,created_at) VALUES "
                "(1,'ccr7-wg','10.10.0.5','s','mikrotik','hotspot',1,'vpn',"
                " '10.10.0.5','PUBKEY7','hr-wg','2026-01-01T00:00:00Z')")
            # A WG router that ALSO carries a stale SSTP framed-IP — the WG path
            # must IGNORE management_remote_address and use the WG /32.
            c.execute(
                "INSERT INTO nas_devices (tenant_id,name,address,secret,vendor,"
                " nas_type,enabled,connection_mode,vpn_peer_address,"
                " management_remote_address,vpn_public_key,created_at) VALUES "
                "(1,'ccr8-stale','10.10.0.8','s','mikrotik','hotspot',1,'vpn',"
                " '10.10.0.8','10.50.0.99','PUBKEY8','2026-01-01T00:00:00Z')")
            # An SSTP router (v6) — the existing path must still resolve the
            # SSTP framed-IP first.
            c.execute(
                "INSERT INTO nas_devices (tenant_id,name,address,secret,vendor,"
                " nas_type,enabled,management_tunnel_type,management_remote_address,"
                " vpn_peer_address,created_at) VALUES "
                "(1,'ccr4-sstp','10.50.0.4','s','mikrotik','hotspot',1,'sstp_mgmt',"
                " '10.50.0.2','10.50.0.2','2026-01-01T00:00:00Z')")
        rows = c.execute(
            "SELECT id,name FROM nas_devices WHERE tenant_id=1").fetchall()
        application._ids = {r["name"]: int(r["id"]) for r in rows}
    yield application
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_tunnel_ip_resolves_wg_address(app):
    """A WG-managed router resolves to its WireGuard /32 (vpn_peer_address)."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        assert ra.router_tunnel_ip(1, app._ids["ccr7-wg"]) == "10.10.0.5"


def test_tunnel_ip_wg_ignores_stale_sstp_column(app):
    """A WG router with a stale management_remote_address must NOT return it —
    the WG path resolves vpn_peer_address, never the SSTP column."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        ip = ra.router_tunnel_ip(1, app._ids["ccr8-stale"])
        assert ip == "10.10.0.8"          # the WG /32 …
        assert ip != "10.50.0.99"         # … NOT the stale SSTP framed-IP


def test_tunnel_ip_sstp_still_prefers_framed_ip(app):
    """Regression: an SSTP router still resolves management_remote_address."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        assert ra.router_tunnel_ip(1, app._ids["ccr4-sstp"]) == "10.50.0.2"


def test_open_winbox_targets_wg_tunnel_ip(app):
    """open_session for a WG router builds a forward whose proxy_pass targets
    the router's WG tunnel IP (not an SSTP address)."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        res = ra.open_session(tenant_id=1, router_id=app._ids["ccr7-wg"],
                              source_ip="198.51.100.7", opened_by="admin")
        assert res["tunnel_ip"] == "10.10.0.5"
        assert 51000 <= res["port"] <= 51199
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "proxy_pass 10.10.0.5:8291;" in cfg     # forward → WG tunnel IP


def test_wg_router_without_tunnel_ip_gives_wg_hint(app):
    """A WG-managed router with no tunnel IP yet errors with a WireGuard-specific
    actionable hint (not «أعِد إعداد نفق SSTP»)."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices (tenant_id,name,address,secret,vendor,"
                " nas_type,enabled,connection_mode,vpn_public_key,created_at) "
                "VALUES (1,'wg-noip','','s','mikrotik','hotspot',1,'vpn',"
                " 'PUBKEYX','2026-01-01T00:00:00Z')")
        rid = c.execute(
            "SELECT id FROM nas_devices WHERE name='wg-noip'").fetchone()["id"]
        with pytest.raises(ra.RemoteAccessError) as exc:
            ra.router_tunnel_ip(1, int(rid))
        assert "WireGuard" in str(exc.value)
        assert "SSTP" not in str(exc.value)


# ════════ «تفعيل WinBox» actionable hint: tunnel-down vs ACL/re-paste ════════

def test_winbox_hint_distinguishes_tunnel_down_from_acl():
    """The «تفعيل WinBox» flow surfaces a DISTINCT, actionable hint per failure
    mode for a WG router (so a closed WinBox isn't a silent dead-end):
      • last check failed  → 'tunnel down' (fix handshake first)
      • last check OK/blank → 're-paste the (idempotent) WG block once'
    SSTP routers get no WG hint (their path is unchanged)."""
    from app.radius.routes.mt_setup import _wg_winbox_hint
    # WG router, tunnel down → handshake-first message
    down = _wg_winbox_hint({"connection_mode": "vpn", "vpn_public_key": "k",
                            "last_check_status": "timeout"})
    assert "غير متّصل" in down and "مصافحة" in down
    # WG router, tunnel reachable → re-paste-the-block message
    up = _wg_winbox_hint({"connection_mode": "vpn", "vpn_public_key": "k",
                          "last_check_status": "reachable"})
    assert "idempotent" in up and "أعد لصق" in up
    # SSTP router → no WG hint at all
    sstp = _wg_winbox_hint({"management_tunnel_type": "sstp_mgmt",
                            "connection_mode": "vpn"})
    assert sstp == ""
