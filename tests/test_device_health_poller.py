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


def test_real_down_marks_down_and_event(app):
    # A real «مفصول»: the direct ping gets no replies AND netwatch isn't up.
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "down"}],
                     ping_rows=[{"status": "timeout"}])  # ping ran, no replies
        summary = poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert summary["down"] == 1
        d = repo.get_device(1, did)
        assert d["status"] == "down"
        assert d["consecutive_down_count"] == 1
        events = repo.list_events(1, device_id=did)
        assert any(e["event_type"] == "down" for e in events)


def test_ping_up_overrides_stale_netwatch_down(app):
    # Device answers the direct ping even though netwatch row says down (stale)
    # → up. Ping is the source of truth (matches the manual «فحص بنج»).
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo

        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "down"}],
                     ping_rows=[{"time": "6ms"}])
        poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)
        assert repo.get_device(1, did)["status"] == "up"


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


def test_iter_tick_streams_progress_then_done(app):
    # The streaming sweep yields start → progress×N → done, with a live count.
    _seed_router(app)
    d1 = _device(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_poller as poller
        # Second monitored device on a different interface/IP.
        svc.create_device(1, {"router_id": 11, "name": "AP2",
                              "interface_name": "ether3", "ip_address": "10.0.9.10"})

        mt = _FakeMt(netwatch=[], nw_ok=True, ping_rows=[{"time": "5ms"}])
        events = list(poller.iter_tick(tenant_id=1, mt=mt, alert_fn=_no_alerts))
        types = [e["type"] for e in events]
        assert types[0] == "start" and types[-1] == "done"
        progs = [e for e in events if e["type"] == "progress"]
        assert len(progs) == 2
        assert progs[0]["total"] == 2
        assert progs[-1]["index"] == 2
        # Every progress event carries the device id so the row can update live.
        assert all("device_id" in p and "status" in p for p in progs)
        assert events[-1]["summary"]["up"] == 2
        # tick() still returns the same final summary.
        assert poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts)["up"] == 2


def test_bugfix_sync_all_matches_manual_ping_when_netwatch_absent(app, monkeypatch):
    """Owner bug: live-apply OFF → netwatch never pushed (no data). The device is
    reachable, so the manual «فحص بنج» says «متصل». Sync-All must AGREE (up) and
    must NOT falsely report «مفصول»."""
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from types import SimpleNamespace
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_poller as poller
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.db.repos import device_health_repo as repo

        # Router reachable, device answers ping; netwatch never applied (empty).
        monkeypatch.setattr(dhmt, "ping", lambda nas, target, count=4:
                            SimpleNamespace(ok=True, data=[{"time": "9ms"}], error=""))
        monkeypatch.setattr(dhmt, "read_netwatch", lambda nas:
                            SimpleNamespace(ok=True, data=[], error=""))

        manual = svc.test_ping(1, did)                       # «فحص بنج»
        poller.tick(tenant_id=1, mt=dhmt, alert_fn=_no_alerts)  # «فحص الكل»
        synced = repo.get_device(1, did)

        assert manual["status"] == "up"
        assert synced["status"] == "up"            # NOT «down»/مفصول
        assert manual["status"] == synced["status"]  # the two never contradict


def test_bugfix_unknown_not_down_when_router_unreachable_and_no_netwatch(app, monkeypatch):
    """Router unreachable + netwatch absent → «unknown» (grey), never a false
    «مفصول»; the manual ping reports the same."""
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from types import SimpleNamespace
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_poller as poller
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.db.repos import device_health_repo as repo

        monkeypatch.setattr(dhmt, "ping", lambda nas, target, count=4:
                            SimpleNamespace(ok=False, data=[], error="تعذر الاتصال"))
        monkeypatch.setattr(dhmt, "read_netwatch", lambda nas:
                            SimpleNamespace(ok=False, data=[], error="تعذر الاتصال"))

        manual = svc.test_ping(1, did)
        poller.tick(tenant_id=1, mt=dhmt, alert_fn=_no_alerts)
        synced = repo.get_device(1, did)

        assert manual["status"] == "unknown"
        assert synced["status"] == "unknown"       # NOT «down»/مفصول
