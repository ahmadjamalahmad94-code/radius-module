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


# ─── K8.1: files + backup ────────────────────────────────────────


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


