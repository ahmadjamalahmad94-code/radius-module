"""device-health — DB-backed live-apply panel toggle (replaces the env flag).

Verifies: default OFF (dry-run/gated), turning the panel toggle ON enables the
apply path (router mocked — no real write), the env var can still FORCE-DISABLE,
and the toggle persists + the route reads/writes it.

Run individually:  pytest tests/test_device_health_live_apply.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_liveapply_")
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


def _device(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        return svc.create_device(1, {
            "router_id": 11, "name": "AP", "interface_name": "ether2",
            "ip_address": "192.168.15.10"})["device_id"]


def test_default_off(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        st = svc.live_apply_state(1)
        assert st["enabled"] is False and st["effective"] is False
        assert st["env_forced_off"] is False


def test_toggle_on_persists_and_enables_gate(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as mt
        out = svc.set_live_apply(1, True, by=1)
        assert out["enabled"] is True and out["effective"] is True
        # Persisted: a fresh read sees it.
        assert mt.live_apply_db_enabled(1) is True
        assert mt.live_apply_enabled(1) is True


def test_apply_gated_when_off_then_applies_when_on(app, monkeypatch):
    _seed_router(app)
    did = _device(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as mt
        from app.radius.services.mikrotik_admin_client import MtResult

        # OFF (default) → gated, no router I/O
        reads = {"n": 0}
        monkeypatch.setattr(mt, "read_router_state",
                            lambda nas: reads.__setitem__("n", reads["n"] + 1) or {})
        out = svc.apply_device(1, did)
        assert out["gated"] is True and out["ok"] is False
        assert reads["n"] == 0

        # Turn the panel toggle ON → apply path runs (router mocked)
        svc.set_live_apply(1, True, by=1)
        monkeypatch.setattr(mt, "read_router_state", lambda nas: {
            "ok": True, "addresses": [], "bindings": [], "netwatch": [], "errors": {}})
        monkeypatch.setattr(mt, "add_ip_address", lambda nas, **k: MtResult(ok=True, data="*1"))
        monkeypatch.setattr(mt, "add_ip_binding", lambda nas, **k: MtResult(ok=True, data="*2"))
        monkeypatch.setattr(mt, "add_netwatch", lambda nas, **k: MtResult(ok=True, data="*3"))
        out2 = svc.apply_device(1, did)
        assert out2["ok"] is True
        assert set(out2["applied"]) == {"ip_address", "ip_binding", "netwatch"}


def test_env_can_force_disable_over_db_on(app, monkeypatch):
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as mt

        svc.set_live_apply(1, True, by=1)               # panel ON
        assert mt.live_apply_db_enabled(1) is True
        monkeypatch.setenv("HOBERADIUS_DEVICE_HEALTH_LIVE_APPLY", "0")  # hard off
        assert mt.env_force_disabled() is True
        assert mt.live_apply_enabled(1) is False        # effective gate is OFF
        st = svc.live_apply_state(1)
        assert st["enabled"] is True and st["effective"] is False \
            and st["env_forced_off"] is True


def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 7
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "csrf"


def test_route_get_and_post_toggle(app, client):
    _login(client)
    # GET initial state
    g = client.get("/admin/radius/device-health/api/live-apply").get_json()
    assert g["ok"] is True and g["enabled"] is False
    # POST enable
    p = client.post("/admin/radius/device-health/api/live-apply",
                    headers={"X-CSRFToken": "csrf", "Content-Type": "application/json"},
                    json={"enabled": True})
    assert p.status_code == 200
    body = p.get_json()
    assert body["ok"] is True and body["enabled"] is True and body["effective"] is True
    # Persisted across a fresh GET
    g2 = client.get("/admin/radius/device-health/api/live-apply").get_json()
    assert g2["enabled"] is True


def test_page_renders_toggle(app, client):
    _login(client)
    html = client.get("/admin/radius/device-health").get_data(as_text=True)
    assert "تفعيل التطبيق الحي على الراوترات" in html
    assert 'id="dh-live-apply-toggle"' in html
