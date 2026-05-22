"""O10 — Topology as an operations view (health overlay)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o10_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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
    u = f"o10_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o10-pass", full_name="O10",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o10-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id, enabled=True, mode="direct"):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, ?)""",
                (nas_id, f"o10-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", 1 if enabled else 0, now, mode),
            )


# ─── overlay_health (pure) ──────────────────────────────────


def test_overlay_health_with_none_is_noop():
    from app.radius.services.mt_topology import (
        Topology, TopologyNode, overlay_health,
    )
    topo = Topology(
        server=TopologyNode(kind="server", id="server",
                            label="VPS", status="online"),
        routers=[TopologyNode(kind="router", id="1",
                              label="r1", status="unknown")],
        links=[],
    )
    out = overlay_health(topo, None)
    assert out is topo
    assert out.routers[0].health_state == ""


def test_overlay_health_decorates_known_routers():
    from app.radius.services.mt_topology import (
        Topology, TopologyNode, overlay_health,
    )
    topo = Topology(
        server=TopologyNode(kind="server", id="server",
                            label="VPS", status="online"),
        routers=[
            TopologyNode(kind="router", id="1", label="a",
                         status="unknown"),
            TopologyNode(kind="router", id="2", label="b",
                         status="unknown"),
        ],
        links=[],
    )
    overlay_health(topo, {
        "1": {"state": "healthy", "score": 95, "signal": "ok"},
        "2": {"state": "risky", "score": 30, "signal": "stale_snapshot"},
    })
    by_id = {r.id: r for r in topo.routers}
    assert by_id["1"].health_state == "healthy"
    assert by_id["1"].health_score == 95
    assert by_id["2"].health_state == "risky"
    assert by_id["2"].health_signal == "stale_snapshot"


def test_overlay_health_unknown_id_skipped_silently():
    from app.radius.services.mt_topology import (
        Topology, TopologyNode, overlay_health,
    )
    topo = Topology(
        server=TopologyNode(kind="server", id="server",
                            label="VPS", status="online"),
        routers=[TopologyNode(kind="router", id="1",
                              label="a", status="unknown")],
        links=[],
    )
    overlay_health(topo, {"999": {"state": "risky", "score": 10}})
    assert topo.routers[0].health_state == ""


def test_overlay_health_bad_score_falls_back_to_zero():
    from app.radius.services.mt_topology import (
        Topology, TopologyNode, overlay_health,
    )
    topo = Topology(
        server=TopologyNode(kind="server", id="server",
                            label="VPS", status="online"),
        routers=[TopologyNode(kind="router", id="1",
                              label="a", status="unknown")],
        links=[],
    )
    overlay_health(topo, {"1": {"state": "risky",
                                "score": "not-a-number"}})
    assert topo.routers[0].health_score == 0
    assert topo.routers[0].health_state == "risky"


def test_to_dict_exposes_new_health_keys():
    from app.radius.services.mt_topology import (
        Topology, TopologyNode, overlay_health,
    )
    topo = Topology(
        server=TopologyNode(kind="server", id="server",
                            label="VPS", status="online"),
        routers=[TopologyNode(kind="router", id="1",
                              label="a", status="unknown")],
        links=[],
    )
    overlay_health(topo, {"1": {"state": "healthy", "score": 90}})
    payload = topo.to_dict()
    r0 = payload["routers"][0]
    assert "health_state" in r0
    assert r0["health_state"] == "healthy"
    # Secret keys still not leaked.
    assert "secret" not in r0
    assert "api_password" not in r0


# ─── Route ───────────────────────────────────────────────────


def test_topology_route_login_guarded(client):
    res = client.get("/admin/radius/topology",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_topology_route_renders_health_filter(app, client):
    _seed_nas(app, nas_id=1)
    _login(client)
    html = client.get("/admin/radius/topology").get_data(as_text=True)
    assert "data-mt-topology-health" in html
    # Filter dropdown labels.
    assert "سليم" in html
    assert "يحتاج انتباه" in html


def test_topology_route_decorates_routers_with_health(app, client):
    _seed_nas(app, nas_id=2)
    _login(client)
    html = client.get("/admin/radius/topology").get_data(as_text=True)
    # The chip wrapper carries the state attribute.
    assert 'data-mt-topology-health="' in html
    assert "data-mt-topology-health-chip" in html


def test_topology_health_filter_narrows_results(app, client):
    # A disabled router scores STATE_OFFLINE (per O2 short-circuit).
    _seed_nas(app, nas_id=3, enabled=False)
    _seed_nas(app, nas_id=4, enabled=True)
    _login(client)
    res = client.get(
        "/admin/radius/topology?health=offline")
    html = res.get_data(as_text=True)
    # The offline disabled router stays.
    assert 'data-mt-topology-node="3"' in html
    # The enabled (non-offline) router is filtered out.
    assert 'data-mt-topology-node="4"' not in html


def test_topology_unknown_health_value_silently_ignored(app, client):
    _seed_nas(app, nas_id=5)
    _login(client)
    res = client.get("/admin/radius/topology?health=bogus")
    assert res.status_code == 200
    # Falls back to 'all' — the router is still listed.
    assert 'data-mt-topology-node="5"' in res.get_data(as_text=True)
