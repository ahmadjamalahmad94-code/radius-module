from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


class MockTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {"ok": True, "status": "ok", "items": []}
        self.exc = exc
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


class CountingDryRunAdapter:
    service_key = "network"
    action_key = "network.public_ip_change"
    dry_run_supported = True

    def __init__(self):
        self.calls = 0

    def execute(self, *, job, dry_run):
        self.calls += 1
        return {
            "status": "planned",
            "dry_run": dry_run,
            "planned_actions": [
                {
                    "type": "public_ip_change",
                    "target": job.get("payload", {}).get("target_router", "unknown"),
                }
            ],
        }


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "service_activation.db"))
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


def _service(response, *, registry=None):
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_admin_service_activation import ServiceActivationService

    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="",
        timeout_seconds=1,
        retry_count=0,
    )
    transport = MockTransport(response=response)
    return ServiceActivationService(
        config=config,
        admin_client=AdminPanelClient(config=config, transport=transport),
        registry=registry,
    ), transport


def _registry(adapter):
    from app.radius.services.license_admin_service_activation import ServiceActivationAdapterRegistry

    registry = ServiceActivationAdapterRegistry()
    registry.register(adapter)
    return registry


def _job(reference="activation-1"):
    return {
        "reference": reference,
        "service_key": "network",
        "action_key": "network.public_ip_change",
        "payload": {
            "target_router": "lab-router-1",
            "requested_public_ip": "203.0.113.10",
        },
    }


def test_poll_receives_no_jobs(app_db):
    service, _transport = _service({"ok": True, "status": "ok", "items": []})

    result = service.poll_once(tenant_id=1)

    assert result["ok"] is True
    assert result["count"] == 0


def test_unsupported_job_reports_unsupported_service(app_db):
    service, _transport = _service({"ok": True, "status": "ok", "items": [_job()]})

    result = service.poll_once(tenant_id=1)

    execution = result["recorded"][0]
    assert execution["status"] == "unsupported_service"
    assert execution["error_json"]["code"] == "unsupported_service"


def test_duplicate_job_does_not_double_execute(app_db):
    adapter = CountingDryRunAdapter()
    service, _transport = _service(
        {"ok": True, "status": "ok", "items": [_job()]},
        registry=_registry(adapter),
    )

    first = service.poll_once(tenant_id=1)["recorded"][0]
    second = service.poll_once(tenant_id=1)["recorded"][0]

    assert first["id"] == second["id"]
    assert adapter.calls == 1


def test_dry_run_adapter_records_planned_actions(app_db):
    adapter = CountingDryRunAdapter()
    service, _transport = _service(
        {"ok": True, "status": "ok", "items": [_job()]},
        registry=_registry(adapter),
    )

    execution = service.poll_once(tenant_id=1, dry_run=True)["recorded"][0]

    assert execution["status"] == "planned"
    assert execution["dry_run"] is True
    assert execution["adapter_key"] == "network:network.public_ip_change"
    assert execution["result_json"]["planned_actions"][0]["type"] == "public_ip_change"


def test_status_callback_success_mocked(app_db):
    service, transport = _service({"ok": True, "status": "ok"})
    service.record_or_execute_job(tenant_id=1, job=_job(), dry_run=True)

    result = service.send_status_callback(tenant_id=1, reference="activation-1")

    assert result["ok"] is True
    assert transport.calls[0]["url"].endswith(
        "/api/integration/hoberadius/service-activations/activation-1/status"
    )


def test_poll_route_defaults_to_dry_run_json(client):
    res = client.post(
        "/api/v1/system/admin-bridge/service-activations/poll",
        json={},
        headers=AUTH,
    )

    assert res.status_code == 200
    assert res.content_type.startswith("application/json")
    assert res.get_json()["data"]["status"] in {"disabled", "config_missing"}
