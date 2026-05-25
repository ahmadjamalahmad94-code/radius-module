"""Safe V40 admin-panel bridge client foundation.

This module is intentionally passive:
- no calls during app startup
- no entitlement enforcement
- no RADIUS/auth/accounting mutation
- all network I/O is opt-in and mockable
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.radius.db.connection import db


LICENSE_CHECK_PATH = "/api/v40/integration/hoberadius/license/check"
CAPACITY_CONTRACT_PATH = "/api/v40/integration/hoberadius/capacity-contract"

SNAPSHOT_LICENSE_CHECK = "license_check"
SNAPSHOT_CAPACITY_CONTRACT = "capacity_contract"

SENSITIVE_KEYS = {
    "secret",
    "shared_secret",
    "token",
    "api_token",
    "password",
    "private_key",
    "radius_secret",
    "license_key",
    "instance_license_key",
}


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _safe_float(value: str | None, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _mask(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                sanitized[key] = _mask(item)
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


@dataclass(frozen=True)
class AdminBridgeConfig:
    enabled: bool
    base_url: str
    license_key: str
    shared_secret: str
    timeout_seconds: float
    retry_count: int

    @classmethod
    def from_env(cls) -> "AdminBridgeConfig":
        return cls(
            enabled=_truthy(os.environ.get("HOBERADIUS_ADMIN_BRIDGE_ENABLED")),
            base_url=(os.environ.get("HOBERADIUS_ADMIN_BASE_URL") or "").strip().rstrip("/"),
            license_key=(
                os.environ.get("HOBERADIUS_LICENSE_KEY")
                or os.environ.get("INSTANCE_LICENSE_KEY")
                or ""
            ).strip(),
            shared_secret=(os.environ.get("HOBERADIUS_ADMIN_SHARED_SECRET") or "").strip(),
            timeout_seconds=_safe_float(
                os.environ.get("HOBERADIUS_ADMIN_TIMEOUT_SECONDS"),
                3.0,
                minimum=0.5,
                maximum=30.0,
            ),
            retry_count=_safe_int(
                os.environ.get("HOBERADIUS_ADMIN_RETRY_COUNT"),
                0,
                minimum=0,
                maximum=3,
            ),
        )

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.base_url:
            missing.append("HOBERADIUS_ADMIN_BASE_URL")
        if not self.license_key:
            missing.append("HOBERADIUS_LICENSE_KEY or INSTANCE_LICENSE_KEY")
        return missing


class AdminBridgeTransport(Protocol):
    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        ...


class UrlLibAdminBridgeTransport:
    """Small stdlib transport. It is only used when a caller invokes the client."""

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={**headers, "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(1024 * 1024)
        parsed = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("admin panel response must be a JSON object")
        return parsed


class AdminBridgeSnapshotStore:
    def save(
        self,
        *,
        tenant_id: int,
        snapshot_type: str,
        status: str,
        source_url: str,
        payload: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        fetched_at: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        now = _utcnow()
        safe_payload = sanitize_payload(payload or {})
        safe_error = sanitize_payload(error or {})
        cur = db().execute(
            """
            INSERT INTO license_admin_bridge_snapshots (
              tenant_id, snapshot_type, status, source_url,
              payload_json, error_json, fetched_at, expires_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id),
                snapshot_type,
                status,
                source_url,
                json.dumps(safe_payload, ensure_ascii=False),
                json.dumps(safe_error, ensure_ascii=False),
                fetched_at or now,
                expires_at,
                now,
            ),
        )
        return self.get(int(cur.lastrowid)) or {}

    def get(self, snapshot_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_bridge_snapshots WHERE id = ?",
            (int(snapshot_id),),
        ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def latest(self, *, tenant_id: int, snapshot_type: str) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM license_admin_bridge_snapshots
            WHERE tenant_id = ? AND snapshot_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(tenant_id), snapshot_type),
        ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def health(
        self,
        *,
        tenant_id: int,
        snapshot_type: str,
        stale_after_seconds: int = 86400,
    ) -> dict[str, Any]:
        snapshot = self.latest(tenant_id=tenant_id, snapshot_type=snapshot_type)
        if not snapshot:
            return {"status": "missing", "stale": True, "snapshot": None}
        fetched_at = str(snapshot.get("fetched_at") or "")
        stale = True
        try:
            clean = fetched_at.replace("Z", "")
            fetched = datetime.fromisoformat(clean)
            stale = datetime.utcnow() - fetched > timedelta(seconds=stale_after_seconds)
        except ValueError:
            stale = True
        status = "stale" if stale else str(snapshot.get("status") or "unknown")
        return {"status": status, "stale": stale, "snapshot": snapshot}

    def _row_to_snapshot(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("payload_json", "error_json"):
            try:
                data[key] = json.loads(data.get(key) or "{}")
            except (TypeError, ValueError):
                data[key] = {}
        return data


class AdminPanelClient:
    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        transport: AdminBridgeTransport | None = None,
        snapshot_store: AdminBridgeSnapshotStore | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.transport = transport or UrlLibAdminBridgeTransport()
        self.snapshot_store = snapshot_store or AdminBridgeSnapshotStore()

    def check_license(self, *, tenant_id: int = 1) -> dict[str, Any]:
        return self._post_snapshot(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_LICENSE_CHECK,
            path=LICENSE_CHECK_PATH,
            payload={"license_key": self.config.license_key},
            validator=_validate_license_payload,
        )

    def fetch_capacity_contract(self, *, tenant_id: int = 1) -> dict[str, Any]:
        return self._post_snapshot(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_CAPACITY_CONTRACT,
            path=CAPACITY_CONTRACT_PATH,
            payload={"license_key": self.config.license_key},
            validator=_validate_capacity_payload,
        )

    def _post_snapshot(
        self,
        *,
        tenant_id: int,
        snapshot_type: str,
        path: str,
        payload: dict[str, Any],
        validator: Any,
    ) -> dict[str, Any]:
        source_url = f"{self.config.base_url}{path}" if self.config.base_url else path
        if not self.config.enabled:
            return {
                "ok": False,
                "status": "disabled",
                "error": {"code": "bridge_disabled"},
                "snapshot": self.snapshot_store.latest(
                    tenant_id=tenant_id,
                    snapshot_type=snapshot_type,
                ),
            }
        missing = self.config.missing_fields()
        if missing:
            snapshot = self.snapshot_store.save(
                tenant_id=tenant_id,
                snapshot_type=snapshot_type,
                status="config_missing",
                source_url=source_url,
                error={"code": "config_missing", "missing": missing},
            )
            return {
                "ok": False,
                "status": "config_missing",
                "error": snapshot["error_json"],
                "snapshot": snapshot,
            }

        headers = {"Accept": "application/json", "User-Agent": "HobeRadius-AdminBridge/1"}
        if self.config.shared_secret:
            headers["X-HobeRadius-Admin-Secret"] = self.config.shared_secret
        response: dict[str, Any] | None = None
        last_error: dict[str, Any] | None = None
        attempts = self.config.retry_count + 1
        for attempt in range(attempts):
            try:
                response = self.transport.request_json(
                    method="POST",
                    url=source_url,
                    headers=headers,
                    json_body=payload,
                    timeout_seconds=self.config.timeout_seconds,
                )
                last_error = None
                break
            except (TimeoutError, socket.timeout) as exc:
                last_error = {
                    "code": "admin_panel_timeout",
                    "message": str(exc) or "admin panel request timed out",
                    "attempt": attempt + 1,
                }
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = {
                    "code": "admin_panel_unavailable",
                    "message": str(exc),
                    "attempt": attempt + 1,
                }
            if attempt + 1 < attempts:
                time.sleep(0.05)

        if response is None:
            snapshot = self.snapshot_store.save(
                tenant_id=tenant_id,
                snapshot_type=snapshot_type,
                status="timeout" if last_error and last_error["code"].endswith("timeout") else "unavailable",
                source_url=source_url,
                error=last_error or {"code": "admin_panel_unavailable"},
            )
            return {
                "ok": False,
                "status": snapshot["status"],
                "error": snapshot["error_json"],
                "snapshot": snapshot,
            }

        validation = validator(response)
        if validation:
            snapshot = self.snapshot_store.save(
                tenant_id=tenant_id,
                snapshot_type=snapshot_type,
                status="invalid_payload",
                source_url=source_url,
                payload=response,
                error={"code": "invalid_payload", "problems": validation},
            )
            return {
                "ok": False,
                "status": "invalid_payload",
                "error": snapshot["error_json"],
                "snapshot": snapshot,
            }

        snapshot = self.snapshot_store.save(
            tenant_id=tenant_id,
            snapshot_type=snapshot_type,
            status="healthy",
            source_url=source_url,
            payload=response,
            expires_at=str(response.get("expires_at") or response.get("valid_until") or ""),
        )
        return {"ok": True, "status": "healthy", "payload": sanitize_payload(response), "snapshot": snapshot}


def _validate_license_payload(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "ok" in payload and not isinstance(payload["ok"], bool):
        problems.append("ok must be boolean when present")
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        problems.append("status is required")
    valid = payload.get("valid")
    if valid is not None and not isinstance(valid, bool):
        problems.append("valid must be boolean when present")
    limits = payload.get("limits")
    if limits is not None and not isinstance(limits, dict):
        problems.append("limits must be an object when present")
    return problems


def _validate_capacity_payload(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "ok" in payload and not isinstance(payload["ok"], bool):
        problems.append("ok must be boolean when present")
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        problems.append("status is required")
    contract = payload.get("contract")
    if contract is not None and not isinstance(contract, dict):
        problems.append("contract must be an object when present")
    limits = payload.get("limits")
    if limits is not None and not isinstance(limits, dict):
        problems.append("limits must be an object when present")
    return problems
