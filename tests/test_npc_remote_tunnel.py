"""NPC remote-tunnel — VPS public-port allocator + nginx config."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_tunnel_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
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


def _seed_router(app, **kw):
    import secrets as _sec
    suffix = _sec.token_hex(3)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices (tenant_id, name, "
                "shortname, address, secret, vendor, nas_type, "
                "ports, snmp_community, auth_port, acct_port, "
                "coa_port, api_port, api_user, api_password, "
                "api_use_tls, location, coordinates, "
                "monitoring_enabled, description, enabled, "
                "require_message_authenticator, ssh_port, "
                "tags, metadata, created_at, updated_at) "
                "VALUES (1, ?, ?, ?,'','mikrotik',"
                "'router',0,'',1812,1813,3799,8728,'admin',"
                "'pw',0,'','',0,'',1,0,?,'','{}',"
                "'2026-01-01','2026-01-01')",
                (f"rt-{suffix}", f"rt-{suffix}",
                 kw.get("address",
                        f"10.0.{int(suffix, 16) % 256}.1"),
                 int(kw.get("ssh_port", 22))),
            )
            return int(cur.lastrowid)


# ─── Port allocator ────────────────────────────────────────


def test_allocate_returns_first_port_in_range(app):
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        p = r.allocate_next_port(
            port_base=51000, port_ceiling=51099,
        )
        assert p == 51000


def test_allocate_skips_used_ports(app):
    rid = _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        # Reserve 51000 + 51001 manually via ensure() calls.
        a = r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5", upstream_port=8291,
        )
        b = r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WEBFIG_HTTPS,
            upstream_address="10.10.0.5", upstream_port=443,
        )
        assert a["public_port"] == 51000
        assert b["public_port"] == 51001
        assert r.allocate_next_port() == 51002


def test_allocate_raises_when_range_exhausted(app):
    rid = _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        # Tiny range = easy exhaustion.
        r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5",
            upstream_port=8291,
            port_base=51000, port_ceiling=51000,
        )
        with pytest.raises(RuntimeError) as excinfo:
            r.allocate_next_port(
                port_base=51000, port_ceiling=51000,
            )
        assert "exhausted" in str(excinfo.value)


def test_ensure_returns_existing_mapping_idempotent(app):
    rid = _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        first = r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5", upstream_port=8291,
        )
        again = r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5", upstream_port=8291,
        )
        assert again["public_port"] == first["public_port"]
        assert again["id"] == first["id"]


def test_ensure_updates_upstream_when_changed(app):
    """If the router's WG IP changes, the port stays stable
    but the upstream address is refreshed."""
    rid = _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        first = r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5",
            upstream_port=8291,
        )
        second = r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.99",  # new IP
            upstream_port=8291,
        )
        assert second["public_port"] == first["public_port"]
        assert second["upstream_address"] == "10.10.0.99"


def test_disable_for_router_marks_all_mappings_disabled(app):
    rid = _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5",
            upstream_port=8291,
        )
        r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_SSH,
            upstream_address="10.10.0.5", upstream_port=22,
        )
        assert r.disable_for_router(rid) == 2
        # Now list_all_enabled should be empty.
        assert r.list_all_enabled() == []


def test_unknown_service_rejected(app):
    rid = _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        with pytest.raises(ValueError):
            r.ensure(
                tenant_id=1, router_id=rid,
                service="ftp",
                upstream_address="10.10.0.5",
                upstream_port=21,
            )


# ─── ensure_tunnels_for_policy ─────────────────────────────


def test_ensure_tunnels_allocates_per_enabled_service(app):
    rid = _seed_router(app, ssh_port=2222)
    with app.app_context():
        from app.radius.services import (
            npc_remote_tunnel as tun,
        )
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        policy = {
            "router_id":          rid,
            "allow_winbox":       True,
            "allow_webfig_https": True,
            "allow_ssh":          True,
            "allow_api":          False,
            "allow_api_ssl":      False,
            "allow_webfig_http":  False,
        }
        out = tun.ensure_tunnels_for_policy(
            tenant_id=1, policy=policy,
        )
        services = sorted(m["service"] for m in out)
        assert services == sorted([
            "winbox", "webfig_https", "ssh",
        ])
        # SSH should pick up the per-NAS port.
        ssh = next(m for m in out if m["service"] == "ssh")
        assert ssh["upstream_port"] == 2222
        # All mapping rows now reachable via the repo.
        assert len(r.list_for_router(rid)) == 3


def test_ensure_tunnels_returns_empty_for_unknown_router(app):
    with app.app_context():
        from app.radius.services import (
            npc_remote_tunnel as tun,
        )
        out = tun.ensure_tunnels_for_policy(
            tenant_id=1,
            policy={"router_id": 9999, "allow_winbox": True},
        )
        assert out == []


# ─── nginx stream config rendering ─────────────────────────


def test_render_stream_config_empty_when_no_mappings(app):
    with app.app_context():
        from app.radius.services import (
            npc_remote_tunnel as tun,
        )
        out = tun.render_stream_config()
        assert "no active mappings" in out


def test_render_stream_config_emits_server_block_per_mapping(
    app,
):
    rid = _seed_router(app)
    with app.app_context():
        from app.radius.services import (
            npc_remote_tunnel as tun,
        )
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5",
            upstream_port=8291,
        )
        r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_SSH,
            upstream_address="10.10.0.5",
            upstream_port=2222,
        )
        cfg = tun.render_stream_config()
        # Expect one server { listen; proxy_pass; } per mapping.
        assert cfg.count("server {") == 2
        assert "listen 51000;" in cfg
        assert "listen 51001;" in cfg
        assert "proxy_pass 10.10.0.5:8291;" in cfg
        assert "proxy_pass 10.10.0.5:2222;" in cfg


# ─── File-write path ──────────────────────────────────────


def test_write_stream_config_creates_file_atomically(
    app, monkeypatch, tmp_path,
):
    rid = _seed_router(app)
    monkeypatch.setenv(
        "HOBERADIUS_NGINX_STREAM_DIR", str(tmp_path),
    )
    with app.app_context():
        from app.radius.services import (
            npc_remote_tunnel as tun,
        )
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5",
            upstream_port=8291,
        )
        path = tun.write_stream_config()
    body = path.read_text(encoding="utf-8")
    assert "listen 51000;" in body
    # No leftover .tmp file.
    assert not any(p.suffix == ".tmp"
                   for p in tmp_path.iterdir())


def test_regenerate_and_reload_returns_status(
    app, monkeypatch, tmp_path,
):
    rid = _seed_router(app)
    monkeypatch.setenv(
        "HOBERADIUS_NGINX_STREAM_DIR", str(tmp_path),
    )
    with app.app_context():
        from app.radius.services import (
            npc_remote_tunnel as tun,
        )
        from app.radius.db.repos import (
            npc_remote_port_mappings_repo as r,
        )
        r.ensure(
            tenant_id=1, router_id=rid,
            service=r.SERVICE_WINBOX,
            upstream_address="10.10.0.5",
            upstream_port=8291,
        )
        out = tun.regenerate_and_reload()
    assert out["mapping_count"] == 1
    assert out["config_path"].endswith("npc_remote.conf")
    assert (tmp_path / "npc_remote.conf").exists()
    assert (tmp_path / ".reload").exists()


# ─── compute_remote_access_urls ────────────────────────────


def test_compute_remote_access_urls_emits_per_service():
    from app.radius.services.npc_remote_access_urls import (
        compute_remote_access_urls,
    )
    mappings = [
        {"service": "winbox", "public_port": 51001,
         "enabled": True},
        {"service": "webfig_https", "public_port": 51002,
         "enabled": True},
        {"service": "ssh", "public_port": 51003,
         "enabled": True},
    ]
    out = compute_remote_access_urls(
        {
            "allow_winbox": True,
            "allow_webfig_https": True,
            "allow_ssh": True,
        },
        public_host="187.77.70.18",
        mappings=mappings,
    )
    assert len(out) == 3
    by_service = {e["service"]: e for e in out}
    assert by_service["winbox"]["url"] == "187.77.70.18:51001"
    assert by_service["webfig_https"]["url"] == \
        "https://187.77.70.18:51002/"
    assert "ssh -p 51003 admin@187.77.70.18" in \
        by_service["ssh"]["clipboard"]


def test_compute_remote_access_urls_skips_disabled():
    from app.radius.services.npc_remote_access_urls import (
        compute_remote_access_urls,
    )
    mappings = [
        {"service": "winbox", "public_port": 51001,
         "enabled": False},
    ]
    out = compute_remote_access_urls(
        {"allow_winbox": True},
        public_host="187.77.70.18",
        mappings=mappings,
    )
    assert out == []


def test_compute_remote_access_urls_returns_empty_without_host():
    from app.radius.services.npc_remote_access_urls import (
        compute_remote_access_urls,
    )
    mappings = [
        {"service": "winbox", "public_port": 51001,
         "enabled": True},
    ]
    out = compute_remote_access_urls(
        {"allow_winbox": True},
        public_host="",
        mappings=mappings,
    )
    assert out == []
