from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "bridge_events.db"))
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


def test_events_recorded_locally(app_db):
    from app.radius.services.license_admin_bridge_events import BridgeEventService

    event = BridgeEventService().record(
        tenant_id=1,
        event_type="heartbeat.sent",
        reference="heartbeat-1",
        payload={"license_key": "lic_secret_123456"},
    )

    assert event["event_type"] == "heartbeat.sent"
    assert event["label_ar"] == "تم إرسال نبض الحالة"
    assert "lic_secret_123456" not in str(event["payload_json"])


def test_duplicate_event_idempotency(app_db):
    from app.radius.services.license_admin_bridge_events import BridgeEventService

    service = BridgeEventService()
    first = service.record(
        tenant_id=1,
        event_type="usage.report_sent",
        event_key="usage:2026-05-25",
        payload={"count": 1},
    )
    second = service.record(
        tenant_id=1,
        event_type="usage.report_sent",
        event_key="usage:2026-05-25",
        payload={"count": 2},
    )

    assert first["id"] == second["id"]
    assert second["payload_json"]["count"] == 1


def test_missing_admin_event_endpoint_does_not_fail(app_db):
    from app.radius.services.license_admin_bridge_events import BridgeEventService

    status = BridgeEventService().admin_callback_status()

    assert status["ok"] is False
    assert status["code"] == "admin_event_endpoint_missing"


def test_event_summary_generated(app_db):
    from app.radius.services.license_admin_bridge_events import BridgeEventService

    service = BridgeEventService()
    service.record(tenant_id=1, event_type="backup.upload_failed", severity="error")
    service.record(tenant_id=1, event_type="heartbeat.sent", severity="info")

    summary = service.summary(tenant_id=1)

    assert summary["total"] == 2
    assert summary["by_type"]["backup.upload_failed"] == 1
    assert summary["by_severity"]["error"] == 1


def test_events_route_returns_json(client, app_db):
    from app.radius.services.license_admin_bridge_events import BridgeEventService

    BridgeEventService().record(tenant_id=1, event_type="restore.request_received")

    res = client.get("/api/v1/system/admin-bridge/events", headers=AUTH)

    assert res.status_code == 200
    body = res.get_json()["data"]
    assert body["items"][0]["event_type"] == "restore.request_received"
    assert body["admin_callback"]["code"] == "admin_event_endpoint_missing"


def test_service_activation_records_event(app_db):
    from app.radius.services.license_admin_service_activation import ServiceActivationService

    job = {
        "reference": "event-activation-1",
        "service_key": "unknown",
        "action_key": "unknown.action",
        "payload": {},
    }
    ServiceActivationService().record_or_execute_job(tenant_id=1, job=job)

    res = ServiceActivationService().get_by_reference(tenant_id=1, reference="event-activation-1")
    assert res["status"] == "unsupported_service"

    from app.radius.services.license_admin_bridge_events import BridgeEventService

    events = BridgeEventService().list_events(tenant_id=1)
    assert events[0]["event_type"] == "service_activation.received"
