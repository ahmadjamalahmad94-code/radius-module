"""Instance health heartbeat for the V40 admin bridge.

All checks are read-only/best-effort. The service never restarts services,
never shells out, and never touches RADIUS, MikroTik, FreeRADIUS, or CoA live
paths.
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
    AdminPanelClient,
    AdminBridgeConfig,
    sanitize_bridge_payload,
)


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _table_exists(table: str) -> bool:
    row = db().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count_table(table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(table):
        return 0
    clause = f" WHERE {where}" if where else ""
    row = db().execute(f"SELECT COUNT(*) AS c FROM {table}{clause}", params).fetchone()
    return int(row["c"] if row else 0)


@dataclass(frozen=True)
class HeartbeatAttempt:
    tenant_id: int
    idempotency_key: str
    dry_run: bool
    status: str
    payload: dict[str, Any]
    error: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    sent_at: str | None = None


class InstanceHealthService:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)

    def build_payload(self, *, tenant_id: int = 1) -> dict[str, Any]:
        warnings: list[str] = []
        payload = {
            "license_key": self.config.license_key,
            "instance_id": os.environ.get("HOBERADIUS_INSTANCE_ID", ""),
            "module": "radius-module",
            "app_version": os.environ.get("HOBERADIUS_BUILD_SHA", ""),
            "environment": os.environ.get("HOBERADIUS_ENV") or os.environ.get("FLASK_ENV") or "development",
            "generated_at": _utcnow(),
            "db": self._db_status(warnings),
            "freeradius": self._freeradius_status(),
            "accounting": self._accounting_status(),
            "backup": self._backup_status(),
            "storage": self._storage_status(),
            "scheduler": self._scheduler_status(warnings),
            "admin_bridge": self._admin_bridge_status(),
            "warnings": warnings,
            "errors": [],
        }
        payload["idempotency_key"] = self.idempotency_key(tenant_id=tenant_id, payload=payload)
        return sanitize_bridge_payload(payload)

    def send_heartbeat(self, *, tenant_id: int = 1, dry_run: bool = True) -> dict[str, Any]:
        payload = self.build_payload(tenant_id=tenant_id)
        key = str(payload["idempotency_key"])
        if dry_run:
            attempt = self.record_attempt(
                HeartbeatAttempt(
                    tenant_id=tenant_id,
                    idempotency_key=key,
                    dry_run=True,
                    status="dry_run",
                    payload=payload,
                )
            )
            return {"ok": True, "dry_run": True, "payload": payload, "attempt": attempt}

        result = self.admin_client.post_instance_heartbeat(payload=payload)
        status = str(result.get("status") or ("sent" if result.get("ok") else "failed"))
        attempt = self.record_attempt(
            HeartbeatAttempt(
                tenant_id=tenant_id,
                idempotency_key=key,
                dry_run=False,
                status="sent" if result.get("ok") else status,
                payload=payload,
                error=result.get("error") if isinstance(result.get("error"), dict) else {},
                response=result.get("response") if isinstance(result.get("response"), dict) else {},
                sent_at=_utcnow() if result.get("ok") else None,
            )
        )
        return {
            "ok": bool(result.get("ok")),
            "dry_run": False,
            "status": attempt.get("status") or status,
            "payload": payload,
            "attempt": attempt,
            "response": result.get("response") or {},
            "error": result.get("error") or {},
        }

    def idempotency_key(self, *, tenant_id: int, payload: dict[str, Any]) -> str:
        stable = {
            "tenant_id": int(tenant_id),
            "instance_id": payload.get("instance_id") or "",
            "generated_at": str(payload.get("generated_at") or "")[:16],
            "db": payload.get("db", {}).get("status"),
            "accounting": payload.get("accounting", {}).get("status"),
        }
        raw = json.dumps(stable, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def record_attempt(self, attempt: HeartbeatAttempt) -> dict[str, Any]:
        try:
            cur = db().execute(
                """
                INSERT INTO license_admin_heartbeat_attempts (
                  tenant_id, idempotency_key, dry_run, status,
                  payload_json, error_json, response_json, sent_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(attempt.tenant_id),
                    attempt.idempotency_key,
                    1 if attempt.dry_run else 0,
                    attempt.status,
                    json.dumps(sanitize_bridge_payload(attempt.payload), ensure_ascii=False),
                    json.dumps(sanitize_bridge_payload(attempt.error or {}), ensure_ascii=False),
                    json.dumps(sanitize_bridge_payload(attempt.response or {}), ensure_ascii=False),
                    attempt.sent_at,
                    _utcnow(),
                ),
            )
            return self.get_attempt(int(cur.lastrowid)) or {}
        except sqlite3.IntegrityError:
            existing = db().execute(
                """
                SELECT * FROM license_admin_heartbeat_attempts
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
            "SELECT * FROM license_admin_heartbeat_attempts WHERE id = ?",
            (int(attempt_id),),
        ).fetchone()
        return self._row(row) if row else None

    def latest_attempt(self, *, tenant_id: int = 1) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM license_admin_heartbeat_attempts
            WHERE tenant_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(tenant_id),),
        ).fetchone()
        return self._row(row) if row else None

    def _db_status(self, warnings: list[str]) -> dict[str, Any]:
        try:
            db().execute("SELECT 1").fetchone()
            status = "ok"
        except sqlite3.Error as exc:
            warnings.append("db_check_failed")
            return {"type": "sqlite", "status": "error", "error": str(exc)}
        return {
            "type": "sqlite",
            "status": status,
            "path_present": bool(db_path()),
        }

    def _freeradius_status(self) -> dict[str, Any]:
        return {
            "status": "unknown",
            "method": "not_checked",
            "reason": "no_safe_read_only_probe_configured",
        }

    def _accounting_status(self) -> dict[str, Any]:
        if not _table_exists("radacct"):
            return {"status": "unknown", "radacct_table": False, "online_sessions": 0}
        online = _count_table("radacct", "(acctstoptime IS NULL OR acctstoptime = '')")
        total = _count_table("radacct")
        return {
            "status": "ok",
            "radacct_table": True,
            "online_sessions": online,
            "total_sessions": total,
        }

    def _backup_status(self) -> dict[str, Any]:
        latest = None
        if _table_exists("backup_run_logs"):
            latest = db().execute(
                "SELECT status, path, message, created_at FROM backup_run_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if latest:
            row = dict(latest)
            return {
                "status": row.get("status") or "unknown",
                "last_backup_at": row.get("created_at") or "",
                "has_local_backup": bool(row.get("path")),
            }
        router_latest = None
        if _table_exists("router_backups"):
            router_latest = db().execute(
                "SELECT status, created_at FROM router_backups ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if router_latest:
            row = dict(router_latest)
            return {
                "status": row.get("status") or "unknown",
                "last_backup_at": row.get("created_at") or "",
                "has_local_backup": True,
            }
        return {"status": "unknown", "has_local_backup": False, "last_backup_at": ""}

    def _storage_status(self) -> dict[str, Any]:
        try:
            path = Path(db_path())
            size = int(path.stat().st_size) if path.exists() else 0
        except OSError:
            size = 0
        return {"db_storage_bytes": size}

    def _scheduler_status(self, warnings: list[str]) -> dict[str, Any]:
        try:
            from app.workers.heartbeat import snapshot

            workers = snapshot()
        except Exception as exc:  # noqa: BLE001
            warnings.append("worker_heartbeat_snapshot_unavailable")
            return {"status": "unknown", "workers": [], "error": str(exc)}
        return {
            "status": "ok" if workers else "unknown",
            "workers": workers,
        }

    def _admin_bridge_status(self) -> dict[str, Any]:
        latest_success = None
        if _table_exists("license_admin_bridge_snapshots"):
            latest_success = db().execute(
                """
                SELECT snapshot_type, normalized_status, fetched_at
                FROM license_admin_bridge_snapshots
                WHERE normalized_status IN ('active', 'valid', 'healthy', 'ok')
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        latest_usage = None
        if _table_exists("license_admin_usage_report_attempts"):
            latest_usage = db().execute(
                """
                SELECT status, sent_at, created_at
                FROM license_admin_usage_report_attempts
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "bridge_enabled": bool(self.config.enabled),
            "last_successful_snapshot": dict(latest_success) if latest_success else None,
            "latest_usage_report": dict(latest_usage) if latest_usage else None,
        }

    def _row(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("payload_json", "error_json", "response_json"):
            try:
                data[key] = json.loads(data.get(key) or "{}")
            except (TypeError, ValueError):
                data[key] = {}
        return data
