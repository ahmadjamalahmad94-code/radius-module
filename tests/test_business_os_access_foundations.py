from __future__ import annotations

import os
import json

import pytest

from app.radius.db.connection import reset_for_tests
from app.radius.services.business_os_access import (
    BUSINESS_OS_PERMISSIONS,
    PERM_APPROVALS_MANAGE,
    PERM_EVENTS_VIEW,
    PERM_FINANCE_VIEW,
    PERM_WALLET_DEBIT,
    AuditGuard,
    LimitPolicy,
    SafetyGateService,
    ScopeResolver,
)
from app.radius.services.business_os_finance import EventService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "business_os_access.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def test_permission_catalog_contains_required_business_os_keys():
    required = {
        "finance.view",
        "finance.write",
        "wallet.credit",
        "wallet.debit",
        "ledger.view",
        "ledger.correct",
        "subscribers.view",
        "subscribers.write",
        "card_users.view",
        "card_users.write",
        "cards.view",
        "cards.write",
        "managers.view",
        "managers.write",
        "distributors.view",
        "distributors.write",
        "notifications.send",
        "campaigns.send",
        "events.view",
        "reports.view",
        "speed_control.write",
        "approvals.manage",
    }
    assert required.issubset(set(BUSINESS_OS_PERMISSIONS))
    assert len(BUSINESS_OS_PERMISSIONS) == len(set(BUSINESS_OS_PERMISSIONS))
    assert PERM_FINANCE_VIEW in BUSINESS_OS_PERMISSIONS
    assert PERM_EVENTS_VIEW in BUSINESS_OS_PERMISSIONS
    assert PERM_APPROVALS_MANAGE in BUSINESS_OS_PERMISSIONS


def test_scope_resolver_maps_actor_to_allowed_owner_scope():
    resolver = ScopeResolver()
    manager = {
        "actor_type": "manager",
        "manager_id": 17,
        "permissions": [PERM_FINANCE_VIEW],
    }
    resolved = resolver.resolve(manager)
    assert resolved["global_access"] is False
    assert resolved["scopes"] == [{"scope_type": "manager", "scope_id": 17}]
    assert resolver.can_access_owner(manager, "manager", 17)
    assert not resolver.can_access_owner(manager, "distributor", 17)

    admin = {"actor_type": "admin", "is_super_admin": True}
    assert resolver.resolve(admin)["global_access"] is True
    assert resolver.can_access_owner(admin, "subscriber", 999)


def test_limit_policy_flags_limits_and_approval_thresholds():
    policy = LimitPolicy()
    ok = policy.evaluate("wallet.debit", amount="900.00")
    assert ok["allowed"] is True
    assert ok["requires_approval"] is False

    approval = policy.evaluate("wallet.debit", amount="1500.00")
    assert approval["allowed"] is True
    assert approval["requires_approval"] is True
    assert "approval_required" in approval["warnings"]

    blocked = policy.evaluate("wallet.debit", amount="6000.00")
    assert blocked["allowed"] is False
    assert "max_wallet_debit_exceeded" in blocked["violations"]

    free_days = policy.evaluate("subscriber.free_days", days=4)
    assert free_days["allowed"] is False
    assert "max_free_days_exceeded" in free_days["violations"]


def test_safety_gate_checks_permissions_before_limits():
    gate = SafetyGateService()
    missing = gate.check("wallet.debit", permissions=[PERM_FINANCE_VIEW], amount="10.00")
    assert missing.allowed is False
    assert missing.missing_permission == PERM_WALLET_DEBIT

    allowed = gate.check("wallet.debit", permissions=[PERM_WALLET_DEBIT], amount="1500.00")
    assert allowed.allowed is True
    assert allowed.requires_approval is True

    blocked = gate.check("wallet.debit", permissions=[PERM_WALLET_DEBIT], amount="6000.00")
    assert blocked.allowed is False
    assert "max_wallet_debit_exceeded" in blocked.violations


def test_audit_guard_records_business_event(app):
    with app.app_context():
        event = AuditGuard().record(
            tenant_id=1,
            actor_type="admin",
            actor_id=3,
            action="wallet.debit",
            target_type="wallet",
            target_id=22,
            reason="test debit review",
            before={"balance": "100.00"},
            after={"balance": "90.00"},
        )
        assert event["category"] == "system"
        assert event["event_key"] == "business_os.wallet.debit"
        metadata = json.loads(event["metadata_json"])
        assert metadata["reason"] == "test debit review"

        events = EventService().list_events(tenant_id=1, category="system")
        assert events[0]["id"] == event["id"]
