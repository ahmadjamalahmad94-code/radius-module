from __future__ import annotations

import os
import sys
import tempfile

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_system_api_")
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


def test_system_api_requires_auth(client):
    res = client.get("/api/v1/system/status")
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_system_routes_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert {
        "/api/v1/system/status",
        "/api/v1/system/diagnostics",
        "/api/v1/system/sync",
        "/api/v1/system/sync/<int:job_id>/retry",
        "/api/v1/system/sync/<int:job_id>/cancel",
        "/api/v1/system/reconcile",
        "/api/v1/system/license-file",
    }.issubset(routes)


def test_system_status_returns_counts_and_sync_stats(client):
    res = client.get("/api/v1/system/status", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["tenant_id"] == 1
    assert "counts" in data
    assert "sync_queue" in data
    assert "workers" in data
    assert "vps" in data
    assert "system" in data
    assert "network" in data["vps"]


def test_system_status_includes_vps_probe_payload(client, monkeypatch):
    from app.radius.routes import status as status_routes

    monkeypatch.setattr(
        status_routes.system_probe,
        "get_vps_status",
        lambda: {
            "hostname": "vps-1",
            "platform": "Linux-test",
            "process_uptime": "3س 4د",
            "system_uptime": "9ي 1س 0د",
            "cpu_pct": 12.5,
            "memory": {"percent": 44.0, "available_human": "2.0 GB"},
            "disk": {"percent": 55.0, "free_human": "20.0 GB", "path": "/"},
            "load": {"one": 0.2, "five": 0.3, "fifteen": 0.4},
            "network": {
                "ping_host": "8.8.8.8",
                "ping_ok": True,
                "ping_ms": 22.4,
                "dns_host": "google.com",
                "dns_ok": True,
            },
        },
    )

    res = client.get("/api/v1/system/status", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["vps"]["hostname"] == "vps-1"
    assert data["system"]["cpu_pct"] == 12.5
    assert data["system"]["ram_pct"] == 44.0
    assert data["system"]["disk_pct"] == 55.0
    assert data["system"]["network"]["ping_ms"] == 22.4


def test_system_diagnostics_uses_backend_service(client, monkeypatch):
    from app.radius.services import mt_diagnostics

    monkeypatch.setattr(
        mt_diagnostics,
        "diagnose_tenant",
        lambda tenant_id: {
            "summary": {"total": 1, "ok": 1},
            "routers": [{"host": "10.0.0.1", "status": "ok"}],
            "tenant_id": tenant_id,
        },
    )
    res = client.get("/api/v1/system/diagnostics", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["summary"]["ok"] == 1
    assert res.get_json()["data"]["tenant_id"] == 1


def test_system_sync_list_retry_cancel_are_real(client):
    from app.radius.db.repos import sync_queue_repo

    retry_id = sync_queue_repo.enqueue(
        tenant_id=1,
        kind="subscriber.update",
        entity_key="u1",
        payload={"username": "u1"},
    )
    cancel_id = sync_queue_repo.enqueue(
        tenant_id=1,
        kind="card.batch",
        entity_key="b1",
        payload={"batch": "b1"},
    )

    listed = client.get("/api/v1/system/sync", headers=AUTH)
    assert listed.status_code == 200, listed.get_json()
    ids = {item["id"] for item in listed.get_json()["data"]["items"]}
    assert {retry_id, cancel_id}.issubset(ids)

    retried = client.post(f"/api/v1/system/sync/{retry_id}/retry", headers=AUTH)
    assert retried.status_code == 200, retried.get_json()
    assert retried.get_json()["data"]["job"]["status"] == "queued"
    assert retried.get_json()["data"]["job"]["last_error"] == ""

    cancelled = client.post(f"/api/v1/system/sync/{cancel_id}/cancel", headers=AUTH)
    assert cancelled.status_code == 200, cancelled.get_json()
    job = cancelled.get_json()["data"]["job"]
    assert job["status"] == "failed"
    assert job["last_error"] == "canceled by admin"


def test_system_reconcile_returns_structured_result(client, monkeypatch):
    from app.workers import mt_reconciler

    monkeypatch.setattr(
        mt_reconciler,
        "reconcile_once",
        lambda: {"routers": 0, "closed": 0, "dry": False},
    )
    res = client.post("/api/v1/system/reconcile", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["stats"]["closed"] == 0


def test_system_license_file_requires_auth(client):
    res = client.get("/api/v1/system/license-file")

    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_system_license_file_returns_safe_bridge_state(client):
    from app.radius.db.repos import tenants_repo
    from app.radius.services.admin_panel_client import (
        SNAPSHOT_CAPACITY,
        SNAPSHOT_LICENSE,
        LicenseAdminSnapshotStore,
    )

    tenants_repo.set_setting(1, "license_admin_bridge.enabled", "1")
    tenants_repo.set_setting(1, "license_admin_bridge.base_url", "https://hoberadius.com")
    tenants_repo.set_setting(1, "license_admin_bridge.license_key", "HBR-SECRET-LICENSE-123456")
    tenants_repo.set_setting(1, "license_admin_bridge.shared_secret", "super-secret-link")
    tenants_repo.set_setting(1, "license_admin_bridge.runtime_contract_sync", "1")
    tenants_repo.set_setting(1, "license_admin_bridge.identity_sync_enabled", "1")
    tenants_repo.set_setting(1, "license_admin_bridge.sync_interval_seconds", "180")

    store = LicenseAdminSnapshotStore()
    store.save(
        tenant_id=1,
        snapshot_type=SNAPSHOT_LICENSE,
        normalized_status="active",
        source_url="https://hoberadius.com/api/license/check",
        payload={"status": "active", "license_key": "HBR-SECRET-LICENSE-123456"},
    )
    store.save(
        tenant_id=1,
        snapshot_type=SNAPSHOT_CAPACITY,
        normalized_status="active",
        source_url="https://hoberadius.com/api/integration/hoberadius/runtime-contract",
        payload={
            "contract": {
                "license": {"active": True, "status": "active"},
                "services": {
                    "ip_change_vpn": {
                        "enabled": True,
                        "status": "active",
                        "download_mbps": 50,
                        "shared_secret": "nested-secret",
                    }
                },
                "limits": {"subscribers": {"max_total": 100}},
                "features": {"cards": {"state": "enabled"}},
            }
        },
    )

    res = client.get("/api/v1/system/license-file", headers=AUTH)

    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["config"]["enabled"] is True
    assert data["config"]["base_url"] == "https://hoberadius.com"
    assert data["config"]["license_key_configured"] is True
    assert data["config"]["shared_secret_configured"] is True
    assert data["config"]["sync_interval_seconds"] == "180"
    assert data["snapshots"]["license"]["status"] == "active"
    assert data["snapshots"]["runtime_contract"]["status"] == "active"
    assert data["contract"]["services"]["ip_change_vpn"]["download_mbps"] == 50
    raw = str(res.get_json())
    assert "HBR-SECRET-LICENSE-123456" not in raw
    assert "super-secret-link" not in raw
    assert "nested-secret" not in raw
