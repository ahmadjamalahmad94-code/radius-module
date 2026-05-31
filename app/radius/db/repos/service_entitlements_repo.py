"""Local service request links and runtime entitlements."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, parse_dt

_SERVICE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")


def _clean_service_key(value: str) -> str:
    key = (value or "").strip()
    if not _SERVICE_KEY_RE.match(key):
        raise ValueError("service_key")
    return key


def _row_dict(row) -> dict[str, Any] | None:
    return dict(row) if row else None


def _future_iso(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=int(days))).isoformat() + "Z"


class ServiceRequestLinkRepository:
    def create_or_update(
        self,
        *,
        tenant_id: int,
        ticket_id: int,
        subscriber_id: int,
        service_key: str,
        service_label: str,
        request_type: str,
        latest_payment_request_id: int | None = None,
        decision: str = "created",
        status: str = "open",
    ) -> dict[str, Any]:
        now = now_iso()
        service_key = _clean_service_key(service_key)
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO service_request_links(
                  tenant_id, ticket_id, subscriber_id, service_key,
                  service_label, request_type, latest_payment_request_id,
                  decision, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id, ticket_id) DO UPDATE SET
                  subscriber_id=excluded.subscriber_id,
                  service_key=excluded.service_key,
                  service_label=excluded.service_label,
                  request_type=excluded.request_type,
                  latest_payment_request_id=COALESCE(
                    excluded.latest_payment_request_id,
                    service_request_links.latest_payment_request_id
                  ),
                  decision=excluded.decision,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (
                    tenant_id,
                    ticket_id,
                    subscriber_id,
                    service_key,
                    service_label[:160],
                    request_type,
                    latest_payment_request_id,
                    decision,
                    status,
                    now,
                    now,
                ),
            )
        return self.get_by_ticket(tenant_id=tenant_id, ticket_id=ticket_id) or {}

    def update_decision(
        self,
        *,
        tenant_id: int,
        ticket_id: int,
        decision: str,
        status: str,
        latest_payment_request_id: int | None = None,
    ) -> dict[str, Any] | None:
        now = now_iso()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE service_request_links
                SET decision = ?,
                    status = ?,
                    latest_payment_request_id = COALESCE(?, latest_payment_request_id),
                    updated_at = ?
                WHERE tenant_id = ? AND ticket_id = ?
                """,
                (decision, status, latest_payment_request_id, now, tenant_id, ticket_id),
            )
        return self.get_by_ticket(tenant_id=tenant_id, ticket_id=ticket_id)

    def get_by_ticket(self, *, tenant_id: int, ticket_id: int) -> dict[str, Any] | None:
        return _row_dict(
            db().execute(
                """
                SELECT * FROM service_request_links
                WHERE tenant_id = ? AND ticket_id = ?
                """,
                (tenant_id, ticket_id),
            ).fetchone()
        )

    def get_by_payment_request(
        self,
        *,
        tenant_id: int,
        payment_request_id: int,
        conn=None,
    ) -> dict[str, Any] | None:
        executor = conn or db()
        return _row_dict(
            executor.execute(
                """
                SELECT * FROM service_request_links
                WHERE tenant_id = ? AND latest_payment_request_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (tenant_id, payment_request_id),
            ).fetchone()
        )


class LocalServiceEntitlementRepository:
    def upsert_from_service_request_payment(
        self,
        *,
        conn,
        tenant_id: int,
        link: dict[str, Any],
        payment_request: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        service_key = _clean_service_key(str(link.get("service_key") or ""))
        now = now_iso()
        config = {
            "activation_mode": "manual_wallet_review",
            "payment_request_id": int(payment_request["id"]),
            "payment_reference": payment_request.get("reference_code") or "",
            "payment_purpose": payment_request.get("purpose") or "",
            "actor": actor or "",
        }
        conn.execute(
            """
            INSERT INTO local_service_entitlements(
              tenant_id, service_key, service_label, enabled, status,
              source_type, source_id, ticket_id, subscriber_id,
              config_json, expires_at, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, service_key) DO UPDATE SET
              service_label=excluded.service_label,
              enabled=excluded.enabled,
              status=excluded.status,
              source_type=excluded.source_type,
              source_id=excluded.source_id,
              ticket_id=excluded.ticket_id,
              subscriber_id=excluded.subscriber_id,
              config_json=excluded.config_json,
              expires_at=excluded.expires_at,
              updated_at=excluded.updated_at
            """,
            (
                tenant_id,
                service_key,
                str(link.get("service_label") or "")[:160],
                1,
                "active",
                "payment_service_apply",
                int(payment_request["id"]),
                int(link.get("ticket_id") or 0) or None,
                int(link.get("subscriber_id") or 0) or None,
                json_dump(config),
                None,
                now,
                now,
            ),
        )
        return self.get_by_service(
            tenant_id=tenant_id,
            service_key=service_key,
            conn=conn,
        ) or {}

    def upsert_trial_from_service_request(
        self,
        *,
        tenant_id: int,
        link: dict[str, Any],
        trial_days: int,
        actor: str,
    ) -> dict[str, Any]:
        service_key = _clean_service_key(str(link.get("service_key") or ""))
        now = now_iso()
        expires_at = _future_iso(trial_days)
        config = {
            "activation_mode": "service_request_trial",
            "trial_days": int(trial_days),
            "actor": actor or "",
        }
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO local_service_entitlements(
                  tenant_id, service_key, service_label, enabled, status,
                  source_type, source_id, ticket_id, subscriber_id,
                  config_json, expires_at, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id, service_key) DO UPDATE SET
                  service_label=excluded.service_label,
                  enabled=excluded.enabled,
                  status=excluded.status,
                  source_type=excluded.source_type,
                  source_id=excluded.source_id,
                  ticket_id=excluded.ticket_id,
                  subscriber_id=excluded.subscriber_id,
                  config_json=excluded.config_json,
                  expires_at=excluded.expires_at,
                  updated_at=excluded.updated_at
                """,
                (
                    tenant_id,
                    service_key,
                    str(link.get("service_label") or "")[:160],
                    1,
                    "active",
                    "service_request_trial",
                    int(link.get("ticket_id") or 0) or None,
                    int(link.get("ticket_id") or 0) or None,
                    int(link.get("subscriber_id") or 0) or None,
                    json_dump(config),
                    expires_at,
                    now,
                    now,
                ),
            )
        return self.get_by_service(tenant_id=tenant_id, service_key=service_key) or {}

    def get_by_service(self, *, tenant_id: int, service_key: str, conn=None) -> dict[str, Any] | None:
        executor = conn or db()
        return _row_dict(
            executor.execute(
                """
                SELECT * FROM local_service_entitlements
                WHERE tenant_id = ? AND service_key = ?
                """,
                (tenant_id, _clean_service_key(service_key)),
            ).fetchone()
        )

    def list_for_contract(self, *, tenant_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM local_service_entitlements
            WHERE tenant_id = ?
            ORDER BY service_key
            """,
            (tenant_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def contract_services(self, *, tenant_id: int) -> dict[str, dict[str, Any]]:
        services: dict[str, dict[str, Any]] = {}
        for row in self.list_for_contract(tenant_id=tenant_id):
            status = str(row.get("status") or "disabled").strip().lower()
            expires_at = row.get("expires_at")
            expired = False
            parsed_expiry = parse_dt(expires_at)
            if parsed_expiry is not None and parsed_expiry < datetime.utcnow():
                expired = True
                status = "expired"
            enabled = bool(row.get("enabled")) and status == "active" and not expired
            config = json_load(row.get("config_json") or "{}", default={})
            services[str(row["service_key"])] = {
                "enabled": enabled,
                "status": status,
                "service_label": row.get("service_label") or "",
                "source": "local_service_entitlement",
                "source_type": row.get("source_type") or "",
                "source_id": row.get("source_id"),
                "ticket_id": row.get("ticket_id"),
                "subscriber_id": row.get("subscriber_id"),
                "payment_request_id": config.get("payment_request_id")
                if isinstance(config, dict)
                else None,
                "trial_days": config.get("trial_days") if isinstance(config, dict) else None,
                "expires_at": expires_at,
                "updated_at": row.get("updated_at"),
            }
        return services
