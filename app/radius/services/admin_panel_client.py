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
import hashlib
import hmac
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

from app.radius.db.connection import db

LOG = logging.getLogger(__name__)

LICENSE_CHECK_PATH = "/api/license/check"
IDENTITY_SYNC_PATH = "/api/integration/hoberadius/identity-sync"
RUNTIME_CONTRACT_PATH = "/api/integration/hoberadius/runtime-contract"
CAPACITY_CONTRACT_PATH = "/api/integration/hoberadius/capacity-contract"
CUSTOMER_USER_PASSWORD_CHANGE_PATH = "/api/integration/hoberadius/customer-users/password-change"
INSTANCE_HEARTBEAT_PATH = "/api/integration/hoberadius/instance-ops/heartbeat"
BACKUP_UPLOAD_PATH = "/api/integration/hoberadius/backups/upload"
RESTORE_POLL_PATH = "/api/integration/hoberadius/backup-restore/poll"
RESTORE_STATUS_PATH_TEMPLATE = "/api/integration/hoberadius/backup-restore/{reference}/status"
SERVICE_ACTIVATION_POLL_PATH = "/api/integration/hoberadius/service-activations/poll"
SERVICE_ACTIVATION_STATUS_PATH_TEMPLATE = "/api/integration/hoberadius/service-activations/{reference}/status"

SNAPSHOT_LICENSE = "license"
SNAPSHOT_CAPACITY = "capacity_contract"
SNAPSHOT_IDENTITY = "identity_sync"

