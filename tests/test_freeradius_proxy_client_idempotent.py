"""Regression: the panel heartbeat calls ProxyTunnelManager every ~5 min. Its
FreeRADIUS proxy-client write must be IDEMPOTENT — an unchanged config must NOT
rewrite the file or touch .reload-trigger, otherwise the freeradius entrypoint
hard-restarts every cycle and drops auth for ~1-2s ("RADIUS server is not
responding" on the router).
"""
from __future__ import annotations

import time


def _mgr(tmp_path):
    from app.radius.services.proxy_tunnel_manager import ProxyTunnelManager
    return ProxyTunnelManager(clients_dir=tmp_path, state_dir=tmp_path)


def test_proxy_client_write_is_idempotent_no_needless_reload(tmp_path):
    mgr = _mgr(tmp_path)
    trigger = tmp_path / ".reload-trigger"

    # 1) first write — creates the client file + touches the reload trigger
    ok, _ = mgr._write_freeradius_client(proxy_tunnel_ip="10.10.0.2", secret="s3cr3tABC")
    assert ok
    assert mgr.clients_path.exists()
    assert trigger.exists()
    m1 = trigger.stat().st_mtime_ns
    body1 = mgr.clients_path.read_text()

    # 2) SAME config again (a no-op heartbeat) — must NOT rewrite or reload
    time.sleep(0.02)
    ok2, _ = mgr._write_freeradius_client(proxy_tunnel_ip="10.10.0.2", secret="s3cr3tABC")
    assert ok2
    assert trigger.stat().st_mtime_ns == m1          # trigger untouched → no restart
    assert mgr.clients_path.read_text() == body1     # file byte-identical

    # 3) real change (secret rotated) — SHOULD rewrite + touch the trigger
    time.sleep(0.02)
    ok3, _ = mgr._write_freeradius_client(proxy_tunnel_ip="10.10.0.2", secret="rotatedXYZ")
    assert ok3
    assert trigger.stat().st_mtime_ns > m1           # trigger touched on real change
    assert "rotatedXYZ" in mgr.clients_path.read_text()


def test_proxy_client_block_has_no_volatile_timestamp(tmp_path):
    """The block must not embed a changing timestamp (that would defeat the
    idempotency guard and restart FreeRADIUS every heartbeat)."""
    mgr = _mgr(tmp_path)
    mgr._write_freeradius_client(proxy_tunnel_ip="10.10.0.2", secret="s3cr3tABC")
    body = mgr.clients_path.read_text()
    assert "Generated at" not in body
