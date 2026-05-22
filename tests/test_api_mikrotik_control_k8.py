"""K8 endpoint-level tests.

These talk through the Flask test client so the API contract (auth,
status codes, envelope shape, confirm-guard) is exercised — not just
the service-layer fetchers (those live in
`test_mikrotik_admin_client.py`).
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_k8_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    created = create_app()

    # Seed a single nas_devices row so the routes resolve a router.
    with created.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                VALUES (1, 1, 'rtr-1', '203.0.113.20', 's', 'mikrotik',
                        'hotspot', 1, ?, 'direct', 'admin', 'x')
                """,
                (now,),
            )
    yield created
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _patch_pool(monkeypatch, mock_client):
    """Replace `_pool_acquire` inside `mikrotik_admin_client` with a
    ctxmanager that yields the supplied mock."""
    from app.radius.services import mikrotik_admin_client as mac

    @contextmanager
    def fake(cfg):
        yield mock_client

    monkeypatch.setattr(mac, "_pool_acquire", fake)


# ─── route registration sanity ───────────────────────────────────


def test_k8_routes_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200
    rules = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/mikrotik/<int:nas_id>/files" in rules
    assert "/api/v1/mikrotik/<int:nas_id>/system/backup/save" in rules
    assert "/api/v1/mikrotik/<int:nas_id>/files/<string:filename>/download" in rules
    assert "/api/v1/mikrotik/<int:nas_id>/system/reboot" in rules
    assert "/api/v1/mikrotik/<int:nas_id>/system/identity/set" in rules


# ─── K8.1: files + backup ────────────────────────────────────────


# ─── L8: parallel overview ───────────────────────────────────────


def test_system_overview_runs_sub_calls_in_parallel(client, monkeypatch):
    """5 sub-fetchers each sleep ~0.4s. Sequential would take ~2s,
    parallel must finish well under 1s. The bound (1s) gives ample
    slack for slow CI but still catches a regression to sequential."""
    import time
    from app.radius.services import mikrotik_admin_client as mac

    def slow_ok(_nas):
        time.sleep(0.4)
        return mac.MtResult(ok=True, data=[{"k": "v"}])

    monkeypatch.setattr(mac, "system_resource", slow_ok)
    monkeypatch.setattr(mac, "system_health", slow_ok)
    monkeypatch.setattr(mac, "system_identity", slow_ok)
    monkeypatch.setattr(mac, "system_clock", slow_ok)
    monkeypatch.setattr(mac, "system_routerboard", slow_ok)

    started = time.perf_counter()
    res = client.get("/api/v1/mikrotik/1/system/overview", headers=AUTH)
    elapsed = time.perf_counter() - started

    assert res.status_code == 200
    body = res.get_json()
    sections = body["data"]["sections"]
    assert set(sections) == {"resource", "health", "identity",
                              "clock", "routerboard"}
    assert all(sections[k]["ok"] for k in sections)
    # 5 × 0.4s sequential = 2.0s. Parallel + threadpool overhead
    # < 1s on any normal box.
    assert elapsed < 1.5, f"overview took {elapsed:.2f}s — likely serial"


def test_files_list_success(client, monkeypatch):
    mc = MagicMock()
    mc.print_.return_value = [
        {".id": "*1", "name": "b1.backup", "type": "backup", "size": "10"},
    ]
    _patch_pool(monkeypatch, mc)

    res = client.get("/api/v1/mikrotik/1/files", headers=AUTH)
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["data"]["ok"] is True
    assert body["data"]["data"][0]["name"] == "b1.backup"


def test_files_list_unknown_router_404(client):
    res = client.get("/api/v1/mikrotik/999/files", headers=AUTH)
    assert res.status_code == 404
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


def test_backup_save_with_explicit_name(client, monkeypatch):
    mc = MagicMock()
    mc.run.return_value = [{"reply": "!done", "attrs": {}}]
    _patch_pool(monkeypatch, mc)

    res = client.post(
        "/api/v1/mikrotik/1/system/backup/save",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"name": "weekly-1"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["data"]["backup_name"] == "weekly-1"
    assert body["data"]["ok"] is True
    args, kwargs = mc.run.call_args
    assert args[0] == "/system/backup/save"
    assert kwargs["attrs"]["name"] == "weekly-1"


def test_backup_save_generates_default_name(client, monkeypatch):
    mc = MagicMock()
    mc.run.return_value = [{"reply": "!done", "attrs": {}}]
    _patch_pool(monkeypatch, mc)

    res = client.post(
        "/api/v1/mikrotik/1/system/backup/save",
        headers={**AUTH, "Content-Type": "application/json"},
        json={},
    )
    assert res.status_code == 200
    body = res.get_json()
    # Default looks like backup-YYYYMMDD-HHMMSS.
    assert body["data"]["backup_name"].startswith("backup-")


def test_backup_save_rejects_unsafe_name(client, monkeypatch):
    # Don't even reach the wire; sanitizer must trip first.
    mc = MagicMock()
    _patch_pool(monkeypatch, mc)

    res = client.post(
        "/api/v1/mikrotik/1/system/backup/save",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"name": "../etc/passwd"},
    )
    assert res.status_code == 200  # envelope-style failure
    body = res.get_json()
    assert body["data"]["ok"] is False
    assert "ممنوعة" in body["data"]["error"]
    mc.run.assert_not_called()


