"""device-health — dependent interface dropdown endpoint (MOCKED MikroTik).

Verifies that the router-interfaces endpoint/service reads /interface via the
admin client, reuses port_script_services.filter_lan_ports to drop WAN +
tunnel interfaces, and falls back gracefully (online=False) when offline.

Run individually:  pytest tests/test_device_health_interfaces.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_ifaces_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_router(app, router_id: int = 11) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction

        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password, created_at)
                   VALUES (?, 1, 'راوتر الاختبار', '10.0.0.1', 'secret',
                           'mikrotik', 'hotspot', 1, 'api', 'pw', ?)""",
                (router_id, now),
            )


# A realistic /interface/print snapshot: WAN (ether1) + LAN ethers + bridge +
# tunnels (pppoe / wireguard / loopback) that MUST be excluded.
_ROWS = [
    {"name": "ether1", "type": "ether"},       # WAN uplink → excluded (default_wan)
    {"name": "ether2", "type": "ether"},       # LAN ✓
    {"name": "ether3", "type": "ether"},       # LAN ✓
    {"name": "bridge-lan", "type": "bridge"},  # LAN ✓
    {"name": "pppoe-out1", "type": "pppoe-out"},   # tunnel → excluded
    {"name": "wg-mgmt", "type": "wireguard"},      # tunnel → excluded
    {"name": "lo", "type": "loopback"},            # loopback → excluded
]


def _patch_ifaces(monkeypatch, ok=True, rows=None):
    from app.radius.services import mikrotik_admin_client as mac
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: SimpleNamespace(ok=ok, data=(rows if rows is not None else _ROWS), error=""))


def test_service_excludes_wan_and_tunnels(app, monkeypatch):
    _seed_router(app)
    with app.app_context():
        _patch_ifaces(monkeypatch)
        from app.radius.services import device_health as svc
        out = svc.list_router_interfaces(1, 11)
        assert out["online"] is True
        assert out["interfaces"] == ["ether2", "ether3", "bridge-lan"]
        # WAN + tunnels are gone
        for excluded in ("ether1", "pppoe-out1", "wg-mgmt", "lo"):
            assert excluded not in out["interfaces"]


def test_service_offline_router_falls_back(app, monkeypatch):
    _seed_router(app)
    with app.app_context():
        _patch_ifaces(monkeypatch, ok=False)
        from app.radius.services import device_health as svc
        out = svc.list_router_interfaces(1, 11)
        assert out["online"] is False
        assert out["interfaces"] == []


def test_service_respects_configured_wan(app, monkeypatch):
    _seed_router(app)
    with app.app_context():
        _patch_ifaces(monkeypatch)
        from app.radius.services import device_health as svc
        # Configured WAN of ether2 (as the setup wizard would record) → it must
        # be excluded, and ether1 (no longer the default guard) becomes LAN.
        monkeypatch.setattr(svc, "_resolve_wan_iface", lambda tid, rid: "ether2")
        out = svc.list_router_interfaces(1, 11)
        assert "ether2" not in out["interfaces"]      # configured WAN excluded
        assert "ether1" in out["interfaces"]          # no longer the guard → LAN
        assert "ether3" in out["interfaces"]


def test_endpoint_returns_filtered_list(app, client, monkeypatch):
    _seed_router(app)
    _patch_ifaces(monkeypatch)
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "csrf"
    res = client.get("/admin/radius/device-health/api/router-interfaces?router_id=11")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True and body["online"] is True
    assert body["interfaces"] == ["ether2", "ether3", "bridge-lan"]


def test_endpoint_no_router_id_is_offline_not_error(app, client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    res = client.get("/admin/radius/device-health/api/router-interfaces")
    assert res.status_code == 200
    assert res.get_json()["online"] is False
