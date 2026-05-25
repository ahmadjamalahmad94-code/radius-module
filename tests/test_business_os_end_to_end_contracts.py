from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.business_os_finance import EventService
from app.radius.services.operations_speed_center import OperationsSpeedCenterService


AUTH = {"Authorization": "Bearer business-os-api-token"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "business_os_end_to_end.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "business-os-api-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "business_os_reviewer"
        sess["admin_name"] = "Business OS Reviewer"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "business-os-review-csrf"


def test_business_os_ui_routes_render_without_missing_templates_or_route_drift(app):
    with app.app_context():
        event = EventService().record_event(
            tenant_id=1,
            category="security",
            severity="warning",
            event_key="review.route_smoke",
            message="Route smoke review event",
        )

    routes = [
        "/admin/radius/dashboard",
        "/admin/radius/finance",
        "/admin/radius/finance/wallets",
        "/admin/radius/finance/ledger",
        "/admin/radius/finance/revenue",
        "/admin/radius/finance/debts",
        "/admin/radius/finance/loans",
        "/admin/radius/card-users",
        "/admin/radius/card-marketplace",
        "/admin/radius/card-pricing",
        "/admin/radius/business-operators",
        "/admin/radius/communications",
        "/admin/radius/communications/templates",
        "/admin/radius/communications/send",
        "/admin/radius/communications/campaigns",
        "/admin/radius/communications/deliveries",
        "/admin/radius/communications/audience",
        "/admin/radius/events",
        f"/admin/radius/events/{event['id']}",
        "/admin/radius/events/risk",
        "/admin/radius/events/security",
        "/admin/radius/events/investigations",
        "/admin/radius/operations",
        "/admin/radius/operations/speed-control",
        "/admin/radius/reports",
        "/admin/radius/reports/financial",
        "/admin/radius/reports/cards",
        "/admin/radius/reports/distributors",
        "/admin/radius/reports/archive",
    ]
    with app.test_client() as client:
        _auth_session(client)
        failures = []
        for route in routes:
            response = client.get(route)
            if response.status_code != 200:
                failures.append((route, response.status_code))

    assert failures == []


def test_business_os_public_portal_login_routes_render_without_admin_navigation(app):
    with app.test_client() as client:
        subscriber = client.get("/admin/radius/portal/subscriber/login")
        card = client.get("/admin/radius/portal/card/login")

    assert subscriber.status_code == 200
    assert card.status_code == 200
    assert "admin-nav" not in subscriber.get_data(as_text=True).lower()
    assert "admin-nav" not in card.get_data(as_text=True).lower()


def test_business_os_json_contracts_have_stable_wrappers(app):
    routes = [
        "/api/v1/finance/wallets",
        "/api/v1/finance/ledger",
        "/api/v1/finance/revenue",
        "/api/v1/events",
        "/api/v1/pricing/snapshots",
        "/api/v1/business/summary",
    ]
    with app.test_client() as client:
        for route in routes:
            response = client.get(route, headers=AUTH)
            payload = response.get_json()
            assert response.status_code == 200
            assert payload["ok"] is True
            assert "data" in payload
            assert "error" not in payload

        _auth_session(client)
        summary = client.get("/admin/radius/reports/summary.json")
        summary_payload = summary.get_json()
        assert summary.status_code == 200
        assert summary_payload["status"] == "ok"
        assert set(summary_payload["summary"]) >= {"finance", "subscribers", "cards", "drilldowns"}


def test_business_os_speed_control_remains_dry_run_only(app):
    with app.app_context():
        policy = OperationsSpeedCenterService().save_speed_policy(
            policy_key="review-smoke",
            title="Review smoke",
            preset="slow_1m",
            multiplier=0.5,
            actor="review",
        )

    assert policy["applied_to_radius"] is False
    assert policy["status"] == "dry_run_ready"
    assert policy["preview"]["applied_to_radius"] is False
