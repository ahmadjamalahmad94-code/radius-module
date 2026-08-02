"""device_health Phase 3 — controlled live apply with a MOCKED MikroTik client.

Verifies: the master gate blocks by default (no router I/O), apply writes only
the missing planned items when enabled, is idempotent (already_present skipped),
records apply_status on scopes/bindings, and never raises. No live device.

Run individually:  pytest tests/test_device_health_apply.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_apply_")
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


def test_apply_gated_off_by_default_does_no_io(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt

        called = {"read": 0}
        monkeypatch.setattr(dhmt, "read_router_state",
                            lambda nas: called.__setitem__("read", called["read"] + 1) or {})
        out = svc.apply_device(1, did)
        assert out["gated"] is True and out["ok"] is False
        assert called["read"] == 0   # router never contacted while gated


def test_apply_creates_missing_items_and_records(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    # Enable live-apply via the panel toggle (env no longer enables; it can
    # only force-disable). Set it in its own context, then run the test.
    with app.app_context():
        from app.radius.services import device_health as _svc
        _svc.set_live_apply(1, True)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult
        from app.radius.db.repos import device_health_repo as repo

        # Empty router → all three created.
        monkeypatch.setattr(dhmt, "read_router_state", lambda nas: {
            "ok": True, "addresses": [], "bindings": [], "netwatch": [], "errors": {}})
        writes = []
        monkeypatch.setattr(dhmt, "add_ip_address",
                            lambda nas, **k: writes.append("ip_address") or MtResult(ok=True, data="*1"))
        monkeypatch.setattr(dhmt, "add_ip_binding",
                            lambda nas, **k: writes.append("ip_binding") or MtResult(ok=True, data="*2"))
        monkeypatch.setattr(dhmt, "add_netwatch",
                            lambda nas, **k: writes.append("netwatch") or MtResult(ok=True, data="*3"))

        out = svc.apply_device(1, did)
        assert out["ok"] is True
        assert set(out["applied"]) == {"ip_address", "ip_binding", "netwatch"}
        assert set(writes) == {"ip_address", "ip_binding", "netwatch"}
        # Bookkeeping persisted.
        scope = repo.scopes_for_network(1, 11, "192.168.15.0/24")[0]
        assert scope["apply_status"] == "applied"
        assert scope["mikrotik_address_id"] == "*1"
        assert repo.get_device(1, did)["mikrotik_netwatch_id"] == "*3"


def test_apply_is_idempotent_when_present(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    # Enable live-apply via the panel toggle (env no longer enables; it can
    # only force-disable). Set it in its own context, then run the test.
    with app.app_context():
        from app.radius.services import device_health as _svc
        _svc.set_live_apply(1, True)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult

        monkeypatch.setattr(dhmt, "read_router_state", lambda nas: {
            "ok": True,
            "addresses": [{"address": "192.168.15.254/24", "interface": "ether2"}],
            "bindings": [{"address": "192.168.15.10", "type": "bypassed"}],
            "netwatch": [{"host": "192.168.15.10"}],
            "errors": {}})
        calls = []
        for fn in ("add_ip_address", "add_ip_binding", "add_netwatch"):
            monkeypatch.setattr(dhmt, fn,
                                lambda nas, **k: calls.append(1) or MtResult(ok=True, data="x"))
        out = svc.apply_device(1, did)
        assert out["ok"] is True
        assert out["applied"] == []
        assert set(out["already_present"]) == {"ip_address", "ip_binding", "netwatch"}
        assert calls == []   # nothing written — fully idempotent


def test_apply_partial_failure_marks_apply_failed(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    # Enable live-apply via the panel toggle (env no longer enables; it can
    # only force-disable). Set it in its own context, then run the test.
    with app.app_context():
        from app.radius.services import device_health as _svc
        _svc.set_live_apply(1, True)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult
        from app.radius.db.repos import device_health_repo as repo

        monkeypatch.setattr(dhmt, "read_router_state", lambda nas: {
            "ok": True, "addresses": [], "bindings": [], "netwatch": [], "errors": {}})
        monkeypatch.setattr(dhmt, "add_ip_address", lambda nas, **k: MtResult(ok=True, data="*1"))
        monkeypatch.setattr(dhmt, "add_ip_binding", lambda nas, **k: MtResult(ok=True, data="*2"))
        monkeypatch.setattr(dhmt, "add_netwatch",
                            lambda nas, **k: MtResult(ok=False, error="رفض الراوتر"))
        out = svc.apply_device(1, did)
        assert out["ok"] is False
        assert "ip_address" in out["applied"]
        assert any(f["kind"] == "netwatch" for f in out["failed"])
        assert repo.get_device(1, did)["status"] == "apply_failed"


def test_apply_actions_filter_restricts_kinds(app, monkeypatch):
    _seed_router(app)
    did = _make_device(app)
    # Enable live-apply via the panel toggle (env no longer enables; it can
    # only force-disable). Set it in its own context, then run the test.
    with app.app_context():
        from app.radius.services import device_health as _svc
        _svc.set_live_apply(1, True)
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.services import device_health_mikrotik as dhmt
        from app.radius.services.mikrotik_admin_client import MtResult

        monkeypatch.setattr(dhmt, "read_router_state", lambda nas: {
            "ok": True, "addresses": [], "bindings": [], "netwatch": [], "errors": {}})
        written = []
        monkeypatch.setattr(dhmt, "add_ip_address", lambda nas, **k: written.append("ip_address") or MtResult(ok=True, data="*1"))
        monkeypatch.setattr(dhmt, "add_ip_binding", lambda nas, **k: written.append("ip_binding") or MtResult(ok=True, data="*2"))
        monkeypatch.setattr(dhmt, "add_netwatch", lambda nas, **k: written.append("netwatch") or MtResult(ok=True, data="*3"))

        out = svc.apply_device(1, did, actions=["netwatch"])
        assert out["applied"] == ["netwatch"]
        assert written == ["netwatch"]


def test_apply_route_gated_returns_200(app):
    _seed_router(app)
    did = _make_device(app)
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "csrf"
    res = client.post(f"/admin/radius/device-health/api/devices/{did}/apply",
                      headers={"X-CSRFToken": "csrf", "Content-Type": "application/json"},
                      json={})
    assert res.status_code == 200
    assert res.get_json()["gated"] is True
