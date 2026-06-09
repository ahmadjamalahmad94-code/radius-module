"""device_health Phase 5 — alert dedup state machine (delivery seam stubbed).

Verifies: down fires only after N consecutive fails, repeat suppressed within
cooldown, recovery fires on return, high-latency fires after N, and a fire
again once the cooldown elapses. No real channel is contacted (_send stubbed).

Run individually:  pytest tests/test_device_health_alerts.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_alerts_")
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


def _device(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        return svc.create_device(1, {
            "router_id": 11, "name": "AP", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})["device_id"]


def _stub_send(alerts_mod, captured):
    alerts_mod._send = (lambda tid, ch, atype, msg, dev, lat:
                        captured.append(atype) or True)


def test_down_fires_only_after_n_consecutive(app, monkeypatch):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_alerts as al
        from app.radius.db.repos import device_health_repo as repo

        sent = []
        _stub_send(al, sent)

        # first down → count 1 < N → no alert
        repo.set_status(tenant_id=1, device_id=did, status="down")
        d = repo.get_device(1, did)
        assert al.evaluate_and_dispatch(tenant_id=1, device=d,
                                        prev_status="unknown", new_status="down") == []
        # second down → count 2 == N → fires
        repo.set_status(tenant_id=1, device_id=did, status="down")
        d = repo.get_device(1, did)
        fired = al.evaluate_and_dispatch(tenant_id=1, device=d,
                                         prev_status="down", new_status="down")
        assert fired == ["down"]
        assert sent == ["down"]


def test_down_repeat_suppressed_within_cooldown(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_alerts as al
        from app.radius.db.repos import device_health_repo as repo

        sent = []
        _stub_send(al, sent)
        for _ in range(3):
            repo.set_status(tenant_id=1, device_id=did, status="down")
        d = repo.get_device(1, did)
        # first eval fires
        assert al.evaluate_and_dispatch(tenant_id=1, device=d,
                                        prev_status="down", new_status="down") == ["down"]
        # immediate second eval → cooldown → skipped (no new send)
        repo.set_status(tenant_id=1, device_id=did, status="down")
        d = repo.get_device(1, did)
        assert al.evaluate_and_dispatch(tenant_id=1, device=d,
                                        prev_status="down", new_status="down") == []
        assert sent == ["down"]   # only one delivery
        # a skipped row was recorded
        alerts = repo.list_events(1, device_id=did)  # sanity: events table separate
        assert repo.last_alert_at(1, f"{did}:down") is not None


def test_recovery_fires_on_return(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_alerts as al
        from app.radius.db.repos import device_health_repo as repo

        sent = []
        _stub_send(al, sent)
        repo.set_status(tenant_id=1, device_id=did, status="up", latency_ms=10.0)
        d = repo.get_device(1, did)
        fired = al.evaluate_and_dispatch(tenant_id=1, device=d,
                                         prev_status="down", new_status="up",
                                         latency_ms=10.0)
        assert fired == ["recovery"]


def test_high_latency_fires_after_n(app):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_alerts as al
        from app.radius.db.repos import device_health_repo as repo

        sent = []
        _stub_send(al, sent)
        # below threshold count
        for _ in range(2):
            repo.set_status(tenant_id=1, device_id=did, status="high_latency", latency_ms=200.0)
        d = repo.get_device(1, did)
        assert al.evaluate_and_dispatch(tenant_id=1, device=d,
                                        prev_status="up", new_status="high_latency",
                                        latency_ms=200.0) == []
        # third sample reaches N
        repo.set_status(tenant_id=1, device_id=did, status="high_latency", latency_ms=200.0)
        d = repo.get_device(1, did)
        assert al.evaluate_and_dispatch(tenant_id=1, device=d,
                                        prev_status="high_latency", new_status="high_latency",
                                        latency_ms=200.0) == ["high_latency"]


def test_down_fires_again_after_cooldown_elapses(app, monkeypatch):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_alerts as al
        from app.radius.db.repos import device_health_repo as repo

        sent = []
        _stub_send(al, sent)
        for _ in range(2):
            repo.set_status(tenant_id=1, device_id=did, status="down")
        d = repo.get_device(1, did)
        assert al.evaluate_and_dispatch(tenant_id=1, device=d,
                                        prev_status="down", new_status="down") == ["down"]
        # Simulate the cooldown window having elapsed.
        monkeypatch.setattr(al, "_seconds_since", lambda iso: 99999.0)
        repo.set_status(tenant_id=1, device_id=did, status="down")
        d = repo.get_device(1, did)
        assert al.evaluate_and_dispatch(tenant_id=1, device=d,
                                        prev_status="down", new_status="down") == ["down"]
        assert sent == ["down", "down"]


def test_poller_integration_fires_alert(app, monkeypatch):
    """End-to-end: the poller calls the real dispatcher; a down device past the
    threshold produces a recorded alert (delivery stubbed)."""
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.services import device_health_alerts as al
        from app.radius.db.repos import device_health_repo as repo
        from types import SimpleNamespace

        sent = []
        _stub_send(al, sent)

        class _Mt:
            def read_netwatch(self, nas):
                return SimpleNamespace(ok=True, data=[{"host": "192.168.15.10", "status": "down"}], error="")
            def ping(self, nas, target, count=4):
                return SimpleNamespace(ok=False, data=[], error="x")

        mt = _Mt()
        # Two sweeps → consecutive_down_count reaches 2 → alert fires on 2nd.
        poller.tick(tenant_id=1, mt=mt)
        poller.tick(tenant_id=1, mt=mt)
        assert "down" in sent
        assert repo.last_alert_at(1, f"{did}:down") is not None
