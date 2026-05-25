"""Business OS core financial/event service foundations.

This module is intentionally additive. It does not integrate with live RADIUS,
does not mutate existing accounting tables, and stores new money amounts as
integer minor units to avoid floating-point drift.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict


class BusinessOSValidationError(ValueError):
    """Raised when a Business OS service input is invalid."""


_CENTS = Decimal("0.01")
_OWNER_TYPES = {"company", "manager", "distributor", "subscriber", "card_user"}
_WALLET_TX_TYPES = {"credit", "debit", "transfer", "hold", "release", "reversal"}
_LEDGER_TYPES = {
    "payment",
    "renewal",
    "debt",
    "loan",
    "discount",
    "wallet_recharge",
    "card_sale",
    "batch_creation",
    "profit_share",
    "reversal",
    "correction",
}
_EVENT_CATEGORIES = {
    "manager",
    "subscriber",
    "card",
    "financial",
    "system",
    "security",
    "radius",
    "notification",
}
_EVENT_SEVERITIES = {"debug", "info", "warning", "error", "critical"}


def money_to_minor(amount: Any) -> int:
    """Convert a decimal-like major amount to integer minor units."""
    try:
        dec = Decimal(str(amount)).quantize(_CENTS, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise BusinessOSValidationError("amount must be numeric") from exc
    return int(dec * 100)


def minor_to_money(value: Any) -> str:
    return str((Decimal(int(value or 0)) / Decimal(100)).quantize(_CENTS))


def _positive_minor(amount: Any, *, field: str = "amount") -> int:
    minor = money_to_minor(amount)
    if minor <= 0:
        raise BusinessOSValidationError(f"{field} must be positive")
    return minor


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _row(row: Any) -> dict[str, Any]:
    out = row_to_dict(row)
    for key in tuple(out):
        if key.endswith("_minor"):
            out[key[:-6]] = minor_to_money(out[key])
    return out


class EventService:
    """Append-only business event recorder."""

    def record_event(
        self,
        *,
        tenant_id: int = 1,
        category: str,
        event_key: str,
        message: str = "",
        severity: str = "info",
        actor_type: str = "",
        actor_id: int | None = None,
        target_type: str = "",
        target_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        return self._record_event(
            db(),
            tenant_id=tenant_id,
            category=category,
            event_key=event_key,
            message=message,
            severity=severity,
            actor_type=actor_type,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            metadata=metadata,
            correlation_id=correlation_id,
        )

    def _record_event(self, conn, **kwargs) -> dict[str, Any]:
        category = str(kwargs["category"] or "").strip()
        severity = str(kwargs.get("severity") or "info").strip()
        event_key = str(kwargs["event_key"] or "").strip()
        if category not in _EVENT_CATEGORIES:
            raise BusinessOSValidationError("unknown event category")
        if severity not in _EVENT_SEVERITIES:
            raise BusinessOSValidationError("unknown event severity")
        if not event_key:
            raise BusinessOSValidationError("event_key is required")
        now = now_iso()
        cur = conn.execute(
            """
            INSERT INTO business_events (
              tenant_id, category, severity, actor_type, actor_id, target_type,
              target_id, event_key, message, metadata_json, correlation_id,
              created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(kwargs.get("tenant_id") or 1),
                category,
                severity,
                str(kwargs.get("actor_type") or ""),
                kwargs.get("actor_id"),
                str(kwargs.get("target_type") or ""),
                kwargs.get("target_id"),
                event_key,
                str(kwargs.get("message") or ""),
                _json(kwargs.get("metadata")),
                str(kwargs.get("correlation_id") or ""),
                now,
            ),
        )
        return self.get_event(tenant_id=int(kwargs.get("tenant_id") or 1), event_id=int(cur.lastrowid))

    def get_event(self, *, tenant_id: int = 1, event_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM business_events WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(event_id)),
        ).fetchone()
        return _row(row)

    def list_events(
        self,
        *,
        tenant_id: int = 1,
        category: str = "",
        severity: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM business_events WHERE tenant_id=?"
        params: list[Any] = [int(tenant_id)]
        if category:
            sql += " AND category=?"
            params.append(category)
        if severity:
            sql += " AND severity=?"
            params.append(severity)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [_row(row) for row in db().execute(sql, tuple(params)).fetchall()]


class LedgerService:
    """Immutable ledger writer."""

    def __init__(self, *, event_service: EventService | None = None) -> None:
        self.event_service = event_service or EventService()

    def write_entry(
        self,
        *,
        tenant_id: int = 1,
        entry_type: str,
        debit_account: str,
        credit_account: str,
        amount: Any,
        currency: str = "JOD",
        actor_type: str = "",
        actor_id: int | None = None,
        target_type: str = "",
        target_id: int | None = None,
        reference_type: str = "",
        reference_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        emit_event: bool = True,
    ) -> dict[str, Any]:
        with transaction() as conn:
            entry = self._write_entry(
                conn,
                tenant_id=tenant_id,
                entry_type=entry_type,
                debit_account=debit_account,
                credit_account=credit_account,
                amount=amount,
                currency=currency,
                actor_type=actor_type,
                actor_id=actor_id,
                target_type=target_type,
                target_id=target_id,
                reference_type=reference_type,
                reference_id=reference_id,
                metadata=metadata,
            )
            if emit_event:
                self.event_service._record_event(
                    conn,
                    tenant_id=tenant_id,
                    category="financial",
                    severity="info",
                    event_key=f"ledger.{entry_type}",
                    message=f"Ledger entry recorded: {entry_type}",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    target_type=target_type,
                    target_id=target_id,
                    metadata={"ledger_entry_id": entry["id"], **(metadata or {})},
                )
            return entry

    def _write_entry(self, conn, **kwargs) -> dict[str, Any]:
        entry_type = str(kwargs["entry_type"] or "").strip()
        if entry_type not in _LEDGER_TYPES:
            raise BusinessOSValidationError("unknown ledger entry_type")
        amount_minor = _positive_minor(kwargs["amount"])
        if not str(kwargs["debit_account"] or "").strip():
            raise BusinessOSValidationError("debit_account is required")
        if not str(kwargs["credit_account"] or "").strip():
            raise BusinessOSValidationError("credit_account is required")
        now = now_iso()
        cur = conn.execute(
            """
            INSERT INTO ledger_entries (
              tenant_id, entry_type, debit_account, credit_account,
              amount_minor, currency, actor_type, actor_id, target_type,
              target_id, reference_type, reference_id, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(kwargs.get("tenant_id") or 1),
                entry_type,
                str(kwargs["debit_account"]),
                str(kwargs["credit_account"]),
                amount_minor,
                str(kwargs.get("currency") or "JOD"),
                str(kwargs.get("actor_type") or ""),
                kwargs.get("actor_id"),
                str(kwargs.get("target_type") or ""),
                kwargs.get("target_id"),
                str(kwargs.get("reference_type") or ""),
                kwargs.get("reference_id"),
                _json(kwargs.get("metadata")),
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM ledger_entries WHERE id=?",
            (int(cur.lastrowid),),
        ).fetchone()
        return _row(row)

    def get_entry(self, *, tenant_id: int = 1, entry_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM ledger_entries WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(entry_id)),
        ).fetchone()
        return _row(row)


class WalletService:
    """Wallet creation and balance-changing operations."""

    def __init__(
        self,
        *,
        ledger_service: LedgerService | None = None,
        event_service: EventService | None = None,
    ) -> None:
        self.event_service = event_service or EventService()
        self.ledger_service = ledger_service or LedgerService(event_service=self.event_service)

    def create_wallet(
        self,
        *,
        tenant_id: int = 1,
        owner_type: str,
        owner_id: int | None = None,
        currency: str = "JOD",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        owner = str(owner_type or "").strip()
        if owner not in _OWNER_TYPES:
            raise BusinessOSValidationError("unknown wallet owner_type")
        if owner != "company" and owner_id is None:
            raise BusinessOSValidationError("owner_id is required for this wallet owner_type")
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO wallets (
                  tenant_id, owner_type, owner_id, currency, metadata_json,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (int(tenant_id), owner, owner_id, currency, _json(metadata), now, now),
            )
            self.event_service._record_event(
                conn,
                tenant_id=tenant_id,
                category="financial",
                event_key="wallet.created",
                message="Wallet created",
                target_type=owner,
                target_id=owner_id,
                metadata={"wallet_id": int(cur.lastrowid)},
            )
            return self.get_wallet(tenant_id=tenant_id, wallet_id=int(cur.lastrowid), conn=conn)

    def get_wallet(self, *, tenant_id: int = 1, wallet_id: int, conn=None) -> dict[str, Any]:
        handle = conn or db()
        row = handle.execute(
            "SELECT * FROM wallets WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(wallet_id)),
        ).fetchone()
        return _row(row)

    def credit(self, *, tenant_id: int = 1, wallet_id: int, amount: Any, **kwargs) -> dict[str, Any]:
        return self._change_balance(
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            amount=amount,
            transaction_type="credit",
            **kwargs,
        )

    def debit(self, *, tenant_id: int = 1, wallet_id: int, amount: Any, **kwargs) -> dict[str, Any]:
        return self._change_balance(
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            amount=amount,
            transaction_type="debit",
            **kwargs,
        )

    def _change_balance(
        self,
        *,
        tenant_id: int,
        wallet_id: int,
        amount: Any,
        transaction_type: str,
        reference_type: str = "",
        reference_id: int | None = None,
        actor_type: str = "",
        actor_id: int | None = None,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if transaction_type not in {"credit", "debit"}:
            raise BusinessOSValidationError("unsupported wallet transaction")
        amount_minor = _positive_minor(amount)
        with transaction() as conn:
            wallet = conn.execute(
                "SELECT * FROM wallets WHERE tenant_id=? AND id=?",
                (int(tenant_id), int(wallet_id)),
            ).fetchone()
            if not wallet:
                raise BusinessOSValidationError("wallet not found")
            before = int(wallet["balance_minor"] or 0)
            after = before + amount_minor if transaction_type == "credit" else before - amount_minor
            if after < 0:
                raise BusinessOSValidationError("wallet balance cannot go negative")
            now = now_iso()
            conn.execute(
                "UPDATE wallets SET balance_minor=?, updated_at=? WHERE tenant_id=? AND id=?",
                (after, now, int(tenant_id), int(wallet_id)),
            )
            cur = conn.execute(
                """
                INSERT INTO wallet_transactions (
                  tenant_id, wallet_id, transaction_type, amount_minor,
                  before_balance_minor, after_balance_minor, currency,
                  reference_type, reference_id, actor_type, actor_id, notes,
                  metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(tenant_id),
                    int(wallet_id),
                    transaction_type,
                    amount_minor,
                    before,
                    after,
                    str(wallet["currency"] or "JOD"),
                    reference_type,
                    reference_id,
                    actor_type,
                    actor_id,
                    notes,
                    _json(metadata),
                    now,
                ),
            )
            tx_id = int(cur.lastrowid)
            ledger = self.ledger_service._write_entry(
                conn,
                tenant_id=tenant_id,
                entry_type="wallet_recharge" if transaction_type == "credit" else "correction",
                debit_account="cash" if transaction_type == "credit" else f"wallet:{wallet_id}",
                credit_account=f"wallet:{wallet_id}" if transaction_type == "credit" else "cash",
                amount=amount,
                currency=str(wallet["currency"] or "JOD"),
                actor_type=actor_type,
                actor_id=actor_id,
                target_type=str(wallet["owner_type"] or ""),
                target_id=wallet["owner_id"],
                reference_type=reference_type or "wallet_transaction",
                reference_id=reference_id or tx_id,
                metadata={"wallet_transaction_id": tx_id, **(metadata or {})},
            )
            self.event_service._record_event(
                conn,
                tenant_id=tenant_id,
                category="financial",
                event_key=f"wallet.{transaction_type}",
                message=f"Wallet {transaction_type} recorded",
                actor_type=actor_type,
                actor_id=actor_id,
                target_type=str(wallet["owner_type"] or ""),
                target_id=wallet["owner_id"],
                metadata={
                    "wallet_id": int(wallet_id),
                    "wallet_transaction_id": tx_id,
                    "ledger_entry_id": ledger["id"],
                },
            )
            return {
                "wallet": self.get_wallet(tenant_id=tenant_id, wallet_id=wallet_id, conn=conn),
                "transaction": _row(
                    conn.execute(
                        "SELECT * FROM wallet_transactions WHERE id=?",
                        (tx_id,),
                    ).fetchone()
                ),
                "ledger_entry": ledger,
            }


class PricingSnapshotService:
    """Captures immutable pricing inputs for future revenue actions."""

    def capture_snapshot(
        self,
        *,
        tenant_id: int = 1,
        reference_type: str,
        reference_id: int | None = None,
        package_id: int | None = None,
        retail_price: Any = 0,
        wholesale_price: Any = 0,
        effective_price: Any | None = None,
        discount_amount: Any = 0,
        currency: str = "JOD",
        captured_by_type: str = "",
        captured_by_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(reference_type or "").strip():
            raise BusinessOSValidationError("reference_type is required")
        retail = money_to_minor(retail_price)
        wholesale = money_to_minor(wholesale_price)
        discount = money_to_minor(discount_amount)
        effective = money_to_minor(effective_price if effective_price is not None else retail_price)
        if min(retail, wholesale, discount, effective) < 0:
            raise BusinessOSValidationError("prices cannot be negative")
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO price_snapshots (
                  tenant_id, reference_type, reference_id, package_id,
                  retail_price_minor, wholesale_price_minor,
                  effective_price_minor, discount_amount_minor, currency,
                  captured_at, captured_by_type, captured_by_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(tenant_id),
                    reference_type,
                    reference_id,
                    package_id,
                    retail,
                    wholesale,
                    effective,
                    discount,
                    currency,
                    now,
                    captured_by_type,
                    captured_by_id,
                    _json(metadata),
                ),
            )
            EventService()._record_event(
                conn,
                tenant_id=tenant_id,
                category="financial",
                event_key="price_snapshot.captured",
                message="Price snapshot captured",
                actor_type=captured_by_type,
                actor_id=captured_by_id,
                target_type=reference_type,
                target_id=reference_id,
                metadata={"price_snapshot_id": int(cur.lastrowid)},
            )
            row = conn.execute(
                "SELECT * FROM price_snapshots WHERE id=?",
                (int(cur.lastrowid),),
            ).fetchone()
            return _row(row)
