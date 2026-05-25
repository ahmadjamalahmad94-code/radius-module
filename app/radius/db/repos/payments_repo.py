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

    def update_status(self, tenant_id: int, request_id: int, status: str) -> None:
        status = _require_choice(status, PAYMENT_STATUSES, "status")
        with transaction() as conn:
            conn.execute(
                "UPDATE payment_requests SET status=?, updated_at=? WHERE tenant_id=? AND id=?",
                (status, now_iso(), tenant_id, request_id),
            )


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
