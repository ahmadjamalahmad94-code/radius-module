"""Business OS API contracts for finance, events, pricing, and summary."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

from ...radius.core.system_config import default_currency
from ...radius.core.tenant import DEFAULT_TENANT_ID
from ...radius.db.connection import db
from ...radius.db.helpers import json_load
from ...radius.services.business_os_finance import (
    BusinessOSValidationError,
    EventService,
    LedgerService,
    PricingSnapshotService,
    WalletService,
    minor_to_money,
)
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    routes = [
        ("/finance/wallets", "business_wallets_list", wallets_list, ["GET"]),
        ("/finance/wallets", "business_wallets_create", wallets_create, ["POST"]),
        ("/finance/wallets/<int:wallet_id>", "business_wallets_detail", wallets_detail, ["GET"]),
        ("/finance/wallets/<int:wallet_id>/credit", "business_wallets_credit", wallets_credit, ["POST"]),
        ("/finance/wallets/<int:wallet_id>/debit", "business_wallets_debit", wallets_debit, ["POST"]),
        ("/finance/wallets/<int:wallet_id>/transactions", "business_wallet_transactions", wallet_transactions, ["GET"]),
        ("/finance/ledger", "business_ledger_list", ledger_list, ["GET"]),
        ("/finance/ledger/corrections", "business_ledger_correction", ledger_correction, ["POST"]),
        ("/finance/revenue", "business_revenue_list", revenue_list, ["GET"]),
        ("/events", "business_events_list", events_list, ["GET"]),
        ("/events", "business_events_record", events_record, ["POST"]),
        ("/pricing/snapshots", "business_price_snapshots_list", price_snapshots_list, ["GET"]),
        ("/pricing/snapshots", "business_price_snapshots_capture", price_snapshots_capture, ["POST"]),
        ("/business/summary", "business_summary", business_summary, ["GET"]),
    ]
    for rule, endpoint, view, methods in routes:
        bp.add_url_rule(rule, endpoint, require_api_token(view), methods=methods)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> tuple[str, int | None]:
    admin_id = getattr(g, "admin_id", None)
    if admin_id:
        return "admin", int(admin_id)
    token_id = getattr(g, "api_token_id", None)
    if token_id:
        return "api_token", int(token_id)
    return "api_token", None


def _payload() -> dict[str, Any]:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _limit(default: int = 100, maximum: int = 500) -> int:
    try:
        return min(max(int(request.args.get("limit") or default), 1), maximum)
    except (TypeError, ValueError):
        return default


def _validation_error(exc: Exception):
    return fail("validation_error", str(exc), status=422)


def wallets_list():
    items = WalletService().list_wallets(
        tenant_id=_tid(),
        owner_type=(request.args.get("owner_type") or "").strip(),
        status=(request.args.get("status") or "").strip(),
        limit=_limit(),
    )
    return ok({"items": items, "count": len(items)})


def wallets_create():
    data = _payload()
    try:
        wallet = WalletService().create_wallet(
            tenant_id=_tid(),
            owner_type=str(data.get("owner_type") or ""),
            owner_id=data.get("owner_id"),
            currency=str(data.get("currency") or default_currency()),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )
    except BusinessOSValidationError as exc:
        return _validation_error(exc)
    return ok({"wallet": wallet}, status=201)


def wallets_detail(wallet_id: int):
    wallet = WalletService().get_wallet(tenant_id=_tid(), wallet_id=wallet_id)
    if not wallet:
        return fail("not_found", "wallet not found", status=404)
    return ok({"wallet": wallet})


def wallets_credit(wallet_id: int):
    data = _payload()
    actor_type, actor_id = _actor()
    try:
        result = WalletService().credit(
            tenant_id=_tid(),
            wallet_id=wallet_id,
            amount=data.get("amount"),
            actor_type=actor_type,
            actor_id=actor_id,
            reference_type=str(data.get("reference_type") or ""),
            reference_id=data.get("reference_id"),
            notes=str(data.get("notes") or "")[:500],
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )
    except BusinessOSValidationError as exc:
        return _validation_error(exc)
    return ok(result, status=201)


def wallets_debit(wallet_id: int):
    data = _payload()
    actor_type, actor_id = _actor()
    try:
        result = WalletService().debit(
            tenant_id=_tid(),
            wallet_id=wallet_id,
            amount=data.get("amount"),
            actor_type=actor_type,
            actor_id=actor_id,
            reference_type=str(data.get("reference_type") or ""),
            reference_id=data.get("reference_id"),
            notes=str(data.get("notes") or "")[:500],
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )
    except BusinessOSValidationError as exc:
        return _validation_error(exc)
    return ok(result, status=201)


def wallet_transactions(wallet_id: int):
    items = WalletService().list_transactions(
        tenant_id=_tid(),
        wallet_id=wallet_id,
        limit=_limit(),
    )
    return ok({"items": items, "count": len(items)})


def ledger_list():
    items = LedgerService().list_entries(
        tenant_id=_tid(),
        entry_type=(request.args.get("entry_type") or "").strip(),
        reference_type=(request.args.get("reference_type") or "").strip(),
        limit=_limit(),
    )
    return ok({"items": items, "count": len(items)})


def ledger_correction():
    data = _payload()
    actor_type, actor_id = _actor()
    try:
        entry = LedgerService().write_entry(
            tenant_id=_tid(),
            entry_type="correction",
            debit_account=str(data.get("debit_account") or ""),
            credit_account=str(data.get("credit_account") or ""),
            amount=data.get("amount"),
            currency=str(data.get("currency") or default_currency()),
            actor_type=actor_type,
            actor_id=actor_id,
            target_type=str(data.get("target_type") or ""),
            target_id=data.get("target_id"),
            reference_type=str(data.get("reference_type") or ""),
            reference_id=data.get("reference_id"),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )
    except BusinessOSValidationError as exc:
        return _validation_error(exc)
    return ok({"entry": entry}, status=201)


def revenue_list():
    rows = db().execute(
        """
        SELECT * FROM revenue_records
        WHERE tenant_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (_tid(), _limit()),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        for key in tuple(item):
            if key.endswith("_minor"):
                item[key[:-6]] = minor_to_money(item[key])
        item["metadata"] = json_load(item.get("metadata_json"), {})
        items.append(item)
    return ok({"items": items, "count": len(items)})


