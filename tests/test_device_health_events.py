"""device_health Phase 6 — event history + alerts JSON endpoints.

Run individually:  pytest tests/test_device_health_events.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_events_")
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


def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "csrf"


def test_events_endpoint_returns_created_event(app, client):
    _seed_router(app)
    _login(client)
    created = client.post(
        "/admin/radius/device-health/api/devices",
        headers={"X-CSRFToken": "csrf", "Content-Type": "application/json"},
        json={"router_id": 11, "name": "AP", "interface_name": "ether2",
              "ip_address": "192.168.15.10"}).get_json()
    did = created["device"]["id"]
    res = client.get(f"/admin/radius/device-health/api/devices/{did}/events")
    assert res.status_code == 200
    events = res.get_json()["events"]
    assert any(e["event_type"] == "created" for e in events)


def test_alerts_endpoint_returns_rows(app, client):
    _seed_router(app)
    _login(client)
    created = client.post(
        "/admin/radius/device-health/api/devices",
        headers={"X-CSRFToken": "csrf", "Content-Type": "application/json"},
        json={"router_id": 11, "name": "AP", "interface_name": "ether2",
              "ip_address": "192.168.15.10"}).get_json()
    did = created["device"]["id"]
    with app.app_context():
        from app.radius.db.repos import device_health_repo as repo
        repo.add_alert(tenant_id=1, device_id=did, alert_type="down",
                       channel="telegram", status="sent",
                       dedup_key=f"{did}:down", message="down")
    res = client.get(f"/admin/radius/device-health/api/devices/{did}/alerts")
    assert res.status_code == 200
    alerts = res.get_json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "down"
    assert alerts[0]["status"] == "sent"


def test_poll_endpoint_runs_sweep(app, client, monkeypatch):
    _seed_router(app)
    _login(client)
    client.post(
        "/admin/radius/device-health/api/devices",
        headers={"X-CSRFToken": "csrf", "Content-Type": "application/json"},
        json={"router_id": 11, "name": "AP", "interface_name": "ether2",
              "ip_address": "192.168.15.10"})
    # Router offline → sweep still returns a summary (device → unknown).
    res = client.post("/admin/radius/device-health/api/poll",
                      headers={"X-CSRFToken": "csrf"})
    assert res.status_code == 200
    summary = res.get_json()["summary"]
    assert summary["scanned"] == 1


def test_poll_stream_route_emits_ndjson(app, client, monkeypatch):
    import json
    from types import SimpleNamespace
    _seed_router(app)
    _login(client)
    client.post(
        "/admin/radius/device-health/api/devices",
        headers={"X-CSRFToken": "csrf", "Content-Type": "application/json"},
        json={"router_id": 11, "name": "AP", "interface_name": "ether2",
              "ip_address": "192.168.15.10"})
    # Make the router reads instant (offline) so the stream is fast + deterministic.
    with app.app_context():
        from app.radius.services import device_health_mikrotik as dhmt
        monkeypatch.setattr(dhmt, "read_netwatch",
                            lambda nas: SimpleNamespace(ok=False, data=[], error="x"))
        monkeypatch.setattr(dhmt, "ping",
                            lambda nas, target, count=4: SimpleNamespace(ok=False, data=[], error="x"))
        res = client.post("/admin/radius/device-health/api/poll/stream",
                          headers={"X-CSRFToken": "csrf"})
    assert res.status_code == 200
    assert "application/x-ndjson" in res.content_type
    lines = [json.loads(ln) for ln in res.get_data(as_text=True).splitlines() if ln.strip()]
    types = [e["type"] for e in lines]
    assert types[0] == "start" and types[-1] == "done"
    progs = [e for e in lines if e["type"] == "progress"]
    assert len(progs) == 1 and progs[0]["total"] == 1
    # ping + netwatch فاشلان (الراوتر الأمّ مفصول) ⇒ «unavailable» لا «unknown»
    # الصامت (إصلاح fix/device-health-unknown-state).
    assert progs[0]["device_id"] and progs[0]["status"] == "unavailable"
    assert lines[-1]["summary"]["scanned"] == 1
