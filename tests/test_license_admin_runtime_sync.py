from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


class MockTransport:
    def __init__(self, response=None):
        self.response = response or {}
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "runtime_sync.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app
    reset_for_tests(None)


@pytest.fixture()
def client(app_db):
    return app_db.test_client()


def _config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    return AdminBridgeConfig(
        enabled=True,
        base_url="http://178.105.180.6",
        license_key="lic_live_test_123456789",
        timeout_seconds=1.0,
        retry_count=0,
    )


def test_license_sync_derives_capacity_and_vpn_service(app_db):
    from app.radius.services.admin_panel_client import (
        SNAPSHOT_CAPACITY,
        SNAPSHOT_LICENSE,
        AdminPanelClient,
        LicenseAdminSnapshotStore,
    )
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

    transport = MockTransport(
        {
            "status": "active",
            "active": True,
            "mode": "production",
            "plan": {"code": "business", "max_users": 2, "max_nas": 1},
            "features": {"cards": False},
            "services": {
                "ip_change_vpn": {
                    "enabled": True,
                    "status": "active",
                    "plan_code": "vpn_50m",
                    "download_mbps": 50,
                    "upload_mbps": 50,
                    "max_vpn_users": 100,
                    "max_locations": 1,
                }
            },
            "stale_after_seconds": 600,
        }
    )
    store = LicenseAdminSnapshotStore()
    admin_client = AdminPanelClient(config=_config(), transport=transport, store=store)

    result = LicenseAdminRuntimeSyncService(
        config=_config(),
        admin_client=admin_client,
        store=store,
    ).sync_once(tenant_id=1)

    assert result["ok"] is True
    assert result["status"] == "active"
    assert result["license_active"] is True
    assert result["limits"]["subscribers"]["max_total"] == 2
    assert result["services"]["ip_change_vpn"]["download_mbps"] == 50
    assert transport.calls[0]["url"] == "http://178.105.180.6/api/license/check"

    license_snapshot = store.latest(tenant_id=1, snapshot_type=SNAPSHOT_LICENSE)
    capacity_snapshot = store.latest(tenant_id=1, snapshot_type=SNAPSHOT_CAPACITY)
    assert license_snapshot["normalized_status"] == "active"
    assert capacity_snapshot["normalized_status"] == "active"
    contract = capacity_snapshot["payload_json"]["contract"]
    assert contract["limits"]["subscribers"]["max_total"] == 2
    assert contract["features"]["cards"]["state"] == "locked"
    assert contract["services"]["ip_change_vpn"]["runtime_hint"] == "wireguard_tc_or_chr_queue"


def test_expired_license_derives_disabled_vpn_service(app_db):
    from app.radius.services.license_admin_runtime_sync import derive_capacity_contract_from_license_payload

    derived = derive_capacity_contract_from_license_payload(
        {
            "status": "expired",
            "active": False,
            "services": {
                "ip_change_vpn": {
                    "enabled": True,
                    "status": "active",
                    "download_mbps": 100,
                    "upload_mbps": 100,
                }
            },
        }
    )

    vpn = derived["services"]["ip_change_vpn"]
    assert vpn["enabled"] is False
    assert vpn["status"] == "expired"
    assert derived["contract"]["license"]["active"] is False


def test_capacity_status_uses_derived_disabled_vpn_for_expired_license(app_db):
    from app.radius.services.admin_panel_client import AdminPanelClient, LicenseAdminSnapshotStore
    from app.radius.services.license_admin_capacity import CapacityEnforcementService
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

    transport = MockTransport(
        {
            "status": "expired",
            "active": False,
            "services": {
                "ip_change_vpn": {
                    "enabled": True,
                    "status": "active",
                    "download_mbps": 100,
                    "upload_mbps": 100,
                }
            },
        }
    )
    store = LicenseAdminSnapshotStore()
    admin_client = AdminPanelClient(config=_config(), transport=transport, store=store)
    LicenseAdminRuntimeSyncService(
        config=_config(),
        admin_client=admin_client,
        store=store,
    ).sync_once(tenant_id=1)

    status = CapacityEnforcementService(store=store).capacity_status(tenant_id=1)

    assert status["status"] == "blocked"
    assert status["services"]["ip_change_vpn"]["enabled"] is False
    assert status["services"]["ip_change_vpn"]["status"] == "expired"


def test_license_sync_endpoint_is_json_and_non_mutating_when_disabled(client):
    res = client.post(
        "/api/v1/system/admin-bridge/license-sync",
        json={},
        headers=AUTH,
    )

    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["data"]["ok"] is False
    assert body["data"]["status"] == "disabled"