def events_list():
    items = EventService().list_events(
        tenant_id=_tid(),
        category=(request.args.get("category") or "").strip(),
        severity=(request.args.get("severity") or "").strip(),
        limit=_limit(),
    )
    return ok({"items": items, "count": len(items)})


def events_record():
    data = _payload()
    actor_type, actor_id = _actor()
    try:
        event = EventService().record_event(
            tenant_id=_tid(),
            category=str(data.get("category") or ""),
            severity=str(data.get("severity") or "info"),
            event_key=str(data.get("event_key") or ""),
            message=str(data.get("message") or ""),
            actor_type=str(data.get("actor_type") or actor_type),
            actor_id=data.get("actor_id", actor_id),
            target_type=str(data.get("target_type") or ""),
            target_id=data.get("target_id"),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            correlation_id=str(data.get("correlation_id") or ""),
        )
    except BusinessOSValidationError as exc:
        return _validation_error(exc)
    return ok({"event": event}, status=201)


def price_snapshots_list():
    package_id = request.args.get("package_id")
    try:
        package_id_int = int(package_id) if package_id else None
    except ValueError:
        return fail("validation_error", "package_id must be an integer", status=422)
    items = PricingSnapshotService().list_snapshots(
        tenant_id=_tid(),
        reference_type=(request.args.get("reference_type") or "").strip(),
        package_id=package_id_int,
        limit=_limit(),
    )
    return ok({"items": items, "count": len(items)})


def price_snapshots_capture():
    data = _payload()
    actor_type, actor_id = _actor()
    try:
        snapshot = PricingSnapshotService().capture_snapshot(
            tenant_id=_tid(),
            reference_type=str(data.get("reference_type") or ""),
            reference_id=data.get("reference_id"),
            package_id=data.get("package_id"),
            retail_price=data.get("retail_price", 0),
            wholesale_price=data.get("wholesale_price", 0),
            effective_price=data.get("effective_price"),
            discount_amount=data.get("discount_amount", 0),
            currency=str(data.get("currency") or default_currency()),
            captured_by_type=actor_type,
            captured_by_id=actor_id,
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )
    except BusinessOSValidationError as exc:
        return _validation_error(exc)
    return ok({"snapshot": snapshot}, status=201)


def business_summary():
    tenant_id = _tid()
    row = db().execute(
        """
        SELECT
          (SELECT COUNT(*) FROM wallets WHERE tenant_id=?) AS wallets,
          (SELECT COALESCE(SUM(balance_minor), 0) FROM wallets WHERE tenant_id=?) AS wallet_balance_minor,
          (SELECT COUNT(*) FROM ledger_entries WHERE tenant_id=?) AS ledger_entries,
          (SELECT COALESCE(SUM(amount_minor), 0) FROM ledger_entries WHERE tenant_id=? AND voided_at IS NULL) AS ledger_total_minor,
          (SELECT COUNT(*) FROM business_events WHERE tenant_id=?) AS events,
          (SELECT COUNT(*) FROM price_snapshots WHERE tenant_id=?) AS price_snapshots,
          (SELECT COUNT(*) FROM revenue_records WHERE tenant_id=?) AS revenue_records
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
    ).fetchone()
    data = dict(row or {})
    data["wallet_balance"] = minor_to_money(data.pop("wallet_balance_minor", 0))
    data["ledger_total"] = minor_to_money(data.pop("ledger_total_minor", 0))
    return ok(data)
