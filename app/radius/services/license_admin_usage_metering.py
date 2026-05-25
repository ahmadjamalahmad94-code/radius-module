"""Usage metering payloads for the V40 admin bridge.

This service only counts local state and optionally sends a report to the admin
panel. It does not enforce capacity limits and does not touch RADIUS,
FreeRADIUS, MikroTik, or CoA paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.radius.db.connection import db, db_path
from app.radius.services.admin_panel_client import (
    AdminBridgeConfig,
    UrlLibAdminBridgeTransport,
    sanitize_bridge_payload,
)

USAGE_REPORT_PATH = "/api/integration/hoberadius/usage-report"


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _month_window(now: datetime | None = None) -> str:
    current = now or datetime.utcnow()
    return current.strftime("%Y-%m")


def _count_table(table: str, *, tenant_id: int, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(table):
        return 0
    if _table_has_column(table, "tenant_id"):
        clause = f" WHERE tenant_id = ?{(' AND ' + where) if where else ''}"
        query_params = (tenant_id, *params)
    else:
        clause = f" WHERE {where}" if where else ""
        query_params = params
    row = db().execute(f"SELECT COUNT(*) AS c FROM {table}{clause}", query_params).fetchone()
    return int(row["c"] if row else 0)


def _table_exists(table: str) -> bool:
    row = db().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_has_column(table: str, column: str) -> bool:
    if not _table_exists(table):
        return False
    rows = db().execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row["name"]) == column for row in rows)


def _db_size_bytes() -> int:
    try:
        path = Path(db_path())
        return int(path.stat().st_size) if path.exists() else 0
    except OSError:
        return 0


def _last_backup_timestamp(tenant_id: int) -> str:
    if not _table_exists("router_backups"):
        return ""
    row = db().execute(
        """
        SELECT created_at FROM router_backups
        WHERE tenant_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone()
    return str(row["created_at"] if row else "")


@dataclass(frozen=True)
class UsageReportAttempt:
    tenant_id: int
    report_window: str
    idempotency_key: str
    dry_run: bool
    status: str
    payload: dict[str, Any]
    error: dict[str, Any] | None = None
    sent_at: str | None = None


