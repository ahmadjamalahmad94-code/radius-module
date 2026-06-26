# -*- coding: utf-8 -*-
"""Bug fixes + redesign for the diagnostics & topology pages.

Covers:
  * topology status now reflects REAL connectivity (radacct live session OR
    cached tunnel-reachability probe) — a reachable router never shows the
    stale «غير معروف» default.
  * diagnostics page loads its SHELL without probing any router (fast/reliable
    even when a router is offline); each card loads lazily via the per-router
    endpoint.

The subnet-overlap logic fix is covered in test_mt_dashboard_p7_diagnostics.py.
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
    tmp = tempfile.mkdtemp(prefix="hr_diagtopo_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
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


def _seed_nas(app, *, nas_id, name, address, mode="vpn",
              vpn_peer="10.10.0.7", last_check="", enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor, nas_type,
                     enabled, created_at, connection_mode, api_user,
                     api_password, vpn_peer_address, last_check_status)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, ?, 'hr', 'pw', ?, ?)""",
                (nas_id, name, address, 1 if enabled else 0, now, mode,
                 vpn_peer, last_check),
            )


def _seed_open_session(app, *, nasip):
    """One open radacct session on `nasip` within the live window."""
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow()
        start = (now - timedelta(minutes=2)).isoformat() + "Z"
        upd = (now - timedelta(seconds=30)).isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO radacct
                    (tenant_id, username, nasipaddress, acctstarttime,
                     acctupdatetime, acctstoptime)
                   VALUES (1, 'live-user', ?, ?, ?, NULL)""",
                (nasip, start, upd),
            )


# ─── TOPOLOGY STATUS BUG ─────────────────────────────────────────────

def test_topology_router_with_live_session_is_online_not_unknown(app):
    """ccr5 repro: tunnel up + live radacct → must be 'online', never
    the stale default 'unknown'."""
    _seed_nas(app, nas_id=55, name="ccr5", address="203.0.113.55",
              mode="vpn", vpn_peer="10.10.0.55")
    _seed_open_session(app, nasip="10.10.0.55")   # session arrives on the tunnel IP
    with app.app_context():
        from app.radius.services.mt_topology import (
            build_topology, overlay_connectivity)
        from app.radius.services import live_sessions
        topo = build_topology(1)
        overlay_connectivity(topo, live_sessions.live_map(1))
    node = {r.id: r for r in topo.routers}["55"]
    assert node.status == "online"
    assert node.meta.get("conn_source") == "radacct"


def test_topology_router_reachable_probe_is_online(app):
    """No live session, but the cached tunnel probe was 'reachable' →
    online (fallback signal), not 'unknown'."""
    _seed_nas(app, nas_id=56, name="ccr6", address="203.0.113.56",
              last_check="reachable")
    with app.app_context():
        from app.radius.services.mt_topology import (
            build_topology, overlay_connectivity)
        topo = build_topology(1)
        overlay_connectivity(topo, {})      # empty live map
    node = {r.id: r for r in topo.routers}["56"]
    assert node.status == "online"
    assert node.meta.get("conn_source") == "tunnel_probe"


def test_topology_router_timeout_probe_is_offline(app):
    _seed_nas(app, nas_id=57, name="ccr7", address="203.0.113.57",
              last_check="timeout")
    with app.app_context():
        from app.radius.services.mt_topology import (
            build_topology, overlay_connectivity)
        topo = build_topology(1)
        overlay_connectivity(topo, {})
    node = {r.id: r for r in topo.routers}["57"]
    assert node.status == "offline"


def test_topology_no_signal_stays_unknown_disabled_preserved(app):
    _seed_nas(app, nas_id=58, name="quiet", address="203.0.113.58",
              last_check="")
    _seed_nas(app, nas_id=59, name="off", address="203.0.113.59",
              last_check="reachable", enabled=False)
    with app.app_context():
        from app.radius.services.mt_topology import (
            build_topology, overlay_connectivity)
        topo = build_topology(1)
        overlay_connectivity(topo, {})
    by_id = {r.id: r for r in topo.routers}
    assert by_id["58"].status == "unknown"        # honest: no signal yet
    assert by_id["59"].status == "disabled"       # disabled never overridden


def test_topology_route_shows_connected_and_legend(app, client):
    _seed_nas(app, nas_id=60, name="ccr-live", address="203.0.113.60",
              mode="vpn", vpn_peer="10.10.0.60")
    _seed_open_session(app, nasip="10.10.0.60")
    with app.app_context():
        _login(client)
        html = client.get("/admin/radius/topology").get_data(as_text=True)
    assert 'data-mt-topology-status="online"' in html
    # no router NODE is left as the stale 'unknown' default
    assert 'data-mt-topology-status="unknown"' not in html
    assert "معاني الشارات" in html               # legend present


# ─── DIAGNOSTICS PERFORMANCE (lazy shell) ────────────────────────────

def test_diagnostics_shell_does_not_probe(app, client, monkeypatch):
    """The page shell must render WITHOUT calling any router probe — that's
    what makes it fast/reliable even when a router is offline."""
    import app.radius.services.mt_diagnostics as md
    calls = {"tcp": 0, "api": 0}
    monkeypatch.setattr(md, "_tcp_probe",
                        lambda *a, **k: calls.__setitem__("tcp", calls["tcp"] + 1) or {"ok": True})
    monkeypatch.setattr(md, "_api_probe",
                        lambda *a, **k: calls.__setitem__("api", calls["api"] + 1) or {"ok": True})
    _seed_nas(app, nas_id=70, name="r70", address="203.0.113.70")
    with app.app_context():
        _login(client)
        res = client.get("/admin/radius/diagnostics")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    # shell rendered the card placeholder + its lazy URL, but NEVER probed
    assert "data-diag-card" in html
    assert "/admin/radius/diagnostics/router/70" in html
    assert calls["tcp"] == 0 and calls["api"] == 0


def test_diagnostics_router_endpoint_probes_one(app, client, monkeypatch):
    import app.radius.services.mt_diagnostics as md
    monkeypatch.setattr(md, "_tcp_probe",
                        lambda host, port, timeout=2.5: {"ok": False,
                            "latency_ms": None, "error": "timed_out", "hint": "x"})
    _seed_nas(app, nas_id=71, name="r71", address="10.10.0.71", mode="vpn")
    with app.app_context():
        _login(client)
        res = client.get("/admin/radius/diagnostics/router/71")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
    assert 'data-mt-repair-mode="vpn"' in html      # full card incl. repair script
    # unknown id → 404
    with app.app_context():
        assert client.get("/admin/radius/diagnostics/router/9999").status_code == 404
