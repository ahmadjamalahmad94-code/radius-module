"""S5.1 — Topology data aggregator."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s5_1_")
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


def _seed_nas(app, *, nas_id, name, mode="direct", enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password, vpn_peer_address)
                   VALUES (?, 1, ?, ?, 'shared-radius-secret', 'mikrotik',
                           'hotspot', ?, ?, ?, 'hr', 'mt-pwd', '10.10.0.7')""",
                (nas_id, name, f"203.0.113.{nas_id}",
                 1 if enabled else 0, now, mode),
            )


def test_topology_empty_fleet_renders_only_server(app):
    with app.app_context():
        from app.radius.services.mt_topology import build_topology
        topo = build_topology(1)
    assert topo.server.kind == "server"
    assert topo.routers == []
    assert topo.links == []


def test_topology_lists_each_router_as_node(app):
    _seed_nas(app, nas_id=10, name="rtr-a")
    _seed_nas(app, nas_id=20, name="rtr-b")
    with app.app_context():
        from app.radius.services.mt_topology import build_topology
        topo = build_topology(1)
    assert {r.label for r in topo.routers} == {"rtr-a", "rtr-b"}
    assert {r.id for r in topo.routers} == {"10", "20"}


def test_topology_links_each_router_to_server(app):
    _seed_nas(app, nas_id=10, name="rtr-a", mode="direct")
    _seed_nas(app, nas_id=20, name="rtr-b", mode="vpn")
    with app.app_context():
        from app.radius.services.mt_topology import build_topology
        topo = build_topology(1)
    assert len(topo.links) == 2
    by_target = {l.target: l for l in topo.links}
    assert by_target["10"].kind == "direct"
    assert by_target["20"].kind == "vpn"
    assert all(l.source == "server" for l in topo.links)


def test_topology_marks_disabled_router_status(app):
    _seed_nas(app, nas_id=30, name="rtr-off", enabled=False)
    with app.app_context():
        from app.radius.services.mt_topology import build_topology
        topo = build_topology(1)
    by_id = {r.id: r for r in topo.routers}
    assert by_id["30"].status == "disabled"


def test_topology_to_dict_does_not_leak_secrets(app):
    """The projected JSON must NOT include api_password,
    api_user, secret. Pin both shape + absence-of-bad-keys."""
    _seed_nas(app, nas_id=40, name="rtr-c")
    with app.app_context():
        from app.radius.services.mt_topology import build_topology
        topo = build_topology(1)
    blob = topo.to_dict()
    # Recursive scan: no string value should equal a known secret.
    import json
    raw = json.dumps(blob, ensure_ascii=False)
    assert "shared-radius-secret" not in raw
    assert "mt-pwd" not in raw
    # The keys we DO want.
    assert blob["server"]["kind"] == "server"
    assert any(r["id"] == "40" for r in blob["routers"])


def test_overlay_snapshots_promotes_status(app):
    """When the S7 snapshot cache lands, online state flows
    through here without changing the aggregator's signature."""
    _seed_nas(app, nas_id=50, name="snap-a")
    _seed_nas(app, nas_id=60, name="snap-b", enabled=False)
    with app.app_context():
        from app.radius.services.mt_topology import (
            build_topology, overlay_snapshots,
        )
        topo = build_topology(1)
        snapshots = {
            "50": {"last_success_at": "2026-05-22T18:00:00Z"},
            "60": {"last_success_at": "2026-05-22T18:00:00Z"},
        }
        out = overlay_snapshots(topo, snapshots)
    by_id = {r.id: r for r in out.routers}
    # 50 was enabled → upgraded to online.
    assert by_id["50"].status == "online"
    # 60 was disabled → snapshot does NOT override the disabled
    # operator state (that'd be misleading).
    assert by_id["60"].status == "disabled"


def test_overlay_snapshots_marks_offline_from_last_error(app):
    _seed_nas(app, nas_id=70, name="snap-c")
    with app.app_context():
        from app.radius.services.mt_topology import (
            build_topology, overlay_snapshots,
        )
        topo = build_topology(1)
        snapshots = {
            "70": {"last_success_at": "", "last_error": "timeout"},
        }
        out = overlay_snapshots(topo, snapshots)
    by_id = {r.id: r for r in out.routers}
    assert by_id["70"].status == "offline"


def test_overlay_with_no_snapshots_is_noop(app):
    _seed_nas(app, nas_id=80, name="noop")
    with app.app_context():
        from app.radius.services.mt_topology import (
            build_topology, overlay_snapshots,
        )
        topo = build_topology(1)
        out = overlay_snapshots(topo, None)
    assert {r.id for r in out.routers} == {"80"}
    assert out.routers[0].status == "unknown"
