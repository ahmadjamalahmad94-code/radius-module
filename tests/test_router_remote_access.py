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


# ════════════ config generation: restriction optional ════════════
def test_render_block_is_source_ip_locked():
    """A RESTRICTED session (source = an IP) still emits allow/deny."""
    from app.radius.services import router_remote_access as ra
    cfg = ra.render_stream_config([{
        "id": 5, "router_id": 3, "opened_by": "ahmad", "expires_at": "x",
        "public_port": 51005, "tunnel_ip": "10.50.0.7", "dst_port": 8291,
        "source_ip": "203.0.113.9",
    }])
    assert "listen 51005;" in cfg
    assert "allow 203.0.113.9;" in cfg and "deny all;" in cfg   # source lock
    assert "proxy_pass 10.50.0.7:8291;" in cfg                  # tunnel target


def test_render_unrestricted_emits_no_deny():
    """An UNRESTRICTED session (source = «any») is open to all — NO allow/deny."""
    from app.radius.services import router_remote_access as ra
    cfg = ra.render_stream_config([{
        "id": 6, "router_id": 3, "opened_by": "ahmad", "expires_at": "x",
        "public_port": 51006, "tunnel_ip": "10.50.0.7", "dst_port": 8291,
        "source_ip": ra.ANY_SOURCE,
    }])
    assert "listen 51006;" in cfg                       # forward IS emitted …
    assert "proxy_pass 10.50.0.7:8291;" in cfg          # … to the tunnel target
    assert "deny all;" not in cfg                       # … open to any source
    assert "allow " not in cfg


def test_render_skips_rows_with_bad_ip():
    from app.radius.services import router_remote_access as ra
    cfg = ra.render_stream_config([{
        "id": 1, "router_id": 1, "opened_by": "x", "expires_at": "x",
        "public_port": 51000, "tunnel_ip": "not-an-ip", "dst_port": 8291,
        "source_ip": "203.0.113.9",
    }])
    assert "listen 51000;" not in cfg     # invalid target → no forward emitted
    assert "deny all;" not in cfg


def test_render_skips_empty_source_failsafe():
    """An EMPTY source is NOT silently promoted to «any» — only the explicit
    sentinel opens a forward. A corrupt/empty row is skipped (fail-safe)."""
    from app.radius.services import router_remote_access as ra
    cfg = ra.render_stream_config([{
        "id": 7, "router_id": 1, "opened_by": "x", "expires_at": "x",
        "public_port": 51007, "tunnel_ip": "10.50.0.7", "dst_port": 8291,
        "source_ip": "",
    }])
    assert "listen 51007;" not in cfg     # empty source ⇒ skipped, never opened


# ════════════ lifecycle: open → config → close ════════════
def test_open_session_default_is_unrestricted(app):
    """Default open (no allowed_source) is UNRESTRICTED — open to any source so
    the operator can reach WinBox from anywhere. NO deny all is emitted."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="198.51.100.7", opened_by="admin")
        assert 51000 <= res["port"] <= 51199
        assert res["endpoint"] == f"203.0.113.1:{res['port']}"   # public host:port
        assert res["unrestricted"] is True
        s = sr.active_for_router(1, app._ccr4)
        assert s["source_ip"] == ra.ANY_SOURCE        # stored sentinel
        assert ra.is_unrestricted(s) is True
        assert ra.source_label(s) == "من أي مكان"
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "deny all;" not in cfg                 # open to any source
        assert "allow " not in cfg
        assert "proxy_pass 10.50.0.2:8291;" in cfg


def test_open_session_restricted_emits_allow_deny(app):
    """Opting into a restriction (allowed_source) still locks the forward."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="198.51.100.7", opened_by="admin",
                              allowed_source="198.51.100.7")
        assert res["unrestricted"] is False
        assert res["source_ip"] == "198.51.100.7"
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "allow 198.51.100.7;" in cfg and "deny all;" in cfg
        assert "proxy_pass 10.50.0.2:8291;" in cfg


