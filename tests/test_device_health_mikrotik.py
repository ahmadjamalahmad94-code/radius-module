"""device_health Phase 2 — MikroTik read wrapper + live diff with a MOCKED
client, plus the live-apply safety gate. No real router is contacted; the
admin-client reads are monkeypatched to return canned MtResult rows.

Run individually:  pytest tests/test_device_health_mikrotik.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_mt_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


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


def _make_device(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        return svc.create_device(1, {
            "router_id": 11, "name": "AP", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})["device_id"]


def test_read_router_state_assembles_three_lists(app, monkeypatch):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult

        monkeypatch.setattr(dhmt, "read_ip_addresses",
                            lambda nas: MtResult(ok=True, data=[{"address": "192.168.15.254/24", "interface": "ether2"}]))
        monkeypatch.setattr(dhmt, "read_ip_bindings",
                            lambda nas: MtResult(ok=True, data=[{"address": "192.168.15.0/24", "type": "bypassed"}]))
        monkeypatch.setattr(dhmt, "read_netwatch",
                            lambda nas: MtResult(ok=True, data=[{"host": "192.168.15.10"}]))

        state = dhmt.read_router_state({"id": 11})
        assert state["ok"] is True
        assert state["addresses"] and state["bindings"] and state["netwatch"]


def test_read_router_state_soft_fails_per_resource(app, monkeypatch):
    _seed_router(app)
    with app.app_context():
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult

        monkeypatch.setattr(dhmt, "read_ip_addresses",
                            lambda nas: MtResult(ok=False, error="تعذر الاتصال"))
        monkeypatch.setattr(dhmt, "read_ip_bindings",
                            lambda nas: MtResult(ok=True, data=[]))
        monkeypatch.setattr(dhmt, "read_netwatch",
                            lambda nas: MtResult(ok=True, data=[]))
        state = dhmt.read_router_state({"id": 11})
        assert state["ok"] is False
        assert "addresses" in state["errors"]


def test_live_plan_marks_already_present(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt

        # Router already has everything → idempotent plan.
        monkeypatch.setattr(dhmt, "read_router_state", lambda nas: {
            "ok": True,
            "addresses": [{"address": "192.168.15.254/24", "interface": "ether2"}],
            # MT109/MT110 — التجاوز صار على عنوان الجهاز وحده؛ ربط
            # الشبكة لم يعد يُعدّ «موجودًا سلفًا» لأنّه ليس ما نكتبه.
            "bindings": [{"address": "192.168.15.10", "type": "bypassed"}],
            "netwatch": [{"host": "192.168.15.10"}],
            "errors": {},
        })
        result = svc.live_plan(1, did)
        assert result["ok"] is True
        actions = {it["kind"]: it["action"] for it in result["plan"]["items"]}
        assert actions == {"ip_address": "already_present",
                           "ip_binding": "already_present",
                           "netwatch": "already_present"}


def test_live_plan_marks_create_when_router_empty(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt

        monkeypatch.setattr(dhmt, "read_router_state", lambda nas: {
            "ok": True, "addresses": [], "bindings": [], "netwatch": [],
            "errors": {}})
        result = svc.live_plan(1, did)
        actions = {it["kind"]: it["action"] for it in result["plan"]["items"]}
        assert actions == {"ip_address": "create", "ip_binding": "create",
                           "netwatch": "create"}


def test_test_ping_parses_latency_and_persists(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult
        from app.radius.db.repos import device_health_repo as repo

        monkeypatch.setattr(dhmt, "ping", lambda nas, target, count=4:
                            MtResult(ok=True, data=[{"time": "2ms"}, {"time": "3ms"}]))
        out = svc.test_ping(1, did)
        assert out["ok"] is True
        assert out["status"] == "up"
        assert out["latency_ms"] == pytest.approx(2.5, abs=0.1)
        # Status persisted on the device row.
        assert repo.get_device(1, did)["status"] == "up"


def test_test_ping_high_latency_status(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult

        # threshold default 80ms; 200ms avg → high_latency
        monkeypatch.setattr(dhmt, "ping", lambda nas, target, count=4:
                            MtResult(ok=True, data=[{"time": "200ms"}]))
        out = svc.test_ping(1, did)
        assert out["status"] == "high_latency"


def test_live_apply_gate_blocks_without_toggle(app):
    """Safety: write helpers refuse unless the panel toggle is ON — proves no
    accidental live mutation while the default-OFF gate is closed."""
    with app.app_context():
        from app.radius.services import device_health_mikrotik as dhmt

        # live=False → refused
        r1 = dhmt.add_ip_address({"id": 11, "tenant_id": 1},
                                 address="192.168.15.254/24",
                                 interface="ether2", live=False)
        assert r1.ok is False
        # live=True but panel toggle OFF (default) → still refused, and the
        # message points to the panel switch (not the terminal/env var).
        r2 = dhmt.add_netwatch({"id": 11, "tenant_id": 1},
                               host="192.168.15.10", live=True)
        assert r2.ok is False
        assert "معطّل" in r2.error and "اللوحة" in r2.error