def test_backup_save_rejects_non_object_body(client):
    res = client.post(
        "/api/v1/mikrotik/1/system/backup/save",
        headers={**AUTH, "Content-Type": "application/json"},
        json=["not", "an", "object"],
    )
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"]["code"] == "bad_request"


# ─── K8.1b: file download (honest unsupported) ───────────────────


def test_file_download_returns_501_not_supported(client):
    res = client.get(
        "/api/v1/mikrotik/1/files/b1.backup/download",
        headers=AUTH,
    )
    assert res.status_code == 501
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_supported"
    assert "غير مدعوم" in body["error"]["message"]
    assert body["error"]["details"]["filename"] == "b1.backup"
    assert body["error"]["details"]["router_id"] == 1


def test_file_download_rejects_traversal_segment(client):
    """`..back` passes the Flask `<string:>` converter (no slash)
    so the handler's own sanitizer must reject it. The contract:
    never return 200 with fabricated bytes."""
    res = client.get(
        "/api/v1/mikrotik/1/files/..%2eback/download",
        headers=AUTH,
    )
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"]["code"] == "invalid_filename"


def test_file_download_404_for_unknown_router(client):
    res = client.get(
        "/api/v1/mikrotik/999/files/x.backup/download",
        headers=AUTH,
    )
    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "not_found"


# ─── K8.2: reboot + identity (confirm guard) ─────────────────────


def test_reboot_without_confirm_returns_409(client):
    res = client.post(
        "/api/v1/mikrotik/1/system/reboot",
        headers={**AUTH, "Content-Type": "application/json"},
        json={},
    )
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "confirm_required"


def test_reboot_with_confirm_false_returns_409(client):
    res = client.post(
        "/api/v1/mikrotik/1/system/reboot",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"confirm": False},
    )
    assert res.status_code == 409


def test_reboot_with_confirm_true_calls_router(client, monkeypatch):
    mc = MagicMock()
    mc.run.return_value = [{"reply": "!done", "attrs": {}}]
    _patch_pool(monkeypatch, mc)

    res = client.post(
        "/api/v1/mikrotik/1/system/reboot",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"confirm": True, "reason": "kernel panic"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["data"]["ok"] is True
    args, _ = mc.run.call_args
    assert args[0] == "/system/reboot"


def test_reboot_unknown_router_404(client):
    res = client.post(
        "/api/v1/mikrotik/999/system/reboot",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"confirm": True},
    )
    assert res.status_code == 404


def test_reboot_non_object_body_400(client):
    res = client.post(
        "/api/v1/mikrotik/1/system/reboot",
        headers={**AUTH, "Content-Type": "application/json"},
        json="just-a-string",
    )
    assert res.status_code == 400


def test_identity_set_without_confirm_returns_409(client):
    res = client.post(
        "/api/v1/mikrotik/1/system/identity/set",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"name": "main-gw"},
    )
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "confirm_required"


def test_identity_set_rejects_bad_name_via_envelope(client, monkeypatch):
    mc = MagicMock()
    _patch_pool(monkeypatch, mc)

    res = client.post(
        "/api/v1/mikrotik/1/system/identity/set",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"confirm": True, "name": "bad name with spaces"},
    )
    # Sanitizer trips before the wire call → envelope-style failure
    # (HTTP 200, data.ok = False).
    assert res.status_code == 200
    body = res.get_json()
    assert body["data"]["ok"] is False
    assert "[A-Za-z0-9._-]" in body["data"]["error"]
    mc.run.assert_not_called()


def test_identity_set_success_calls_router(client, monkeypatch):
    mc = MagicMock()
    mc.run.return_value = [{"reply": "!done", "attrs": {}}]
    _patch_pool(monkeypatch, mc)

    res = client.post(
        "/api/v1/mikrotik/1/system/identity/set",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"confirm": True, "name": "main-gw"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["data"]["new_name"] == "main-gw"
    args, kwargs = mc.run.call_args
    assert args[0] == "/system/identity/set"
    assert kwargs["attrs"]["name"] == "main-gw"


