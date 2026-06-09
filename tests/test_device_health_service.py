"""device_health service + repo — CRUD, duplicate prevention, network-scope
duplicates, status transitions, alert dedup.  NO live MikroTik (router never
contacted in these paths).

Run individually:  pytest tests/test_device_health_service.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_svc_")
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


def _seed_router(app, router_id: int = 11, name: str = "راوتر الاختبار") -> None:
    with app.app_context():
        from app.radius.db.connection import transaction

        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password, created_at)
                   VALUES (?, 1, ?, '10.0.0.1', 'secret',
                           'mikrotik', 'hotspot', 1, 'api', 'pw', ?)""",
                (router_id, name, now),
            )


def test_create_computes_network_and_persists(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.db.repos import device_health_repo as repo

        out = svc.create_device(1, {
            "router_id": 11, "name": "AP المدخل", "device_type": "ap",
            "interface_name": "ether2", "ip_address": "192.168.15.10",
            "location": "البرج", "subnet_prefix": 24, "gateway_last_octet": 254,
        })
        d = repo.get_device(1, out["device_id"])
        assert d["network_cidr"] == "192.168.15.0/24"
        assert d["gateway_address"] == "192.168.15.254/24"
        assert d["interface_name"] == "ether2"
        assert d["status"] == "unknown"
        # Scope + binding recorded as pending (no live apply).
        scopes = repo.list_scopes(1, router_id=11)
        assert any(s["network_cidr"] == "192.168.15.0/24"
                   and s["apply_status"] == "pending" for s in scopes)
        assert repo.list_bindings(1, router_id=11)


def test_create_requires_interface(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc

        with pytest.raises(svc.DeviceHealthError):
            svc.create_device(1, {
                "router_id": 11, "name": "x", "interface_name": "",
                "ip_address": "192.168.15.10"})


def test_create_invalid_ip_rejected(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc

        with pytest.raises(svc.DeviceHealthError):
            svc.create_device(1, {
                "router_id": 11, "name": "x", "interface_name": "ether2",
                "ip_address": "not-an-ip"})


def test_duplicate_device_same_router_ip_blocked(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc

        base = {"router_id": 11, "name": "AP1", "interface_name": "ether2",
                "ip_address": "192.168.15.10"}
        svc.create_device(1, base)
        with pytest.raises(svc.DeviceHealthError):
            svc.create_device(1, dict(base, name="AP1-dup"))


def test_same_subnet_other_interface_allowed_with_warning(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.db.repos import device_health_repo as repo

        svc.create_device(1, {
            "router_id": 11, "name": "AP-eth2", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})
        out2 = svc.create_device(1, {
            "router_id": 11, "name": "AP-eth5", "interface_name": "ether5",
            "ip_address": "192.168.15.11"})
        # Allowed as a separate device + separate scope, but warned.
        assert out2["device_id"]
        assert any("غموض" in w or "مدخل آخر" in w for w in out2["warnings"])
        scopes = repo.scopes_for_network(1, 11, "192.168.15.0/24")
        ifaces = {s["interface_name"] for s in scopes}
        assert ifaces == {"ether2", "ether5"}


def test_soft_delete_frees_duplicate_slot(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.db.repos import device_health_repo as repo

        out = svc.create_device(1, {
            "router_id": 11, "name": "AP1", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})
        assert svc.delete_device(1, out["device_id"]) is True
        # Same router+IP can be re-added once the previous row is soft-deleted.
        out2 = svc.create_device(1, {
            "router_id": 11, "name": "AP1-new", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})
        assert out2["device_id"] != out["device_id"]
        assert repo.get_device(1, out["device_id"]) is None  # hidden (deleted)


def test_set_status_transitions_and_counters(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.db.repos import device_health_repo as repo

        did = svc.create_device(1, {
            "router_id": 11, "name": "AP", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})["device_id"]

        # up → down → down (counter climbs, last_down_at set)
        repo.set_status(tenant_id=1, device_id=did, status="up", latency_ms=12.0)
        repo.set_status(tenant_id=1, device_id=did, status="down")
        repo.set_status(tenant_id=1, device_id=did, status="down")
        d = repo.get_device(1, did)
        assert d["status"] == "down"
        assert d["consecutive_down_count"] == 2
        assert d["last_down_at"]
        assert d["last_status_change_at"]

        # recovery resets the down counter and stamps last_up_at
        repo.set_status(tenant_id=1, device_id=did, status="up", latency_ms=8.0)
        d2 = repo.get_device(1, did)
        assert d2["status"] == "up"
        assert d2["consecutive_down_count"] == 0
        assert d2["last_up_at"]
        assert d2["last_latency_ms"] == 8.0


def test_alert_dedup_helper(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.db.repos import device_health_repo as repo

        did = svc.create_device(1, {
            "router_id": 11, "name": "AP", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})["device_id"]
        key = f"{did}:down"
        assert repo.last_alert_at(1, key) is None
        repo.add_alert(tenant_id=1, device_id=did, alert_type="down",
                       channel="telegram", status="sent", dedup_key=key,
                       message="down")
        # Newest fire for the bucket is now retrievable — cooldown gate input.
        assert repo.last_alert_at(1, key) is not None


def test_high_latency_status_bucket(app):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import device_health_repo as repo
        from app.radius.services import device_health as svc

        did = svc.create_device(1, {
            "router_id": 11, "name": "AP", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})["device_id"]
        repo.set_status(tenant_id=1, device_id=did, status="high_latency",
                        latency_ms=140.0)
        s = svc.summary(1)
        assert s["high_latency"] == 1
        assert s["total"] == 1
