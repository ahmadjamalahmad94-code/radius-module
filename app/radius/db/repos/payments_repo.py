"""Payment Collection Center repositories.

Manual Wallet is intentionally treated as payment instructions only. These
repositories persist payment intent, proof, transaction, and webhook contracts;
they do not apply services or write ledger entries.
"""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso
from . import accounting_repo

PAYMENT_PROVIDERS = {"manual_wallet", "jawwal_pay"}
CONFIRMATION_MODES = {"manual", "api"}
CURRENCIES = {"ILS", "USD", "JOD"}
PAYMENT_PURPOSES = {
    "card_purchase",
    "monthly_subscription",
    "subscriber_renewal",
    "quota_topup",
    "time_extension",
    "distributor_payment",
    "loan_settlement",
}
PAYMENT_STATUSES = {
    "pending",
    "proof_submitted",
    "under_review",
    "paid",
    "rejected",
    "expired",
    "cancelled",
    "failed",
}
PROOF_TYPES = {"manual_reference", "image", "note"}
REVIEW_STATUSES = {"approved", "rejected"}
TRANSACTION_STATUSES = {
    "pending",
    "verified_manual",
    "paid_manual",
    "verified_api",
    "failed",
}


def _require_choice(value: str, allowed: set[str], field: str) -> str:
    value = (value or "").strip()
    if value not in allowed:
        raise ValueError(field)
    return value


def _require_positive(amount: float, field: str = "amount") -> float:
    try:
        parsed = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError(field) from exc
    if parsed <= 0:
        raise ValueError(field)
    return parsed


def _bool_int(value: bool | int) -> int:
    return 1 if bool(value) else 0


def _expires_after(minutes: Optional[int]) -> Optional[str]:
    if not minutes:
        return None
    return (datetime.utcnow() + timedelta(minutes=int(minutes))).isoformat() + "Z"


@dataclass(frozen=True)
class PaymentSettings:
    id: Optional[int]
    tenant_id: int
    provider: str = "manual_wallet"
    enabled: bool = False
    wallet_number: str = ""
    wallet_owner_name: str = ""
    currency: str = "ILS"
    confirmation_mode: str = "manual"
    auto_apply: bool = False
    allow_cards: bool = True
    allow_monthly_subscriptions: bool = True
    allow_distributor_payments: bool = True
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    payment_request_ttl_minutes: Optional[int] = 1440
    created_at: str = ""
    updated_at: str = ""