SENSITIVE_KEYS = {
    "secret",
    "shared_secret",
    "token",
    "api_token",
    "authorization",
    "password",
    "new_password",
    "current_password",
    "password_hash",
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


def bridge_setting(key: str, default: str = "") -> str:
    try:
        from app.radius.core.tenant import DEFAULT_TENANT_ID
        from app.radius.db.repos import tenants_repo

        return str(tenants_repo.get_setting(DEFAULT_TENANT_ID, key, default) or "").strip()
    except Exception:
        return default


def bridge_flag(env_name: str, setting_key: str, default: bool = False) -> bool:
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip() != "":
        return _truthy(raw_env)
    raw_setting = bridge_setting(setting_key, "1" if default else "0")
    return _truthy(raw_setting)


def _env_or_bridge_setting(env_names: tuple[str, ...], setting_key: str, default: str = "") -> str:
    for env_name in env_names:
        raw = os.environ.get(env_name)
        if raw is not None and raw.strip() != "":
            return raw.strip()
    return bridge_setting(setting_key, default)


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
        timeout_value = _env_or_bridge_setting(
            ("HOBERADIUS_ADMIN_TIMEOUT_SECONDS",),
            "license_admin_bridge.timeout_seconds",
            "3.0",
        )
        retry_value = _env_or_bridge_setting(
            ("HOBERADIUS_ADMIN_RETRY_COUNT",),
            "license_admin_bridge.retry_count",
            "0",
        )
        return cls(
            enabled=bridge_flag("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "license_admin_bridge.enabled"),
            base_url=_env_or_bridge_setting(
                ("HOBERADIUS_ADMIN_BASE_URL",),
                "license_admin_bridge.base_url",
            ).rstrip("/"),
            license_key=_env_or_bridge_setting(
                ("HOBERADIUS_LICENSE_KEY", "INSTANCE_LICENSE_KEY"),
                "license_admin_bridge.license_key",
            ),
            shared_secret=_env_or_bridge_setting(
                ("HOBERADIUS_ADMIN_SHARED_SECRET",),
                "license_admin_bridge.shared_secret",
            ),
            timeout_seconds=_safe_float(
                timeout_value,
                3.0,
                minimum=0.5,
                maximum=30.0,
            ),
            retry_count=_safe_int(
                retry_value,
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
              AND normalized_status IN ('active', 'valid', 'healthy', 'ok', 'grace')
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
            request_payload=self._license_check_payload(),
            validator=_validate_license_payload,
            fallback_state=lambda: get_current_license_state(tenant_id=tenant_id, store=self.store),
        )

    def fetch_capacity_contract(self, *, tenant_id: int = 1) -> dict[str, Any]:
        return self._fetch_snapshot(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_CAPACITY,
            path=CAPACITY_CONTRACT_PATH,
            request_payload=self._license_check_payload(),
            validator=_validate_capacity_payload,
            fallback_state=lambda: get_current_capacity_contract(tenant_id=tenant_id, store=self.store),
        )

    def fetch_runtime_contract(self, *, tenant_id: int = 1) -> dict[str, Any]:
        return self._fetch_snapshot(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_CAPACITY,
            path=RUNTIME_CONTRACT_PATH,
            request_payload=self._license_check_payload(),
            validator=_validate_runtime_contract_payload,
            fallback_state=lambda: get_current_capacity_contract(tenant_id=tenant_id, store=self.store),
        )

    def fetch_identity_sync(self, *, tenant_id: int = 1) -> dict[str, Any]:
        source_url = f"{self.config.base_url}{IDENTITY_SYNC_PATH}" if self.config.base_url else IDENTITY_SYNC_PATH
        if not self.config.enabled:
            return {"ok": False, "status": "disabled", "error": {"code": "bridge_disabled"}}
        if not str(self.config.base_url or "").lower().startswith("https://"):
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=SNAPSHOT_IDENTITY,
                normalized_status="https_required",
                source_url=source_url,
                error={"code": "https_required", "message": "identity sync requires HTTPS admin panel URL"},
            )
            return {"ok": False, "status": "https_required", "error": snapshot["error_json"], "snapshot": snapshot}
        missing = self.config.missing_fields()
        if self.config.shared_secret == "":
            missing.append("HOBERADIUS_ADMIN_SHARED_SECRET")
        if missing:
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=SNAPSHOT_IDENTITY,
                normalized_status="config_missing",
                source_url=source_url,
                error={"code": "config_missing", "missing": missing},
            )
            return {"ok": False, "status": "config_missing", "error": snapshot["error_json"], "snapshot": snapshot}
        try:
            response = self.transport.request_json(
                method="POST",
                url=source_url,
                headers=self._headers(),
                json_body=self._license_check_payload(),
                timeout_seconds=self.config.timeout_seconds,
            )
        except (TimeoutError, socket.timeout) as exc:
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=SNAPSHOT_IDENTITY,
                normalized_status="timeout",
                source_url=source_url,
                error={"code": "admin_panel_timeout", "message": str(exc)},
            )
            return {"ok": False, "status": "timeout", "error": snapshot["error_json"], "snapshot": snapshot}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=SNAPSHOT_IDENTITY,
                normalized_status="unavailable",
                source_url=source_url,
                error={"code": "admin_panel_unavailable", "message": str(exc)},
            )
            return {"ok": False, "status": "unavailable", "error": snapshot["error_json"], "snapshot": snapshot}

        problems = _validate_identity_payload(response)
        if problems:
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=SNAPSHOT_IDENTITY,
                normalized_status="invalid_payload",
                source_url=source_url,
                payload=response,
                error={"code": "invalid_payload", "problems": problems},
            )
            return {"ok": False, "status": "invalid_payload", "error": snapshot["error_json"], "snapshot": snapshot}
        normalized = _normalize_status(response)
        if response.get("ok") is not True:
            snapshot = self.store.save(
                tenant_id=tenant_id,
                snapshot_type=SNAPSHOT_IDENTITY,
                normalized_status=normalized,
                source_url=source_url,
                payload=response,
                error=sanitize_bridge_payload(response),
                stale_after_seconds=_stale_after(response),
            )
            return {
                "ok": False,
                "status": normalized,
                "error": snapshot["error_json"],
                "snapshot": snapshot,
            }
        snapshot = self.store.save(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_IDENTITY,
            normalized_status=normalized,
            source_url=source_url,
            payload=response,
            stale_after_seconds=_stale_after(response),
        )
        return {
            "ok": True,
            "status": normalized,
            "payload": response,
            "snapshot": snapshot,
        }

    def post_customer_user_password_change(
        self,
        *,
        external_user_id: int | str,
        username: str,
        new_password: str,
    ) -> dict[str, Any]:
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {
                "ok": False,
                "status": "https_required",
                "error": {"code": "https_required", "message": "Password sync requires HTTPS admin panel URL."},
            }
        missing = self.config.missing_fields()
        if self.config.shared_secret == "":
            missing.append("HOBERADIUS_ADMIN_SHARED_SECRET")
        if missing:
            return {
                "ok": False,
                "status": "config_missing",
                "error": {"code": "config_missing", "missing": missing},
            }
        payload = self._license_check_payload({
            "external_user_id": str(external_user_id or "").strip(),
            "username": str(username or "").strip(),
            "new_password": str(new_password or ""),
        })
        return self._post_bridge_payload(path=CUSTOMER_USER_PASSWORD_CHANGE_PATH, payload=payload)

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

    def poll_service_activations(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_bridge_payload(path=SERVICE_ACTIVATION_POLL_PATH, payload=payload)

    def post_service_activation_status(self, *, reference: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe_reference = urllib.parse.quote(str(reference), safe="")
        path = SERVICE_ACTIVATION_STATUS_PATH_TEMPLATE.format(reference=safe_reference)
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

    def _license_check_payload(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "license_key": self.config.license_key,
            "server_fingerprint": _server_fingerprint(),
            "hostname": _hostname(),
            "version": _module_version(),
            "install_id": _install_id(),
            "domain": _public_domain(),
        }
        if extra:
            payload.update(extra)
        if self.config.shared_secret:
            payload["timestamp"] = int(time.time())
            payload["nonce"] = uuid.uuid4().hex
            payload["signature"] = sign_admin_bridge_payload(payload, self.config.shared_secret)
        return payload


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
    if "services" in payload and not isinstance(payload["services"], dict):
        problems.append("services must be an object when present")
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


def _validate_runtime_contract_payload(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if "ok" in payload and not isinstance(payload["ok"], bool):
        problems.append("ok must be boolean when present")
    if not isinstance(payload.get("status"), str) or not payload.get("status", "").strip():
        problems.append("status is required")
    contract = payload.get("contract")
    if contract is not None and not isinstance(contract, dict):
        problems.append("contract must be an object")
    node = contract if isinstance(contract, dict) else payload
    if "license" in node and not isinstance(node["license"], dict):
        problems.append("license must be an object")
    if "services" in node and not isinstance(node["services"], dict):
        problems.append("services must be an object")
    if "limits" in node and not isinstance(node["limits"], dict):
        problems.append("limits must be an object")
    return problems


def _validate_identity_payload(payload: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if payload.get("ok") is not True:
        if not isinstance(payload.get("status"), str) or not payload.get("status", "").strip():
            problems.append("status is required when identity sync is not ok")
        if "users" in payload and not isinstance(payload["users"], list):
            problems.append("users must be a list when present")
        return problems
    if not isinstance(payload.get("users"), list):
        problems.append("users must be a list")
        return problems
    for idx, user in enumerate(payload["users"]):
        if not isinstance(user, dict):
            problems.append(f"users[{idx}] must be an object")
            continue
        if "password" in user or "plain_password" in user:
            problems.append(f"users[{idx}] must not contain plaintext password")
        for key in ("external_user_id", "username", "password_hash", "password_hash_scheme", "password_version"):
            if key not in user:
                problems.append(f"users[{idx}].{key} is required")
        if str(user.get("password_hash_scheme") or "").lower() != "werkzeug":
            problems.append(f"users[{idx}].password_hash_scheme is unsupported")
    return problems


def _normalize_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "unknown").strip().lower()
    if status == "unknown" and payload.get("ok") is True:
        return "ok"
    if status in {"active", "valid", "healthy", "ok", "grace"}:
        return status
    if status in {
        "inactive",
        "expired",
        "blocked",
        "suspended",
        "revoked",
        "denied",
        "disabled",
        "not_found",
        "invalid_request",
        "fingerprint_denied",
        "rate_limited",
    }:
        return status
    return "unknown"


def canonical_admin_bridge_payload(body: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in body.items()
        if key not in {"signature", "hmac_signature"}
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sign_admin_bridge_payload(body: dict[str, Any], secret: str) -> str:
    canonical = canonical_admin_bridge_payload(body)
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _server_fingerprint() -> str:
    explicit = (
        os.environ.get("HOBERADIUS_SERVER_FINGERPRINT")
        or os.environ.get("HOBERADIUS_INSTANCE_FINGERPRINT")
        or ""
    ).strip()
    if explicit:
        return explicit[:255]
    seed = "|".join(
        part
        for part in (
            os.environ.get("HOBERADIUS_INSTANCE_ID", "").strip(),
            _hostname(),
            os.environ.get("HOBERADIUS_DB_PATH", "").strip(),
        )
        if part
    )
    if not seed:
        seed = _hostname() or "hoberadius-local-instance"
    return f"hr-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def _hostname() -> str:
    try:
        return socket.gethostname()[:255]
    except OSError:
        return ""


def _module_version() -> str:
    return (
        os.environ.get("HOBERADIUS_BUILD_SHA")
        or os.environ.get("HOBERADIUS_VERSION")
        or "radius-module"
    )[:80]


def _install_id() -> str:
    return (
        os.environ.get("HOBERADIUS_INSTANCE_ID")
        or os.environ.get("HOBERADIUS_INSTALL_ID")
        or _server_fingerprint()
    )[:120]


def _public_domain() -> str:
    raw = (
        os.environ.get("HOBERADIUS_PUBLIC_URL")
        or os.environ.get("HOBERADIUS_DOMAIN")
        or ""
    ).strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw if "://" in raw else f"//{raw}")
    return (parsed.netloc or parsed.path or raw)[:255]


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
