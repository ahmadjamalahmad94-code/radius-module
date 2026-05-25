from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.business_os_finance import (
    BusinessOSValidationError,
    EventService,
    LedgerService,
    PricingSnapshotService,
    WalletService,
)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "business_os.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "business-os-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def test_business_os_migration_creates_core_tables(app):
    required = {
        "wallets",
        "wallet_transactions",
        "ledger_entries",
        "price_snapshots",
        "business_events",
        "revenue_records",
        "profit_shares",
        "archive_snapshots",
        "approval_requests",
    }
    with app.app_context():
        rows = db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert required.issubset({row["name"] for row in rows})


def test_wallet_can_be_created_credited_and_debited(app):
    with app.app_context():
        service = WalletService()
        wallet = service.create_wallet(
            tenant_id=1,
            owner_type="manager",
            owner_id=7,
            metadata={"source": "test"},
        )
        assert wallet["balance"] == "0.00"

        credited = service.credit(
            tenant_id=1,
            wallet_id=wallet["id"],
            amount="12.345",
            actor_type="admin",
            actor_id=1,
            reference_type="manual",
            reference_id=101,
        )
        assert credited["wallet"]["balance"] == "12.35"
        assert credited["transaction"]["before_balance"] == "0.00"
        assert credited["transaction"]["after_balance"] == "12.35"
        assert credited["ledger_entry"]["entry_type"] == "wallet_recharge"

        debited = service.debit(
            tenant_id=1,
            wallet_id=wallet["id"],
            amount="2.35",
            actor_type="admin",
            actor_id=1,
            reference_type="manual",
            reference_id=102,
        )
        assert debited["wallet"]["balance"] == "10.00"

        events = EventService().list_events(tenant_id=1, category="financial")
        keys = {event["event_key"] for event in events}
        assert {"wallet.created", "wallet.credit", "wallet.debit"}.issubset(keys)


def test_wallet_rejects_invalid_amounts_and_negative_balance(app):
    with app.app_context():
        service = WalletService()
        wallet = service.create_wallet(tenant_id=1, owner_type="company")
        with pytest.raises(BusinessOSValidationError):
            service.credit(tenant_id=1, wallet_id=wallet["id"], amount="0")
        with pytest.raises(BusinessOSValidationError):
            service.debit(tenant_id=1, wallet_id=wallet["id"], amount="1.00")


def test_ledger_entry_can_be_written_immutably(app):
    with app.app_context():
        entry = LedgerService().write_entry(
            tenant_id=1,
            entry_type="payment",
            debit_account="cash",
            credit_account="revenue",
            amount="25.00",
            actor_type="admin",
            actor_id=1,
            target_type="subscriber",
            target_id=22,
            reference_type="payment",
            reference_id=33,
            metadata={"channel": "cash"},
        )
        assert entry["amount"] == "25.00"
        assert entry["voided_at"] is None

        row = db().execute(
            "SELECT COUNT(*) AS count FROM ledger_entries WHERE reversal_of=?",
            (entry["id"],),
        ).fetchone()
        assert row["count"] == 0


def test_event_can_be_recorded_and_filtered(app):
    with app.app_context():
        event = EventService().record_event(
            tenant_id=1,
            category="security",
            severity="warning",
            event_key="login.failed",
            message="Failed login",
            actor_type="admin",
            actor_id=9,
            target_type="system",
        )
        assert event["event_key"] == "login.failed"
        filtered = EventService().list_events(
            tenant_id=1,
            category="security",
            severity="warning",
        )
        assert filtered[0]["id"] == event["id"]


def test_price_snapshot_can_be_captured(app):
    with app.app_context():
        snapshot = PricingSnapshotService().capture_snapshot(
            tenant_id=1,
            reference_type="card_batch",
            reference_id=44,
            package_id=3,
            retail_price="100",
            wholesale_price="65.555",
            effective_price="90",
            discount_amount="10",
            captured_by_type="manager",
            captured_by_id=7,
        )
        assert snapshot["retail_price"] == "100.00"
        assert snapshot["wholesale_price"] == "65.56"
        assert snapshot["effective_price"] == "90.00"
        assert snapshot["discount_amount"] == "10.00"

        events = EventService().list_events(tenant_id=1, category="financial")
        assert any(event["event_key"] == "price_snapshot.captured" for event in events)
