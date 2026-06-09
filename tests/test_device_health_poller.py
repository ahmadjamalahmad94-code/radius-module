"""device_health Phase 4 — polling + status with a MOCKED MikroTik client.

Verifies status derivation from Netwatch (+ ping fallback for latency),
status-change events, and the unreachable-router → 'unknown' path. No live
device. alert_fn is stubbed so Phase-4 logic is isolated from Phase-5.

Run individually:  pytest tests/test_device_health_poller.py -q
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
    tmp = tempfile.mkdtemp(prefix="hr_device_health_poll_")
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


def _device(app, ip="192.168.15.10", name="AP"):
    with app.app_context():
        from app.radius.services import device_health as svc
        return svc.create_device(1, {
            "router_id": 11, "name": name, "interface_name": "ether2",
            "ip_address": ip})["device_id"]


class _FakeMt:
    """Minimal stand-in for device_health_mikrotik."""
    def __init__(self, netwatch=None, nw_ok=True, ping_rows=None, ping_ok=True):
        self._nw = netwatch if netwatch is not None else []
        self._nw_ok = nw_ok
        self._ping_rows = ping_rows if ping_rows is not None else [{"time": "5ms"}]
        self._ping_ok = ping_ok

    def read_netwatch(self, nas):
        return SimpleNamespace(ok=self._nw_ok, data=self._nw, error="")

    def ping(self, nas, target, count=4):
        return SimpleNamespace(ok=self._ping_ok, data=self._ping_rows, error="")


def _no_alerts(**kwargs):
    return []


def test_netwatch_up_with_ping_latency(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "up"}],
                     ping_rows=[{"time": "5ms"}, {"time": "7ms"}])
        summary = poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert summary["up"] == 1 and summary["changed"] == 1
        d = repo.get_device(1, did)
        assert d["status"] == "up"
        assert d["last_latency_ms"] == pytest.approx(6.0, abs=0.1)


def test_netwatch_down_marks_down_and_event(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "down"}])
        summary = poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert summary["down"] == 1
        d = repo.get_device(1, did)
        assert d["status"] == "down"
        assert d["consecutive_down_count"] == 1
        events = repo.list_events(1, device_id=did)
        assert any(e["event_type"] == "down" for e in events)


def test_high_latency_from_ping(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "up"}],
                     ping_rows=[{"time": "250ms"}])  # > 80ms threshold
        poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert repo.get_device(1, did)["status"] == "high_latency"


def test_unreachable_router_is_unknown(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        # Netwatch unreadable AND ping fails → unknown.
        mt = _FakeMt(netwatch=[], nw_ok=False, ping_ok=False)
        summary = poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert summary["unknown"] == 1
        assert repo.get_device(1, did)["status"] == "unknown"


def test_netwatch_up_but_ping_filtered_keeps_up(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        # Netwatch up, but ICMP filtered (ping has only timeout rows) → stay up.
        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "up"}],
                     ping_rows=[{"status": "timeout"}])
        poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert repo.get_device(1, did)["status"] == "up"


def test_no_change_no_event(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "down"}])
        poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        first = len(repo.list_events(1, device_id=did))
        poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)  # same status
        second = len(repo.list_events(1, device_id=did))
        assert second == first  # no duplicate event for unchanged status


def test_ping_fallback_when_no_netwatch_row(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        # Netwatch readable but empty → ping fallback decides.
        mt = _FakeMt(netwatch=[], nw_ok=True, ping_rows=[{"time": "10ms"}])
        poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert repo.get_device(1, did)["status"] == "up"