class UsageMeteringService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        transport: Any | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.transport = transport or UrlLibAdminBridgeTransport()

    def collect_metrics(self, *, tenant_id: int = 1) -> dict[str, Any]:
        month_prefix = _month_window()
        cards_month = 0
        if _table_exists("cards"):
            row = db().execute(
                """
                SELECT COUNT(*) AS c FROM cards
                WHERE tenant_id = ? AND substr(created_at, 1, 7) = ?
                """,
                (tenant_id, month_prefix),
            ).fetchone()
            cards_month = int(row["c"] if row else 0)

        online_sessions = 0
        if _table_exists("radacct"):
            row = db().execute(
                """
                SELECT COUNT(*) AS c FROM radacct
                WHERE tenant_id = ? AND (acctstoptime IS NULL OR acctstoptime = '')
                """,
                (tenant_id,),
            ).fetchone()
            online_sessions = int(row["c"] if row else 0)

        return {
            "subscribers_total": _count_table("subscribers", tenant_id=tenant_id),
            "subscribers_active": _count_table(
                "subscribers",
                tenant_id=tenant_id,
                where="status IN ('enabled', 'active')",
            ),
            "cards_generated_total": _count_table("cards", tenant_id=tenant_id),
            "cards_generated_month": cards_month,
            "active_cards": _count_table(
                "cards",
                tenant_id=tenant_id,
                where="used = 0 AND revoked = 0",
            ),
            "card_batches": _count_table("card_batches", tenant_id=tenant_id),
            "nas_count": _count_table("nas_devices", tenant_id=tenant_id),
            "routers_count": _count_table("nas_devices", tenant_id=tenant_id),
            "admins_count": _count_table("admins", tenant_id=tenant_id),
            "profiles_plans_count": _count_table("access_plans", tenant_id=tenant_id),
            "print_templates_count": _count_table("print_templates", tenant_id=tenant_id),
            "current_online_sessions": online_sessions,
            "db_storage_bytes": _db_size_bytes(),
            "last_backup_timestamp": _last_backup_timestamp(tenant_id),
        }

    def build_payload(
        self,
        *,
        tenant_id: int = 1,
        report_window: str | None = None,
    ) -> dict[str, Any]:
        window = report_window or _month_window()
        metrics = self.collect_metrics(tenant_id=tenant_id)
        payload = {
            "license_key": self.config.license_key,
            "instance_id": os.environ.get("HOBERADIUS_INSTANCE_ID", ""),
            "module": "radius-module",
            "report_window": window,
            "generated_at": _utcnow(),
            "app_version": os.environ.get("HOBERADIUS_BUILD_SHA", ""),
            "metrics": metrics,
        }
        payload["idempotency_key"] = self.idempotency_key(
            tenant_id=tenant_id,
            report_window=window,
            metrics=metrics,
        )
        return sanitize_bridge_payload(payload)

    def idempotency_key(
        self,
        *,
        tenant_id: int,
        report_window: str,
        metrics: dict[str, Any],
    ) -> str:
        raw = json.dumps(
            {
                "tenant_id": int(tenant_id),
                "report_window": report_window,
                "metrics": metrics,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def send_usage_report(
        self,
        *,
        tenant_id: int = 1,
        report_window: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        payload = self.build_payload(tenant_id=tenant_id, report_window=report_window)
        key = str(payload["idempotency_key"])
        if dry_run:
            attempt = self.record_attempt(
                UsageReportAttempt(
                    tenant_id=tenant_id,
                    report_window=str(payload["report_window"]),
                    idempotency_key=key,
                    dry_run=True,
                    status="dry_run",
                    payload=payload,
                )
            )
            return {"ok": True, "dry_run": True, "payload": payload, "attempt": attempt}

        if not self.config.enabled or not self.config.base_url or not self.config.license_key:
            attempt = self.record_attempt(
                UsageReportAttempt(
                    tenant_id=tenant_id,
                    report_window=str(payload["report_window"]),
                    idempotency_key=key,
                    dry_run=False,
                    status="disabled",
                    payload=payload,
                    error={"code": "bridge_disabled_or_config_missing"},
                )
            )
            return {"ok": False, "dry_run": False, "status": "disabled", "attempt": attempt}

        url = f"{self.config.base_url}{USAGE_REPORT_PATH}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "HobeRadius-AdminBridge/1",
            "Idempotency-Key": key,
        }
        if self.config.shared_secret:
            headers["X-HobeRadius-Admin-Secret"] = self.config.shared_secret
        try:
            response = self.transport.request_json(
                method="POST",
                url=url,
                headers=headers,
                json_body=payload,
                timeout_seconds=self.config.timeout_seconds,
            )
        except (TimeoutError, OSError, ValueError) as exc:
            attempt = self.record_attempt(
                UsageReportAttempt(
                    tenant_id=tenant_id,
                    report_window=str(payload["report_window"]),
                    idempotency_key=key,
                    dry_run=False,
                    status="failed",
                    payload=payload,
                    error={"code": "usage_report_failed", "message": str(exc)},
                )
            )
            return {"ok": False, "dry_run": False, "status": "failed", "attempt": attempt}

        attempt = self.record_attempt(
            UsageReportAttempt(
                tenant_id=tenant_id,
                report_window=str(payload["report_window"]),
                idempotency_key=key,
                dry_run=False,
                status="sent",
                payload=payload,
                error={},
                sent_at=_utcnow(),
            )
        )
        return {
            "ok": True,
            "dry_run": False,
            "status": "sent",
            "response": sanitize_bridge_payload(response),
            "attempt": attempt,
        }

    def record_attempt(self, attempt: UsageReportAttempt) -> dict[str, Any]:
        try:
            cur = db().execute(
                """
                INSERT INTO license_admin_usage_report_attempts (
                  tenant_id, report_window, idempotency_key, dry_run, status,
                  payload_json, error_json, sent_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(attempt.tenant_id),
                    attempt.report_window,
                    attempt.idempotency_key,
                    1 if attempt.dry_run else 0,
                    attempt.status,
                    json.dumps(sanitize_bridge_payload(attempt.payload), ensure_ascii=False),
                    json.dumps(sanitize_bridge_payload(attempt.error or {}), ensure_ascii=False),
                    attempt.sent_at,
                    _utcnow(),
                ),
            )
            return self.get_attempt(int(cur.lastrowid)) or {}
        except sqlite3.IntegrityError:
            existing = db().execute(
                """
                SELECT * FROM license_admin_usage_report_attempts
                WHERE tenant_id = ? AND idempotency_key = ? AND dry_run = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    int(attempt.tenant_id),
                    attempt.idempotency_key,
                    1 if attempt.dry_run else 0,
                ),
            ).fetchone()
            return self._row(existing) if existing else {}

    def get_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_usage_report_attempts WHERE id = ?",
            (int(attempt_id),),
        ).fetchone()
        return self._row(row) if row else None

    def latest_attempt(self, *, tenant_id: int = 1) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM license_admin_usage_report_attempts
            WHERE tenant_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(tenant_id),),
        ).fetchone()
        return self._row(row) if row else None

    def _row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("payload_json", "error_json"):
            try:
                data[key] = json.loads(data.get(key) or "{}")
            except (TypeError, ValueError):
                data[key] = {}
        return data