def test_close_removes_forward(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="198.51.100.7", opened_by="admin",
                              allowed_source="198.51.100.7")
        port = res["port"]
        assert ra.close_session(tenant_id=1, session_id=res["session_id"],
                                closed_by="admin")
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert f"listen {port};" not in cfg
        assert "allow 198.51.100.7;" not in cfg


def test_reopen_replaces_not_duplicates(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        ra.open_session(tenant_id=1, router_id=app._ccr4,
                        source_ip="198.51.100.7", opened_by="admin",
                        allowed_source="198.51.100.7")
        ra.open_session(tenant_id=1, router_id=app._ccr4,
                        source_ip="198.51.100.8", opened_by="admin",
                        allowed_source="198.51.100.8")
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
def test_bad_detected_ip_does_not_block_open(app):
    """The opener's detected IP (``source_ip``) is audit-only now — a malformed
    one (e.g. behind a weird proxy) must NOT block an «any» open."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="not-an-ip", opened_by="admin")
        assert res["unrestricted"] is True
        s = sr.active_for_router(1, app._ccr4)
        assert s["source_ip"] == ra.ANY_SOURCE      # forward source, not the audit IP


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


# ════════════ always-on / persistent (secure form) ════════════
def test_persistent_open_has_no_expiry_and_survives_sweep(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="198.51.100.7", opened_by="admin",
                              persistent=True)
        assert res["always_on"] is True
        s = sr.active_for_router(1, app._ccr4)
        assert int(s["always_on"]) == 1
        assert str(s["expires_at"] or "") == ""          # no expiry sentinel
        assert ra.seconds_remaining(s) == -1             # UI shows «دائم»
        assert ra.is_persistent(s) is True
        # the reaper must NOT touch a persistent session
        assert sr.list_expired() == []
        assert ra.sweep_expired() == 0
        assert sr.active_for_router(1, app._ccr4) is not None
        # persistent defaults to unrestricted («any») too — open to anywhere
        assert res["unrestricted"] is True
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "deny all;" not in cfg and "allow " not in cfg


def test_persistent_can_still_be_restricted(app):
    """A persistent forward can opt into an IP/CIDR restriction (allow/deny)."""
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        ra.open_session(tenant_id=1, router_id=app._ccr4,
                        source_ip="198.51.100.7", opened_by="admin",
                        persistent=True, allowed_source="198.51.100.7")
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "allow 198.51.100.7;" in cfg and "deny all;" in cfg


def test_persistent_with_cidr_allow_list(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        ra.open_session(tenant_id=1, router_id=app._ccr4,
                        source_ip="198.51.100.7", opened_by="admin",
                        persistent=True, allowed_source="203.0.113.0/24")
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "allow 203.0.113.0/24;" in cfg and "deny all;" in cfg
        assert "allow 198.51.100.7;" not in cfg          # allow-list overrides admin IP


def test_invalid_allowed_source_rejected(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        with pytest.raises(ra.RemoteAccessError):
            ra.open_session(tenant_id=1, router_id=app._ccr4,
                            source_ip="198.51.100.7", opened_by="admin",
                            persistent=True, allowed_source="not-a-cidr")


def test_global_always_on_setting_makes_open_persistent(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_REMOTE_ACCESS_ALWAYS_ON", "1")
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        ra.open_session(tenant_id=1, router_id=app._ccr4,
                        source_ip="198.51.100.7", opened_by="admin")  # no explicit flag
        s = sr.active_for_router(1, app._ccr4)
        assert int(s["always_on"]) == 1 and str(s["expires_at"] or "") == ""


def test_time_boxed_still_default_and_swept(app):
    with app.app_context():
        from app.radius.services import router_remote_access as ra
        from app.radius.db.repos import router_remote_sessions_repo as sr
        res = ra.open_session(tenant_id=1, router_id=app._ccr4,
                              source_ip="198.51.100.7", opened_by="admin")
        assert res["always_on"] is False
        s = sr.active_for_router(1, app._ccr4)
        assert int(s["always_on"]) == 0 and str(s["expires_at"] or "") != ""
        assert ra.seconds_remaining(s) > 0
