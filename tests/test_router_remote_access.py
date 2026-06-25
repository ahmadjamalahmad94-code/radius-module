# -*- coding: utf-8 -*-
"""Secure remote access (Open WinBox) — port allocation, source-IP-locked nginx
config, time-boxed lifecycle, sweep. The security-critical logic.

The container→host-tunnel forwarding itself can only be verified on a real host
(see deploy notes); here we lock in everything the panel controls: the exact
generated nginx block, the IP/port allocation, the lifecycle, and validation.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ra_")
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
            c.execute(
                "INSERT INTO nas_devices (tenant_id,name,address,secret,vendor,"
                " nas_type,enabled,management_tunnel_type,management_remote_address,"
                " created_at) VALUES (1,'ccr4','10.50.0.4','s','mikrotik','hotspot',"
                " 1,'sstp_mgmt','10.50.0.2','2026-01-01T00:00:00Z')")
        c2 = c.execute("SELECT id FROM nas_devices WHERE name='ccr4'").fetchone()
        application._ccr4 = int(c2["id"])
    yield application
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


# ════════════ port allocation (avoid NPC + active) ════════════
def test_allocate_avoids_npc_and_active_ports(app):
    with app.app_context():
        from app.radius.db.repos import router_remote_sessions_repo as sr
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        # NPC reserves 51000; an active session holds 51001
        with transaction() as c:
            c.execute("INSERT INTO npc_remote_port_mappings (tenant_id,router_id,"
                      "service,public_port,upstream_address,upstream_port,enabled,"
                      "created_at,updated_at) VALUES (1,9,'winbox',51000,'x',8291,1,?,?)",
                      (now_iso(), now_iso()))
        sr.create_session(tenant_id=1, router_id=9, service="winbox",
                          public_port=51001, tunnel_ip="10.50.0.9", dst_port=8291,
                          source_ip="1.2.3.4", opened_by="a",
                          expires_at="2999-01-01T00:00:00Z")
        assert sr.allocate_port() == 51002   # 51000 (npc) + 51001 (active) skipped


# ════════════ source-IP-locked config generation ════════════
def test_render_block_is_source_ip_locked():
    from app.radius.services import router_remote_access as ra
    cfg = ra.render_stream_config([{
        "id": 5, "router_id": 3, "opened_by": "ahmad", "expires_at": "x",
        "public_port": 51005, "tunnel_ip": "10.50.0.7", "dst_port": 8291,
        "source_ip": "203.0.113.9",
    }])
    assert "listen 51005;" in cfg
    assert "allow 203.0.113.9;" in cfg and "deny all;" in cfg   # source lock
    assert "proxy_pass 10.50.0.7:8291;" in cfg                  # tunnel target


def test_render_skips_rows_with_bad_ip():
    from app.radius.services import router_remote_access as ra
    cfg = ra.render_stream_config([{
        "id": 1, "router_id": 1, "opened_by": "x", "expires_at": "x",
        "public_port": 51000, "tunnel_ip": "not-an-ip", "dst_port": 8291,
        "source_ip": "203.0.113.9",
    }])
    assert "listen 51000;" not in cfg     # invalid target → no forward emitted
    assert "deny all;" not in cfg


# ════════════ lifecycle: open → config → close ════════════
def test_open_session_writes_locked_forward(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="198.51.100.7", opened_by="admin")
        assert 51000 <= res["port"] <= 51199
        assert res["endpoint"] == f"203.0.113.1:{res['port']}"   # public host:port
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "allow 198.51.100.7;" in cfg and "deny all;" in cfg
        assert "proxy_pass 10.50.0.2:8291;" in cfg


def test_close_removes_forward(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="198.51.100.7", opened_by="admin")
        assert ra.close_session(tenant_id=1, session_id=res["session_id"],
                                closed_by="admin")
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "allow 198.51.100.7;" not in cfg


def test_reopen_replaces_not_duplicates(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        ra.open_session(tenant_id=1, router_id=app._ccr4,
                        source_ip="198.51.100.7", opened_by="admin")
        ra.open_session(tenant_id=1, router_id=app._ccr4,
                        source_ip="198.51.100.8", opened_by="admin")
        active = sr.list_active(1)
        for_router = [s for s in active if s["router_id"] == app._ccr4]
        assert len(for_router) == 1                 # reopen replaced
        assert for_router[0]["source_ip"] == "198.51.100.8"


# ════════════ time-box: sweep auto-closes ════════════
def test_sweep_closes_expired(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        sid = sr.create_session(
            tenant_id=1, router_id=app._ccr4, service="winbox", public_port=51010,
            tunnel_ip="10.50.0.2", dst_port=8291, source_ip="1.2.3.4",
            opened_by="admin", expires_at="2000-01-01T00:00:00Z")  # past
        ra.regenerate_and_reload()
        assert "allow 1.2.3.4;" in open(ra._stream_dir() / ra.STREAM_FILE).read()
        assert ra.sweep_expired() == 1
        assert sr.get(sid)["status"] == "expired"
        assert "allow 1.2.3.4;" not in open(ra._stream_dir() / ra.STREAM_FILE).read()


# ════════════ validation / settings ════════════
def test_bad_source_ip_rejected(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        with pytest.raises(ra.RemoteAccessError):
            ra.open_session(tenant_id=1, router_id=app._ccr4,
                            source_ip="not-an-ip", opened_by="admin")


def test_disabled_setting_blocks_open(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_REMOTE_ACCESS_ENABLED", "0")
        from app.radius.services import router_remote_access as ra
        with pytest.raises(ra.RemoteAccessError):
            ra.open_session(tenant_id=1, router_id=app._ccr4,
                            source_ip="1.2.3.4", opened_by="admin")


def test_router_without_tunnel_ip_rejected(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO nas_devices (tenant_id,name,address,secret,"
                      "vendor,nas_type,enabled,management_tunnel_type,created_at) "
                      "VALUES (1,'noip','','s','mikrotik','hotspot',1,'sstp_mgmt',"
                      "'2026-01-01T00:00:00Z')")
        rid = c.execute("SELECT id FROM nas_devices WHERE name='noip'").fetchone()["id"]
        with pytest.raises(ra.RemoteAccessError):
            ra.open_session(tenant_id=1, router_id=int(rid),
                            source_ip="1.2.3.4", opened_by="admin")
