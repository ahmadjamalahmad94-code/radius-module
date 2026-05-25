from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.db.helpers import now_iso
from app.radius.services.business_os_finance import EventService, WalletService
from app.radius.services.events_risk_center import EventsRiskCenterService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "events_risk_center.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "risk_admin"
        sess["admin_name"] = "Risk Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "risk-csrf"


def test_event_filtering_by_category_severity_actor_and_target(app):
    with app.app_context():
        event_service = EventService()
        event_service.record_event(
            tenant_id=1,
            category="security",
            severity="warning",
            event_key="login.failed",
            actor_type="subscriber",
            actor_id=10,
            target_type="subscriber",
            target_id=10,
            correlation_id="corr-1",
        )
        event_service.record_event(
            tenant_id=1,
            category="financial",
            severity="info",
            event_key="wallet.credit",
            target_type="wallet",
            target_id=99,
        )
        service = EventsRiskCenterService(tenant_id=1)
        filtered = service.list_events(
            category="security",
            severity="warning",
            actor_type="subscriber",
            actor_id=10,
            target_type="subscriber",
            target_id=10,
            correlation_id="corr-1",
        )

    assert len(filtered) == 1
    assert filtered[0]["event_key"] == "login.failed"


def test_entity_timeline_returns_target_events(app):
    with app.app_context():
        event_service = EventService()
        event_service.record_event(
            tenant_id=1,
            category="subscriber",
            severity="info",
            event_key="subscriber.created",
            target_type="subscriber",
            target_id=42,
        )
        event_service.record_event(
            tenant_id=1,
            category="subscriber",
            severity="warning",
            event_key="subscriber.loan",
            target_type="subscriber",
            target_id=42,
        )
        timeline = EventsRiskCenterService(tenant_id=1).entity_timeline(
            entity_type="subscriber",
            entity_id=42,
        )

    assert [item["event_key"] for item in timeline] == ["subscriber.loan", "subscriber.created"]


def test_risk_rules_detect_negative_wallet_and_failed_logins(app):
    with app.app_context():
        wallet = WalletService().create_wallet(tenant_id=1, owner_type="manager", owner_id=1)
        db().execute("UPDATE wallets SET balance_minor=-500 WHERE id=?", (wallet["id"],))
        event_service = EventService()
        for _ in range(3):
            event_service.record_event(
                tenant_id=1,
                category="security",
                severity="warning",
                event_key="login.failed",
                actor_type="subscriber",
                actor_id=5,
                target_type="subscriber",
                target_id=5,
            )
        result = EventsRiskCenterService(tenant_id=1).run_risk_rules()
        keys = {flag["flag_key"] for flag in result["flags"]}

    assert "wallet_negative" in keys
    assert "repeated_failed_logins" in keys
    assert result["flags_created"] >= 2


def test_fraud_flag_creation_records_evidence(app):
    with app.app_context():
        flag = EventsRiskCenterService(tenant_id=1).create_fraud_flag(
            flag_key="suspicious_wallet_debit",
            severity="error",
            entity_type="wallet",
            entity_id=7,
            risk_score=82,
            summary="Large manual debit",
            evidence={"amount_minor": 50000},
            actor="qa",
        )

    assert flag["status"] == "open"
    assert flag["risk_score"] == 82
    assert flag["evidence"]["amount_minor"] == 50000


def test_revenue_ledger_mismatch_rule_creates_flag(app):
    with app.app_context():
        db().execute(
            """
            INSERT INTO revenue_records(
                tenant_id, source_type, source_id, original_price_minor,
                retail_price_minor, wholesale_cost_minor, collected_amount_minor,
                net_profit_minor, company_share_minor, currency, status,
                metadata_json, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (1, "manual_sale", 123, 1000, 1000, 0, 1000, 1000, 1000, "JOD", "posted", "{}", now_iso()),
        )
        result = EventsRiskCenterService(tenant_id=1).run_risk_rules()
        keys = {flag["flag_key"] for flag in result["flags"]}

    assert "revenue_ledger_mismatch" in keys


def test_events_routes_render_and_do_not_support_delete(app):
    with app.app_context():
        EventService().record_event(
            tenant_id=1,
            category="security",
            severity="critical",
            event_key="permission.denied",
            target_type="manager",
            target_id=1,
            message="Denied",
        )
    with app.test_client() as client:
        _auth_session(client)
        index = client.get("/admin/radius/events")
        risk = client.get("/admin/radius/events/risk")
        security = client.get("/admin/radius/events/security")
        investigations = client.get("/admin/radius/events/investigations")
        delete_attempt = client.delete("/admin/radius/events/1")

    assert index.status_code == 200
    assert "events-table" in index.get_data(as_text=True)
    assert risk.status_code == 200
    assert "fraud-flags-table" in risk.get_data(as_text=True)
    assert security.status_code == 200
    assert "security-events-table" in security.get_data(as_text=True)
    assert investigations.status_code == 200
    assert "investigations-table" in investigations.get_data(as_text=True)
    assert delete_attempt.status_code >= 400
