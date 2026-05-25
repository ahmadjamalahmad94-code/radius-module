"""Safe V40 admin-panel bridge foundation.

The client is deliberately passive:
- no calls during app startup
- no entitlement enforcement
- no RADIUS/auth/accounting mutation
- no backup/restore/service activation behavior
- all HTTP I/O is opt-in and mockable
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

from app.radius.db.connection import db

LOG = logging.getLogger(__name__)

LICENSE_CHECK_PATH = "/api/license/check"
CAPACITY_CONTRACT_PATH = "/api/integration/hoberadius/capacity-contract"
INSTANCE_HEARTBEAT_PATH = "/api/integration/hoberadius/instance-ops/heartbeat"
BACKUP_UPLOAD_PATH = "/api/integration/hoberadius/backups/upload"
RESTORE_POLL_PATH = "/api/integration/hoberadius/backup-restore/poll"
RESTORE_STATUS_PATH_TEMPLATE = "/api/integration/hoberadius/backup-restore/{reference}/status"

SNAPSHOT_LICENSE = "license"
SNAPSHOT_CAPACITY = "capacity_contract"

SENSITIVE_KEYS = {
    "secret",
    "shared_secret",
    "token",
    "api_token",
    "authorization",
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


def sanitize_bridge_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                result[key] = _mask(item)
            else:
                result[key] = sanitize_bridge_payload(item)
        return result
    if isinstance(value, list):
        return [sanitize_bridge_payload(item) for item in value]
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


class LicenseAdminSnapshotStore:
    def save(
        self,
        *,
        tenant_id: int,
        snapshot_type: str,
        normalized_status: str,
        source_url: str,
        payload: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        fetched_at: str | None = None,
        stale_after_seconds: int = 86400,
    ) -> dict[str, Any]:
        now = _utcnow()
        cur = db().execute(
            """
            INSERT INTO license_admin_bridge_snapshots (
              tenant_id, snapshot_type, normalized_status, source_url,
              payload_json, error_json, fetched_at, stale_after_seconds, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id),
                snapshot_type,
                normalized_status,
                source_url,
                json.dumps(sanitize_bridge_payload(payload or {}), ensure_ascii=False),
                json.dumps(sanitize_bridge_payload(error or {}), ensure_ascii=False),
                fetched_at or now,
                int(stale_after_seconds or 86400),
                now,
            ),
        )
        return self.get(int(cur.lastrowid)) or {}

    def get(self, snapshot_id: int) -> dict[str, Any] | None:
        row = db().execute(
            "SELECT * FROM license_admin_bridge_snapshots WHERE id = ?",
            (int(snapshot_id),),
        ).fetchone()
        return self._row(row) if row else None

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
        return self._row(row) if row else None

    def latest_success(self, *, tenant_id: int, snapshot_type: str) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM license_admin_bridge_snapshots
            WHERE tenant_id = ? AND snapshot_type = ?
              AND normalized_status IN ('active', 'valid', 'healthy', 'ok')
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(tenant_id), snapshot_type),
        ).fetchone()
        return self._row(row) if row else None

    def state(self, *, tenant_id: int, snapshot_type: str) -> dict[str, Any]:
        latest = self.latest(tenant_id=tenant_id, snapshot_type=snapshot_type)
        success = self.latest_success(tenant_id=tenant_id, snapshot_type=snapshot_type)
        if not success:
            return {
                "ok": False,
                "status": "unknown",
                "stale": True,
                "snapshot": latest,
                "last_success": None,
            }
        stale = _snapshot_is_stale(success)
        return {
            "ok": True,
            "status": "stale" if stale else str(success["normalized_status"]),
            "stale": stale,
            "snapshot": latest,
            "last_success": success,
        }

    def _row(self, row: Any) -> dict[str, Any]:
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
        store: LicenseAdminSnapshotStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.transport = transport or UrlLibAdminBridgeTransport()
        self.store = store or LicenseAdminSnapshotStore()
        self.logger = logger or LOG

    def fetch_license_snapshot(self, *, tenant_id: int = 1) -> dict[str, Any]:
        return self._fetch_snapshot(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_LICENSE,
            path=LICENSE_CHECK_PATH,
            request_payload={"license_key": self.config.license_key},
            validator=_validate_license_payload,
            fallback_state=lambda: get_current_license_state(tenant_id=tenant_id, store=self.store),
        )

    def fetch_capacity_contract(self, *, tenant_id: int = 1) -> dict[str, Any]:
        return self._fetch_snapshot(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_CAPACITY,
            path=CAPACITY_CONTRACT_PATH,
            request_payload={"license_key": self.config.license_key},
            validator=_validate_capacity_payload,
            fallback_state=lambda: get_current_capacity_contract(tenant_id=tenant_id, store=self.store),
        )

    def post_instance_heartbeat(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        source_url = (
            f"{self.config.base_url}{INSTANCE_HEARTBEAT_PATH}"
            if self.config.base_url
            else INSTANCE_HEARTBEAT_PATH
        )
        if not self.config.enabled:
            return {
                "ok": False,
                "status": "disabled",
                "error": {"code": "bridge_disabled"},
            }
        missing = self.config.missing_fields()
        if missing:
            return {
                "ok": False,
                "status": "config_missing",
                "error": {"code": "config_missing", "missing": missing},
            }
        try:
            response = self.transport.request_json(
                method="POST",
                url=source_url,
                headers=self._headers(),
                json_body=payload,
                timeout_seconds=self.config.timeout_seconds,
            )
        except (TimeoutError, socket.timeout) as exc:
            return {
                "ok": False,
                "status": "timeout",
                "error": {"code": "admin_panel_timeout", "message": str(exc)},
            }
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "error": {"code": "admin_panel_unavailable", "message": str(exc)},
            }
        return {
            "ok": True,
            "status": _normalize_status(response),
            "response": sanitize_bridge_payload(response),
        }

    def post_backup_upload(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        source_url = (
            f"{self.config.base_url}{BACKUP_UPLOAD_PATH}"
            if self.config.base_url
            else BACKUP_UPLOAD_PATH
        )
        if not self.config.enabled:
            return {
                "ok": False,
                "status": "disabled",
                "error": {"code": "bridge_disabled"},
            }
        missing = self.config.missing_fields()
        if missing:
            return {
                "ok": False,
                "status": "config_missing",
                "error": {"code": "config_missing", "missing": missing},
            }
        try:
            response = self.transport.request_json(
                method="POST",
                url=source_url,
                headers=self._headers(),
                json_body=payload,
                timeout_seconds=self.config.timeout_seconds,
            )
        except (TimeoutError, socket.timeout) as exc:
            return {
                "ok": False,
                "status": "timeout",
                "error": {"code": "admin_panel_timeout", "message": str(exc)},
            }
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "error": {"code": "admin_panel_unavailable", "message": str(exc)},
            }
        return {
            "ok": True,
            "status": _normalize_status(response),
            "response": sanitize_bridge_payload(response),
        }

    def poll_restore_requests(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_bridge_payload(path=RESTORE_POLL_PATH, payload=payload)

    def post_restore_status(self, *, reference: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe_reference = urllib.parse.quote(str(reference), safe="")
        path = RESTORE_STATUS_PATH_TEMPLATE.format(reference=safe_reference)
        return self._post_bridge_payload(path=path, payload=payload)

    def _post_bridge_payload(self, *, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        source_url = f"{self.config.base_url}{path}" if self.config.base_url else path
        if not self.config.enabled:
            return {
                "ok": False,
                "status": "disabled",
                "error": {"code": "bridge_disabled"},
            }
        missing = self.config.missing_fields()
        if missing:
            return {
                "ok": False,
                "status": "config_missing",
                "error": {"code": "config_missing", "missing": missing},
            }
        try:
            response = self.transport.request_json(
                method="POST",
                url=source_url,
                headers=self._headers(),
                json_body=payload,
                timeout_seconds=self.config.timeout_seconds,
            )
        except (TimeoutError, socket.timeout) as exc:
            return {
                "ok": False,
                "status": "timeout",
                "error": {"code": "admin_panel_timeout", "message": str(exc)},
            }
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {
                "ok": False,
                "status": "unavailable",
                "error": {"code": "admin_panel_unavailable", "message": str(exc)},
            }
        return {
            "ok": True,
            "status": _normalize_status(response),
            "response": sanitize_bridge_payload(response),
        }

    def _fetch_snapshot(
        self,
        *,
        tenant_id: int,
        snapshot_type: str,
        path: str,
        request_payload: dict[str, Any],
        validator: Callable[[dict[str, Any]], list[str]],
        fallback_state: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        source_url = f"{self.config.base_url}{path}" if self.config.base_url else path
        if not self.config.enabled:
            return {
                "ok": False,
                "status": "disabled",
                "error": {"code": "bridge_disabled"},
                "state": fallback_state(),
            }
        missing = self.config.missing_fields()
        if missing:
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=snapshot_type,
                normalized_status="config_missing",
                source_url=source_url,
                error={"code": "config_missing", "missing": missing},
            )
            return {
                "ok": False,
                "status": "config_missing",
                "error": snapshot["error_json"],
                "state": fallback_state(),
                "snapshot": snapshot,
            }

        response: dict[str, Any] | None = None
        last_error: dict[str, Any] | None = None
        for attempt in range(self.config.retry_count + 1):
            try:
                response = self.transport.request_json(
                    method="POST",
                    url=source_url,
                    headers=self._headers(),
                    json_body=request_payload,
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
            if attempt < self.config.retry_count:
                time.sleep(0.05)

        if response is None:
            status = "timeout" if (last_error or {}).get("code") == "admin_panel_timeout" else "unavailable"
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=snapshot_type,
                normalized_status=status,
                source_url=source_url,
                error=last_error or {"code": "admin_panel_unavailable"},
            )
            self.logger.warning(
                "admin bridge %s failed: %s",
                snapshot_type,
                sanitize_bridge_payload(last_error or {}),
            )
            return {
                "ok": False,
                "status": status,
                "error": snapshot["error_json"],
                "state": fallback_state(),
                "snapshot": snapshot,
            }

        problems = validator(response)
        if problems:
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=snapshot_type,
                normalized_status="invalid_payload",
                source_url=source_url,
                payload=response,
                error={"code": "invalid_payload", "problems": problems},
            )
            return {
                "ok": False,
                "status": "invalid_payload",
                "error": snapshot["error_json"],
                "state": fallback_state(),
                "snapshot": snapshot,
            }

        normalized = _normalize_status(response)
        snapshot = self.store.save(
            tenant_id=tenant_id,
            snapshot_type=snapshot_type,
            normalized_status=normalized,
            source_url=source_url,
            payload=response,
            stale_after_seconds=_stale_after(response),
        )
        return {
            "ok": True,
            "status": normalized,
            "payload": snapshot["payload_json"],
            "state": fallback_state(),
            "snapshot": snapshot,
        }

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "HobeRadius-AdminBridge/1",
        }
        if self.config.shared_secret:
            headers["X-HobeRadius-Admin-Secret"] = self.config.shared_secret
        return headers


def get_current_license_state(
    *, tenant_id: int = 1, store: LicenseAdminSnapshotStore | None = None
) -> dict[str, Any]:
    return (store or LicenseAdminSnapshotStore()).state(
        tenant_id=tenant_id,
        snapshot_type=SNAPSHOT_LICENSE,
    )


def get_current_capacity_contract(
    *, tenant_id: int = 1, store: LicenseAdminSnapshotStore | None = None
) -> dict[str, Any]:
    return (store or LicenseAdminSnapshotStore()).state(
        tenant_id=tenant_id,
        snapshot_type=SNAPSHOT_CAPACITY,
    )


def _validate_license_payload(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "ok" in payload and not isinstance(payload["ok"], bool):
        problems.append("ok must be boolean when present")
    if not isinstance(payload.get("status"), str) or not payload.get("status", "").strip():
        problems.append("status is required")
    if "valid" in payload and not isinstance(payload["valid"], bool):
        problems.append("valid must be boolean when present")
    if "limits" in payload and not isinstance(payload["limits"], dict):
        problems.append("limits must be an object when present")
    return problems


def _validate_capacity_payload(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "ok" in payload and not isinstance(payload["ok"], bool):
        problems.append("ok must be boolean when present")
    if not isinstance(payload.get("status"), str) or not payload.get("status", "").strip():
        problems.append("status is required")
    if "contract" in payload and not isinstance(payload["contract"], dict):
        problems.append("contract must be an object when present")
    if "limits" in payload and not isinstance(payload["limits"], dict):
        problems.append("limits must be an object when present")
    return problems


def _normalize_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "unknown").strip().lower()
    if status in {"active", "valid", "healthy", "ok"}:
        return status
    if status in {"inactive", "expired", "blocked", "suspended"}:
        return status
    return "unknown"


def _stale_after(payload: dict[str, Any]) -> int:
    raw = payload.get("stale_after_seconds")
    try:
        return max(60, min(604800, int(raw)))
    except (TypeError, ValueError):
        return 86400


def _snapshot_is_stale(snapshot: dict[str, Any]) -> bool:
    fetched_at = str(snapshot.get("fetched_at") or "").replace("Z", "")
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    age = datetime.utcnow() - fetched
    return age.total_seconds() > int(snapshot.get("stale_after_seconds") or 86400)
