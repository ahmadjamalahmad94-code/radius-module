from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests


class MockTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {"ok": True}
        self.exc = exc
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "usage.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app


def _insert_seed_usage() -> None:
    db().execute(
        """
        INSERT INTO nas_devices (tenant_id, name, address, created_at)
        VALUES (1, 'router-1', '10.0.0.1', '2026-05-01T00:00:00Z')
        """
    )
    db().execute(
        """
        INSERT INTO access_plans (tenant_id, name, created_at)
        VALUES (1, 'Plan A', '2026-05-01T00:00:00Z')
        """
    )
    plan_id = db().execute("SELECT id FROM access_plans LIMIT 1").fetchone()["id"]
    db().execute(
        """
        INSERT INTO subscribers (tenant_id, username, status, plan_id, created_at)
        VALUES (1, 'sub-1', 'enabled', ?, '2026-05-01T00:00:00Z')
        """,
        (plan_id,),
    )
    db().execute(
        """
        INSERT INTO card_batches (tenant_id, batch_code, plan_id, created_at)
        VALUES (1, 'B1', ?, '2026-05-01T00:00:00Z')
        """,
        (plan_id,),
    )
    batch_id = db().execute("SELECT id FROM card_batches LIMIT 1").fetchone()["id"]
    db().execute(
        """
        INSERT INTO cards (tenant_id, batch_id, username, password, plan_id, created_at)
        VALUES (1, ?, 'card-1', '1234', ?, ?)
        """,
        (batch_id, plan_id, "2026-05-10T00:00:00Z"),
    )
    db().execute(
        """
        INSERT INTO radacct (tenant_id, acctsessionid, username, nasipaddress, acctstarttime)
        VALUES (1, 's1', 'sub-1', '10.0.0.1', '2026-05-10T00:00:00Z')
        """
    )


def test_metrics_calculation_with_seeded_data(app_db, monkeypatch):
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "lic_test_123456789")
    _insert_seed_usage()
    payload = UsageMeteringService().build_payload(tenant_id=1, report_window="2026-05")

    metrics = payload["metrics"]
    assert metrics["subscribers_total"] == 1
    assert metrics["subscribers_active"] == 1
    assert metrics["cards_generated_total"] == 1
    assert metrics["cards_generated_month"] >= 0
    assert metrics["active_cards"] == 1
    assert metrics["card_batches"] == 1
    assert metrics["nas_count"] == 1
    assert metrics["profiles_plans_count"] == 1
    assert metrics["current_online_sessions"] == 1
    assert payload["license_key"] == "lic_...6789"


def test_empty_database_gives_zeros_not_errors(app_db):
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    metrics = UsageMeteringService().collect_metrics(tenant_id=1)

    assert metrics["subscribers_total"] == 0
    assert metrics["cards_generated_total"] == 0
    assert metrics["nas_count"] == 0
    assert metrics["current_online_sessions"] == 0
    assert metrics["db_storage_bytes"] >= 0


def test_failed_remote_report_is_recorded_but_app_continues(app_db):
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    config = AdminBridgeConfig(
        enabled=True,
        base_url="https://admin.example.test",
        license_key="lic_test_123456789",
        shared_secret="",
        timeout_seconds=1,
        retry_count=0,
    )
    result = UsageMeteringService(
        config=config,
        transport=MockTransport(exc=TimeoutError("slow")),
    ).send_usage_report(tenant_id=1, report_window="2026-05", dry_run=False)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["attempt"]["status"] == "failed"
    assert result["attempt"]["error_json"]["code"] == "usage_report_failed"


def test_idempotency_key_stable_for_same_report_window(app_db):
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    service = UsageMeteringService()
    first = service.build_payload(tenant_id=1, report_window="2026-05")
    second = service.build_payload(tenant_id=1, report_window="2026-05")

    assert first["idempotency_key"] == second["idempotency_key"]


def test_invalid_config_disables_sending_safely(app_db):
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    result = UsageMeteringService().send_usage_report(
        tenant_id=1,
        report_window="2026-05",
        dry_run=False,
    )

    assert result["ok"] is False
    assert result["status"] == "disabled"
    assert result["attempt"]["status"] == "disabled"


def test_manual_usage_report_route_defaults_to_dry_run(app_db):
    client = app_db.test_client()
    res = client.post(
        "/api/v1/system/admin-bridge/usage-report",
        json={"report_window": "2026-05"},
        headers={"Authorization": "Bearer dev-token-please-change"},
    )

    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["dry_run"] is True
    assert data["payload"]["report_window"] == "2026-05"