def _settings_from_row(row) -> PaymentSettings:
    return PaymentSettings(
        id=row["id"],
        tenant_id=row["tenant_id"],
        provider=row["provider"],
        enabled=bool(row["enabled"]),
        wallet_number=row["wallet_number"] or "",
        wallet_owner_name=row["wallet_owner_name"] or "",
        currency=row["currency"],
        confirmation_mode=row["confirmation_mode"],
        auto_apply=bool(row["auto_apply"]),
        allow_cards=bool(row["allow_cards"]),
        allow_monthly_subscriptions=bool(row["allow_monthly_subscriptions"]),
        allow_distributor_payments=bool(row["allow_distributor_payments"]),
        min_amount=row["min_amount"],
        max_amount=row["max_amount"],
        payment_request_ttl_minutes=row["payment_request_ttl_minutes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PaymentSettingsRepository:
    def get(self, tenant_id: int) -> Optional[PaymentSettings]:
        row = db().execute(
            "SELECT * FROM tenant_payment_settings WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        return _settings_from_row(row) if row else None

    def upsert(self, *, tenant_id: int, **kwargs: Any) -> PaymentSettings:
        provider = _require_choice(kwargs.get("provider", "manual_wallet"), PAYMENT_PROVIDERS, "provider")
        confirmation_mode = _require_choice(
            kwargs.get("confirmation_mode", "manual"), CONFIRMATION_MODES, "confirmation_mode"
        )
        currency = _require_choice(kwargs.get("currency", "ILS"), CURRENCIES, "currency")
        min_amount = kwargs.get("min_amount")
        max_amount = kwargs.get("max_amount")
        if min_amount is not None:
            min_amount = _require_positive(min_amount, "min_amount")
        if max_amount is not None:
            max_amount = _require_positive(max_amount, "max_amount")
        ttl = kwargs.get("payment_request_ttl_minutes", 1440)
        if ttl is not None and int(ttl) <= 0:
            raise ValueError("payment_request_ttl_minutes")
        now = now_iso()
        existing = self.get(tenant_id)
        values = (
            provider,
            _bool_int(kwargs.get("enabled", False)),
            str(kwargs.get("wallet_number", "") or ""),
            str(kwargs.get("wallet_owner_name", "") or ""),
            currency,
            confirmation_mode,
            _bool_int(kwargs.get("auto_apply", False)),
            _bool_int(kwargs.get("allow_cards", True)),
            _bool_int(kwargs.get("allow_monthly_subscriptions", True)),
            _bool_int(kwargs.get("allow_distributor_payments", True)),
            min_amount,
            max_amount,
            ttl,
            now,
            tenant_id,
        )
        with transaction() as conn:
            if existing:
                conn.execute(
                    """
                    UPDATE tenant_payment_settings
                    SET provider=?, enabled=?, wallet_number=?, wallet_owner_name=?,
                        currency=?, confirmation_mode=?, auto_apply=?, allow_cards=?,
                        allow_monthly_subscriptions=?, allow_distributor_payments=?,
                        min_amount=?, max_amount=?, payment_request_ttl_minutes=?,
                        updated_at=?
                    WHERE tenant_id=?
                    """,
                    values,
                )
            else:
                conn.execute(
                    """
                    INSERT INTO tenant_payment_settings(
                      provider, enabled, wallet_number, wallet_owner_name,
                      currency, confirmation_mode, auto_apply, allow_cards,
                      allow_monthly_subscriptions, allow_distributor_payments,
                      min_amount, max_amount, payment_request_ttl_minutes,
                      created_at, updated_at, tenant_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    values[:-1] + (now, tenant_id),
                )
        created = self.get(tenant_id)
        assert created is not None
        return created


class PaymentReferenceGenerator:
    def __init__(self, *, prefix: str = "PAY", length: int = 8) -> None:
        self.prefix = prefix
        self.length = length

    def generate(self, tenant_id: int) -> str:
        alphabet = string.ascii_uppercase + string.digits
        for _ in range(100):
            suffix = "".join(secrets.choice(alphabet) for _ in range(self.length))
            reference = f"{self.prefix}-{suffix}"
            exists = db().execute(
                "SELECT 1 FROM payment_requests WHERE tenant_id = ? AND reference_code = ?",
                (tenant_id, reference),
            ).fetchone()
            if not exists:
                return reference
        raise RuntimeError("payment_reference_collision")


class PaymentRequestRepository:
    def __init__(self, reference_generator: Optional[PaymentReferenceGenerator] = None) -> None:
        self.reference_generator = reference_generator or PaymentReferenceGenerator()

    def create(
        self,
        *,
        tenant_id: int,
        payer_type: str,
        purpose: str,
        amount: float,
        currency: str,
        provider: str,
        receiver_wallet: str,
        payer_id: Optional[int] = None,
        created_by: Optional[int] = None,
        ttl_minutes: Optional[int] = 1440,
        reference_code: Optional[str] = None,
    ) -> dict[str, Any]:
        purpose = _require_choice(purpose, PAYMENT_PURPOSES, "purpose")
        provider = _require_choice(provider, PAYMENT_PROVIDERS, "provider")
        currency = _require_choice(currency, CURRENCIES, "currency")
        amount = _require_positive(amount)
        if not (payer_type or "").strip():
            raise ValueError("payer_type")
        now = now_iso()
        reference = reference_code or self.reference_generator.generate(tenant_id)
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO payment_requests(
                  tenant_id, payer_type, payer_id, purpose, amount, currency,
                  provider, receiver_wallet, reference_code, status, expires_at,
                  created_by, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    payer_type.strip(),
                    payer_id,
                    purpose,
                    amount,
                    currency,
                    provider,
                    receiver_wallet or "",
                    reference,
                    "pending",
                    _expires_after(ttl_minutes),
                    created_by,
                    now,
                    now,
                ),
            )
            new_id = cur.lastrowid
        row = self.get(tenant_id, new_id)
        assert row is not None
        return row

    def get(self, tenant_id: int, request_id: int) -> Optional[dict[str, Any]]:
        row = db().execute(
            "SELECT * FROM payment_requests WHERE tenant_id = ? AND id = ?",
            (tenant_id, request_id),
        ).fetchone()
        return dict(row) if row else None

    def list(
        self,
        tenant_id: int,
        *,
        status: str = "",
        purpose: str = "",
        payer_type: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM payment_requests WHERE tenant_id = ?"
        vals: list[Any] = [tenant_id]
        if status:
            status = _require_choice(status, PAYMENT_STATUSES, "status")
            sql += " AND status = ?"
            vals.append(status)
        if purpose:
            purpose = _require_choice(purpose, PAYMENT_PURPOSES, "purpose")
            sql += " AND purpose = ?"
            vals.append(purpose)
        if payer_type:
            sql += " AND payer_type = ?"
            vals.append(payer_type)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        vals.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        return [dict(row) for row in db().execute(sql, vals).fetchall()]

    def update_status(self, tenant_id: int, request_id: int, status: str) -> None:
        status = _require_choice(status, PAYMENT_STATUSES, "status")
        with transaction() as conn:
            conn.execute(
                "UPDATE payment_requests SET status=?, updated_at=? WHERE tenant_id=? AND id=?",
                (status, now_iso(), tenant_id, request_id),
            )

    def list_for_review(self, tenant_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM payment_requests
            WHERE tenant_id = ? AND status IN ('proof_submitted', 'under_review')
            ORDER BY id DESC
            """,
            (tenant_id,),
        ).fetchall()
        return [dict(row) for row in rows]


class PaymentProofRepository:
    def create(
        self,
        *,
        payment_request_id: int,
        proof_type: str = "manual_reference",
        reference_number: str = "",
        image_path: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        proof_type = _require_choice(proof_type, PROOF_TYPES, "proof_type")
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO payment_proofs(
                  payment_request_id, proof_type, reference_number, image_path,
                  note, submitted_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (payment_request_id, proof_type, reference_number or None, image_path or None, note or None, now),
            )
            new_id = cur.lastrowid
        row = db().execute("SELECT * FROM payment_proofs WHERE id = ?", (new_id,)).fetchone()
        return dict(row)

    def list_for_request(self, payment_request_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            "SELECT * FROM payment_proofs WHERE payment_request_id = ? ORDER BY id DESC",
            (payment_request_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_for_request(self, payment_request_id: int) -> Optional[dict[str, Any]]:
        row = db().execute(
            "SELECT * FROM payment_proofs WHERE payment_request_id = ? ORDER BY id DESC LIMIT 1",
            (payment_request_id,),
        ).fetchone()
        return dict(row) if row else None

    def mark_reviewed(
        self,
        *,
        proof_id: int,
        reviewed_by: Optional[int],
        review_status: str,
        review_note: str = "",
    ) -> dict[str, Any]:
        review_status = _require_choice(review_status, REVIEW_STATUSES, "review_status")
        with transaction() as conn:
            conn.execute(
                """
                UPDATE payment_proofs
                SET reviewed_by=?, reviewed_at=?, review_status=?, review_note=?
                WHERE id=?
                """,
                (reviewed_by, now_iso(), review_status, review_note or None, proof_id),
            )
        row = db().execute("SELECT * FROM payment_proofs WHERE id = ?", (proof_id,)).fetchone()
        return dict(row)


class PaymentTransactionRepository:
    def create(
        self,
        *,
        payment_request_id: int,
        amount: float,
        currency: str,
        status: str,
        provider_transaction_id: Optional[str] = None,
        raw_payload: Optional[dict[str, Any] | str] = None,
        verified_at: Optional[str] = None,
    ) -> dict[str, Any]:
        amount = _require_positive(amount)
        currency = _require_choice(currency, CURRENCIES, "currency")
        status = _require_choice(status, TRANSACTION_STATUSES, "status")
        payload = raw_payload if isinstance(raw_payload, str) else json_dump(raw_payload or {})
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO payment_collection_transactions(
                  payment_request_id, provider_transaction_id, amount, currency,
                  status, raw_payload, verified_at, created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    payment_request_id,
                    provider_transaction_id,
                    amount,
                    currency,
                    status,
                    payload,
                    verified_at,
                    now_iso(),
                ),
            )
            new_id = cur.lastrowid
        row = db().execute("SELECT * FROM payment_collection_transactions WHERE id = ?", (new_id,)).fetchone()
        return dict(row)


class PaymentCollectionLedgerRepository:
    def apply_paid_request(self, *, tenant_id: int, request_id: int, actor: str = "") -> dict[str, Any]:
        with transaction() as conn:
            request_row = conn.execute(
                "SELECT * FROM payment_requests WHERE tenant_id = ? AND id = ?",
                (tenant_id, request_id),
            ).fetchone()
            if not request_row:
                raise ValueError("payment_request")
            request_data = dict(request_row)
            if request_data["status"] != "paid":
                raise ValueError("status")
            existing_ledger_id = request_data.get("ledger_entry_id")
            if existing_ledger_id:
                existing_entry = conn.execute(
                    "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ? AND id = ?",
                    (tenant_id, existing_ledger_id),
                ).fetchone()
                if existing_entry:
                    return dict(existing_entry)

            existing = conn.execute(
                """
                SELECT * FROM accounting_ledger_entries
                WHERE tenant_id = ? AND source_type = 'payment_collection_request' AND source_id = ?
                """,
                (tenant_id, request_id),
            ).fetchone()
            if existing:
                ledger_id = existing["id"]
                conn.execute(
                    """
                    UPDATE payment_requests
                    SET ledger_entry_id = ?, ledger_applied_at = COALESCE(ledger_applied_at, ?), updated_at = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (ledger_id, now_iso(), now_iso(), tenant_id, request_id),
                )
                return dict(existing)

            subscriber_id = None
            username = ""
            if request_data["payer_type"] == "subscriber" and request_data.get("payer_id"):
                subscriber = conn.execute(
                    """
                    SELECT id, username FROM subscribers
                    WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
                    """,
                    (tenant_id, request_data["payer_id"]),
                ).fetchone()
                if subscriber:
                    subscriber_id = subscriber["id"]
                    username = subscriber["username"] or ""

            ledger_id = accounting_repo.create_ledger_entry(
                conn,
                tenant_id=tenant_id,
                entry_type="payment",
                amount=float(request_data["amount"]),
                direction="credit",
                currency=request_data["currency"],
                subscriber_id=subscriber_id,
                username=username,
                operator=actor,
                source_type="payment_collection_request",
                source_id=request_id,
                related_type="payment_request",
                related_id=request_id,
                status="posted",
                notes=f"Manual wallet payment request {request_data['reference_code']}",
                metadata={
                    "payment_request_id": request_id,
                    "reference_code": request_data["reference_code"],
                    "purpose": request_data["purpose"],
                    "provider": request_data["provider"],
                    "receiver_wallet": request_data["receiver_wallet"],
                    "service_apply": "not_applied_in_rm_p5",
                },
            )
            applied_at = now_iso()
            conn.execute(
                """
                UPDATE payment_requests
                SET ledger_entry_id = ?, ledger_applied_at = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (ledger_id, applied_at, applied_at, tenant_id, request_id),
            )
        row = db().execute(
            "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ? AND id = ?",
            (tenant_id, ledger_id),
        ).fetchone()
        return dict(row)


class PaymentServiceApplyRepository:
    def apply_paid_request(
        self,
        *,
        tenant_id: int,
        request_id: int,
        actor: str = "",
        simulate_failure: bool = False,
    ) -> dict[str, Any]:
        with transaction() as conn:
            request_row = conn.execute(
                "SELECT * FROM payment_requests WHERE tenant_id = ? AND id = ?",
                (tenant_id, request_id),
            ).fetchone()
            if not request_row:
                raise ValueError("payment_request")
            request_data = dict(request_row)
            if request_data["status"] != "paid":
                raise ValueError("status")

            existing = conn.execute(
                """
                SELECT * FROM payment_service_apply_attempts
                WHERE tenant_id = ? AND payment_request_id = ? AND status = 'applied'
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_id, request_id),
            ).fetchone()
            if existing:
                return dict(existing)

            result = {
                "payment_request_id": request_id,
                "purpose": request_data["purpose"],
                "payer_type": request_data["payer_type"],
                "payer_id": request_data["payer_id"],
                "ledger_entry_id": request_data.get("ledger_entry_id"),
                "live_radius_apply": False,
                "live_coa_apply": False,
                "live_mikrotik_apply": False,
                "mode": "record_only",
            }
            status = "applied"
            error_message = ""
            if simulate_failure:
                status = "failed"
                error_message = "simulated apply failure"
                result["failure"] = error_message

            cur = conn.execute(
                """
                INSERT INTO payment_service_apply_attempts(
                  tenant_id, payment_request_id, status, actor, result_json,
                  error_message, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    tenant_id,
                    request_id,
                    status,
                    actor,
                    json_dump(result),
                    error_message,
                    now_iso(),
                ),
            )
            attempt_id = cur.lastrowid
            applied_at = now_iso() if status == "applied" else None
            conn.execute(
                """
                UPDATE payment_requests
                SET service_apply_status = ?, service_apply_attempt_id = ?,
                    service_applied_at = COALESCE(?, service_applied_at),
                    updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (status, attempt_id, applied_at, now_iso(), tenant_id, request_id),
            )
        row = db().execute(
            "SELECT * FROM payment_service_apply_attempts WHERE tenant_id = ? AND id = ?",
            (tenant_id, attempt_id),
        ).fetchone()
        return dict(row)

    def list_for_request(self, *, tenant_id: int, request_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM payment_service_apply_attempts
            WHERE tenant_id = ? AND payment_request_id = ?
            ORDER BY id DESC
            """,
            (tenant_id, request_id),
        ).fetchall()
        return [dict(row) for row in rows]


class PaymentReconciliationRepository:
    def summary(self, *, tenant_id: int) -> dict[str, Any]:
        paid_without_ledger = [
            dict(row) for row in db().execute(
                """
                SELECT id, reference_code, amount, currency, status, created_at
                FROM payment_requests
                WHERE tenant_id = ? AND status = 'paid' AND ledger_entry_id IS NULL
                ORDER BY id DESC
                """,
                (tenant_id,),
            ).fetchall()
        ]
        paid_not_applied = [
            dict(row) for row in db().execute(
                """
                SELECT id, reference_code, amount, currency, status,
                       service_apply_status, ledger_entry_id, created_at
                FROM payment_requests
                WHERE tenant_id = ? AND status = 'paid'
                  AND COALESCE(service_apply_status, 'not_applied') != 'applied'
                ORDER BY id DESC
                """,
                (tenant_id,),
            ).fetchall()
        ]
        expired_pending = [
            dict(row) for row in db().execute(
                """
                SELECT id, reference_code, amount, currency, status, expires_at, created_at
                FROM payment_requests
                WHERE tenant_id = ?
                  AND status IN ('pending', 'proof_submitted', 'under_review')
                  AND expires_at IS NOT NULL
                  AND expires_at < ?
                ORDER BY expires_at ASC
                """,
                (tenant_id, now_iso()),
            ).fetchall()
        ]
        duplicate_provider_transactions = [
            dict(row) for row in db().execute(
                """
                SELECT provider_transaction_id, COUNT(*) AS count,
                       GROUP_CONCAT(t.payment_request_id) AS payment_request_ids
                FROM payment_collection_transactions t
                JOIN payment_requests r ON r.id = t.payment_request_id
                WHERE r.tenant_id = ? AND t.provider_transaction_id IS NOT NULL
                GROUP BY t.provider_transaction_id
                HAVING COUNT(*) > 1
                ORDER BY count DESC
                """,
                (tenant_id,),
            ).fetchall()
        ]
        return {
            "paid_without_ledger": paid_without_ledger,
            "paid_not_applied": paid_not_applied,
            "expired_pending": expired_pending,
            "duplicate_provider_transactions": duplicate_provider_transactions,
            "counts": {
                "paid_without_ledger": len(paid_without_ledger),
                "paid_not_applied": len(paid_not_applied),
                "expired_pending": len(expired_pending),
                "duplicate_provider_transactions": len(duplicate_provider_transactions),
            },
        }


class PaymentWebhookEventRepository:
    def create(
        self,
        *,
        provider: str,
        payload: dict[str, Any] | str,
        event_id: Optional[str] = None,
        payment_request_id: Optional[int] = None,
        signature_valid: Optional[bool] = None,
        processed: bool = False,
    ) -> dict[str, Any]:
        provider = _require_choice(provider, PAYMENT_PROVIDERS, "provider")
        stored_payload = payload if isinstance(payload, str) else json_dump(payload)
        sig = None if signature_valid is None else _bool_int(signature_valid)
        with transaction() as conn:
            if event_id:
                existing = conn.execute(
                    "SELECT * FROM payment_webhook_events WHERE provider=? AND event_id=?",
                    (provider, event_id),
                ).fetchone()
                if existing:
                    return dict(existing)
            cur = conn.execute(
                """
                INSERT INTO payment_webhook_events(
                  provider, event_id, payment_request_id, payload,
                  signature_valid, processed, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    provider,
                    event_id,
                    payment_request_id,
                    stored_payload,
                    sig,
                    _bool_int(processed),
                    now_iso(),
                ),
            )
            new_id = cur.lastrowid
        row = db().execute("SELECT * FROM payment_webhook_events WHERE id = ?", (new_id,)).fetchone()
        return dict(row)

    @staticmethod
    def payload(row: dict[str, Any]) -> Any:
        return json_load(row.get("payload"), {})
