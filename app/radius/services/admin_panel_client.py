"""Safe V40 admin-panel bridge foundation.

The client is deliberately passive:
- no calls during app startup
- no entitlement enforcement
- no RADIUS/auth/accounting mutation
- no direct backup/restore/service activation mutation
- all HTTP I/O is opt-in and mockable
"""
from __future__ import annotations

import json
import logging
import os
from ..core import env_settings
import hashlib
import secrets
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
IDENTITY_SYNC_PATH = "/api/integration/hoberadius/identity-sync"
RUNTIME_CONTRACT_PATH = "/api/integration/hoberadius/runtime-contract"
CAPACITY_CONTRACT_PATH = "/api/integration/hoberadius/capacity-contract"
CUSTOMER_USER_PASSWORD_CHANGE_PATH = "/api/integration/hoberadius/customer-users/password-change"
CUSTOMER_SERVICE_REQUEST_PATH = "/api/integration/hoberadius/service-requests"
INSTANCE_HEARTBEAT_PATH = "/api/integration/hoberadius/instance-ops/heartbeat"
USAGE_SNAPSHOT_PUSH_PATH = "/api/integration/hoberadius/usage-snapshot/push"
BACKUP_UPLOAD_PATH = "/api/integration/hoberadius/backups/upload"
PORTAL_SSO_PATH = "/api/integration/hoberadius/portal-sso"
GDRIVE_STATUS_PATH = "/api/integration/hoberadius/google-drive/status"
# WhatsApp subscriber messaging — the radius module is a THIN CLIENT: it never
# talks to the upstream messaging provider's API and never stores any provider
# credential (token / business-account id / app secret). Every WhatsApp action
# is a signed bridge POST to the license panel, which owns the provider
# credentials and performs the actual send.
WHATSAPP_STATUS_PATH = "/api/integration/hoberadius/whatsapp/status"
WHATSAPP_ENQUEUE_PATH = "/api/integration/hoberadius/whatsapp/messages/enqueue"
WHATSAPP_TEST_PATH = "/api/integration/hoberadius/whatsapp/messages/test"
WHATSAPP_CLOUD_TEST_PATH = "/api/integration/hoberadius/whatsapp/cloud-test"
WHATSAPP_PREFERENCES_SYNC_PATH = "/api/integration/hoberadius/whatsapp/subscriber-preferences/sync"
WHATSAPP_MESSAGE_STATUS_PATH = "/api/integration/hoberadius/whatsapp/messages/status"
# CHR tunnels — the radius module is a THIN CONSUMER: it asks the panel to
# provision/list tunnels and acknowledges what it stored locally. It never
# generates CHR credentials and never stores raw tunnel secrets (see
# services/license_tunnel_bridge.py + migration 110).
VPN_TUNNEL_REQUEST_PATH = "/api/integration/hoberadius/vpn/tunnels/request"
VPN_TUNNELS_PATH = "/api/integration/hoberadius/vpn/tunnels"
VPN_TUNNELS_ACK_PATH = "/api/integration/hoberadius/vpn/tunnels/ack"
# Super-admin enforcement — the panel is the source of truth for which local
# admins may be super. This module reports its admin inventory and applies the
# overrides the panel returns in the identity-sync response.
ADMINS_REPORT_PATH = "/api/integration/hoberadius/admins/report"
# Bridge-token rotation — customer reports locally-minted tokens to the panel
# so both sides converge on the same rotating credential.
BRIDGE_TOKEN_REPORT_PATH = "/api/integration/hoberadius/bridge-token/report"
RESTORE_POLL_PATH = "/api/integration/hoberadius/backup-restore/poll"
RESTORE_STATUS_PATH_TEMPLATE = "/api/integration/hoberadius/backup-restore/{reference}/status"
SERVICE_ACTIVATION_POLL_PATH = "/api/integration/hoberadius/service-activations/poll"
SERVICE_ACTIVATION_STATUS_PATH_TEMPLATE = "/api/integration/hoberadius/service-activations/{reference}/status"
# Unified notifications — the panel EMITS notifications (license/billing/service
# events) and this module INGESTS them via poll, then acks what it stored so the
# panel can stop re-sending. Customer support tickets/complaints flow the other
# way (this module → panel) over the same signed bridge.
NOTIFICATIONS_POLL_PATH = "/api/integration/hoberadius/notifications/poll"
NOTIFICATIONS_ACK_PATH = "/api/integration/hoberadius/notifications/ack"
CUSTOMER_SUPPORT_TICKET_PATH = "/api/integration/hoberadius/customer-support/tickets"
# Central FCM push — the radius module is a THIN FORWARDER: the ONE global
# mobile app is backed by a single central Firebase project owned by the
# licensing panel, so this module never holds the Firebase key and never calls
# FCM. It forwards (a) the app's device-token registration and (b) push
# requests to the panel, which performs the actual FCM send to that customer's
# devices. Mirrors the WhatsApp thin-client posture.
PUSH_REGISTER_TOKEN_PATH = "/api/integration/hoberadius/push/register-token"
PUSH_UNREGISTER_TOKEN_PATH = "/api/integration/hoberadius/push/unregister-token"
PUSH_SEND_PATH = "/api/integration/hoberadius/push/send"

SNAPSHOT_LICENSE = "license"
SNAPSHOT_CAPACITY = "capacity_contract"
SNAPSHOT_IDENTITY = "identity_sync"

# Bounded-by-design cache: the snapshot table is a "latest known state" cache,
# not a history log. Every sync cycle used to APPEND a full-payload row forever
# (this is what ballooned the DB to 165MB). The writer now trims each scope to
# the latest N rows on every insert, so the table can never grow unbounded
# again — a single license/capacity/identity scope holds at most
# SNAPSHOT_KEEP_PER_SCOPE rows plus, if older, the last *successful* one (which
# state()/latest_success read independently). The daily retention worker applies
# the identical bound as a self-healing backstop.
SNAPSHOT_KEEP_PER_SCOPE = 5
# The normalized_status values that count as a usable success snapshot. Single
# source of truth — latest_success(), the write-time trim, and log_retention all
# reference this exact set.
SNAPSHOT_SUCCESS_STATUSES = ("active", "valid", "healthy", "ok", "grace")


def _snapshot_keep_per_scope() -> int:
    raw = os.environ.get("HOBERADIUS_RETENTION_LICENSE_ADMIN_BRIDGE_SNAPSHOTS_KEEP")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0, int(str(raw).strip()))
        except ValueError:
            pass
    return SNAPSHOT_KEEP_PER_SCOPE

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


def mask_license_key(value: str | None) -> str:
    """Return a redacted ``lic_…xxxx`` form of a license key — safe for logs.

    Never logs the full key. Returns the first 4 chars (or ``lic`` if the key
    is too short) joined by ``…`` to the last 4. Empty/None → ``""`` so the
    helper is safe to drop into any string-formatting context.

    Used by the bearer-in-body path: the key IS the secret, so any debug
    line that references it must mask first. ``sanitize_bridge_payload``
    already masks dict-shaped payloads through ``SENSITIVE_KEYS``; this
    helper covers the rare cases where the key is interpolated into a
    bare string before logging.
    """
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return s[:2] + "…"
    return s[:4] + "…" + s[-4:]


def bridge_setting(key: str, default: str = "") -> str:
    try:
        from app.radius.core.tenant import DEFAULT_TENANT_ID
        from app.radius.db.repos import tenants_repo

        return str(tenants_repo.get_setting(DEFAULT_TENANT_ID, key, default) or "").strip()
    except Exception:
        return default


def bridge_flag(env_name: str, setting_key: str, default: bool = False) -> bool:
    raw_env = env_settings.env(env_name)
    if raw_env is not None and raw_env.strip() != "":
        return _truthy(raw_env)
    raw_setting = bridge_setting(setting_key, "1" if default else "0")
    return _truthy(raw_setting)


def _env_or_bridge_setting(env_names: tuple[str, ...], setting_key: str, default: str = "") -> str:
    for env_name in env_names:
        raw = env_settings.env(env_name)
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
    """Bridge configuration — SIMPLE_LINK only (يونيو 2026 purge).

    The legacy ``shared_secret`` field + HMAC signed-path were removed
    permanently. Auth is bearer-in-body: the ``license_key`` is the
    only credential the client and the panel both know. See the
    Simple_Link contract on the panel side.
    """
    enabled: bool
    base_url: str
    license_key: str
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
        # Parse 4xx/5xx JSON bodies too. The simplified panel contract returns
        # ``403 {"ok":false,"status":"customer_pending","reason":"customer_pending"}``
        # when the customer card hasn't been activated yet — we MUST surface
        # that as a normal status so the route layer can map it to the owner's
        # Arabic friendly message instead of leaking «cryptic 403».
        # Without this, urllib raises HTTPError → URLError branch → status
        # collapses to ``unavailable`` and the reason is lost.
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(1024 * 1024)
        except urllib.error.HTTPError as http_err:
            # 401/403 from the panel carries a JSON body with ``status``+``reason``.
            try:
                raw_err = http_err.read(1024 * 1024) if http_err.fp else b""
            except Exception:  # noqa: BLE001
                raw_err = b""
            try:
                parsed_err = json.loads((raw_err or b"").decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                parsed_err = {}
            if isinstance(parsed_err, dict) and parsed_err:
                # Make sure ``status`` is present so _normalize_status doesn't
                # collapse to "unknown".
                if "status" not in parsed_err and parsed_err.get("reason"):
                    parsed_err["status"] = str(parsed_err["reason"]).strip().lower()
                parsed_err.setdefault("ok", False)
                parsed_err.setdefault("http_status", http_err.code)
                return parsed_err
            # Empty/unparseable error body — re-raise so the caller falls
            # into the ``unavailable`` branch (existing behavior).
            raise
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
        new_id = int(cur.lastrowid)
        # Bound the cache at write time so this table can never grow unbounded.
        self._trim_scope(tenant_id=int(tenant_id), snapshot_type=snapshot_type)
        return self.get(new_id) or {}

    def _trim_scope(self, *, tenant_id: int, snapshot_type: str) -> int:
        """Delete everything in this (tenant, snapshot_type) scope beyond the
        latest ``keep`` rows, always preserving the last *successful* snapshot in
        the scope (state()/latest_success may read it even when it is older than
        the newest row). Scoped + index-backed, so it is O(keep) per write.
        Returns the number of rows removed. ``keep<=0`` disables trimming."""
        keep = _snapshot_keep_per_scope()
        if keep <= 0:
            return 0
        status_ph = ",".join("?" for _ in SNAPSHOT_SUCCESS_STATUSES)
        try:
            cur = db().execute(
                f"""
                DELETE FROM license_admin_bridge_snapshots
                WHERE tenant_id = ? AND snapshot_type = ?
                  AND id < (
                    SELECT MIN(keep_id) FROM (
                      SELECT id AS keep_id FROM license_admin_bridge_snapshots
                      WHERE tenant_id = ? AND snapshot_type = ?
                      ORDER BY id DESC LIMIT ?
                    )
                  )
                  AND id NOT IN (
                    SELECT MAX(id) FROM license_admin_bridge_snapshots
                    WHERE tenant_id = ? AND snapshot_type = ?
                      AND normalized_status IN ({status_ph})
                  )
                """,
                (
                    tenant_id, snapshot_type,
                    tenant_id, snapshot_type, keep,
                    tenant_id, snapshot_type, *SNAPSHOT_SUCCESS_STATUSES,
                ),
            )
            return int(cur.rowcount or 0)
        except Exception:  # noqa: BLE001 — trimming must never break a sync write
            return 0

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
              AND normalized_status IN ({status_ph})
            ORDER BY id DESC
            LIMIT 1
            """.format(status_ph=",".join("?" for _ in SNAPSHOT_SUCCESS_STATUSES)),
            (int(tenant_id), snapshot_type, *SNAPSHOT_SUCCESS_STATUSES),
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

    def post_customer_service_request(
        self,
        *,
        service_key: str,
        request_type: str = "activation",
        notes: str = "",
        desired_limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {
                "ok": False,
                "status": "https_required",
                "error": {"code": "https_required", "message": "طلبات الخدمات تتطلب رابط لوحة تراخيص آمن HTTPS."},
            }
        payload = self._license_check_payload({
            "service_key": str(service_key or "").strip(),
            "request_type": str(request_type or "activation").strip(),
            "notes": str(notes or "").strip(),
            "desired_limits": desired_limits or {},
        })
        return self._post_bridge_payload(path=CUSTOMER_SERVICE_REQUEST_PATH, payload=payload)

    def post_ip_change_request(self, *, requested_speed_mbps: int) -> dict[str, Any]:
        """يدفع طلب «تغيير الـIP» إلى لوحة التراخيص بالعقد القانونيّ الموحَّد.

        يُعاد استخدام نفس قناة الربط/المصادقة (Bearer license_key عبر
        _post_bridge_payload + _license_check_payload) ونفس مسار طلبات
        الخدمة CUSTOMER_SERVICE_REQUEST_PATH. الجسم القانونيّ المتّفق عليه:
        {service_type, requested_speed_mbps, billing_cycle, data_limit}
        (مغلّفًا بظرف الترخيص القياسيّ الذي تتطلّبه كل نداءات التكامل)."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {
                "ok": False,
                "status": "https_required",
                "error": {"code": "https_required",
                          "message": "طلبات الخدمات تتطلب رابط لوحة تراخيص آمن HTTPS."},
            }
        payload = self._license_check_payload({
            "service_type": "ip_change",
            "requested_speed_mbps": int(requested_speed_mbps),
            "billing_cycle": "monthly",
            "data_limit": "unlimited",
        })
        return self._post_bridge_payload(path=CUSTOMER_SERVICE_REQUEST_PATH,
                                         payload=payload)

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
        # The heartbeat payload was built through ``sanitize_bridge_payload``
        # (caller side), which MASKS ``license_key``. The panel needs the
        # REAL key in the wire body — it's the bearer secret per
        # SIMPLE_LINK_CONTRACT. Mirror what ``post_backup_upload`` does:
        # restore the live license_key just before transmit. Same reasoning
        # for the new ``provision_on_link`` fields the panel reads to mint
        # the RADIUS instance + ProxyRealmRoute.
        body = dict(payload)
        if self.config.license_key:
            body["license_key"] = self.config.license_key
        try:
            response = self.transport.request_json(
                method="POST",
                url=source_url,
                headers=self._headers(),
                json_body=body,
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
            # Raw response — for immediate transient processing only (e.g.
            # extracting the panel-minted ``shared_secret`` before it gets
            # masked by ``sanitize_bridge_payload``). Caller must NOT persist
            # or log this value. Mirrors ``_fetch_snapshot``'s ``_raw_response``.
            "_raw_response": response,
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
        # The upload payload was built through sanitize_bridge_payload(), which
        # MASKS sensitive keys including ``license_key`` — so the panel would
        # receive a masked key and fail to resolve the license/secret (401).
        # Restore the real license_key in the body — it's the bearer
        # secret per the panel's Simple_Link contract, and the upstream
        # ``sanitize_bridge_payload`` step masked it before we got here.
        body = dict(payload)
        if self.config.license_key:
            body["license_key"] = self.config.license_key
        # Backup uploads carry the full DB content (many MB) and the panel may
        # spend time storing + forwarding it, so the tiny default bridge
        # timeout (≈3s) is far too short. Use a dedicated, generous timeout.
        backup_timeout = _safe_float(
            env_settings.env("HOBERADIUS_ADMIN_BACKUP_TIMEOUT_SECONDS"),
            180.0,
            minimum=30.0,
            maximum=900.0,
        )
        try:
            response = self.transport.request_json(
                method="POST",
                url=source_url,
                headers=self._headers(),
                json_body=body,
                timeout_seconds=max(self.config.timeout_seconds, backup_timeout),
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
        # The transport returns the panel's JSON body even for a 4xx response
        # (it parses error bodies instead of raising). Treating "transport
        # didn't raise" as success SILENTLY SWALLOWED panel rejections — e.g.
        # 413 content-too-large, customer_pending, or the backups service not
        # being provisioned — so the upload looked successful while the backup
        # never reached the customer file (and the panel never forwarded it to
        # Google Drive). Honor the panel's own ok/http_status so a rejection
        # surfaces as a clear error instead of a false success.
        http_status = response.get("http_status")
        panel_rejected = (
            response.get("ok") is False
            or (isinstance(http_status, int) and http_status >= 400)
        )
        if panel_rejected:
            reason = (
                response.get("reason")
                or response.get("status")
                or (response.get("error") or {}).get("message")
                or (f"HTTP {http_status}" if http_status else "rejected")
            )
            return {
                "ok": False,
                "status": _normalize_status(response),
                "error": {
                    "code": "panel_rejected_backup",
                    "http_status": http_status,
                    "message": str(reason),
                },
                "response": sanitize_bridge_payload(response),
            }
        return {
            "ok": True,
            "status": _normalize_status(response),
            "response": sanitize_bridge_payload(response),
        }

    def fetch_google_drive_status(self) -> dict[str, Any]:
        """Read the customer's Google Drive connection status from the panel."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(path=GDRIVE_STATUS_PATH, payload=self._license_check_payload())

    # ── WhatsApp subscriber messaging (thin client) ─────────────────────────
    # All five methods are signed bridge POSTs through the panel, mirroring
    # fetch_google_drive_status EXACTLY. The panel holds the provider
    # credentials and performs the real send; this module never calls the
    # upstream messaging provider's API and never stores any provider secret.
    # Each returns the parsed JSON dict and NEVER raises — a safe dict comes
    # back on any failure.
    def get_whatsapp_status(self) -> dict[str, Any]:
        """Read the customer's WhatsApp connection/usage status from the panel."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(path=WHATSAPP_STATUS_PATH, payload=self._license_check_payload())

    def enqueue_whatsapp_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask the panel to enqueue a templated WhatsApp message to a subscriber.

        ``payload`` carries source_event_type, subscriber_id, recipient_phone,
        template_key, language, variables and idempotency_key — all merged into
        the signed license-check envelope so the panel can authenticate + dedupe.
        """
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=WHATSAPP_ENQUEUE_PATH,
            payload=self._license_check_payload(dict(payload or {})),
        )

    def send_whatsapp_test(self, recipient_phone: str, idempotency_key: str) -> dict[str, Any]:
        """Ask the panel to send a single WhatsApp test message (idempotent)."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=WHATSAPP_TEST_PATH,
            payload=self._license_check_payload({
                "recipient_phone": str(recipient_phone or "").strip(),
                "idempotency_key": str(idempotency_key or "").strip(),
            }),
        )

    def send_whatsapp_cloud_test(
        self, recipient_phone: str, *, template_name: str = "", language: str = ""
    ) -> dict[str, Any]:
        """Ask the panel to send a TEST WhatsApp message via its HOUSE Cloud API
        credentials (the settings panel) — test-only, no customer queue.

        Returns the panel's JSON ({"ok": True, "provider_message_id": ...} or
        {"ok": False, "message_ar": ...}). Never raises.
        """
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=WHATSAPP_CLOUD_TEST_PATH,
            payload=self._license_check_payload({
                "recipient_phone": str(recipient_phone or "").strip(),
                "template_name": str(template_name or "").strip(),
                "language": str(language or "").strip(),
            }),
        )

    def sync_subscriber_preferences(self, subscribers: list) -> dict[str, Any]:
        """Push the local per-subscriber WhatsApp opt-in preferences to the panel."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=WHATSAPP_PREFERENCES_SYNC_PATH,
            payload=self._license_check_payload({"subscribers": list(subscribers or [])}),
        )

    def get_message_status(self, idempotency_key: str) -> dict[str, Any]:
        """Read the delivery status of a previously enqueued WhatsApp message."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=WHATSAPP_MESSAGE_STATUS_PATH,
            payload=self._license_check_payload({
                "idempotency_key": str(idempotency_key or "").strip(),
            }),
        )

    def request_portal_sso(self) -> dict[str, Any]:
        """Ask the panel for a short-lived SSO link into the customer portal."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required",
                    "error": {"code": "https_required", "message": "الدخول الموحّد يتطلب رابط لوحة آمن HTTPS."}}
        payload = self._license_check_payload()
        return self._post_bridge_payload(path=PORTAL_SSO_PATH, payload=payload)

    # ── CHR tunnels (thin consumer) ─────────────────────────────────────────
    # All three are signed bridge POSTs mirroring the WhatsApp/SSO helpers. The
    # panel owns the CHR; this module only requests, lists, and acks. Each
    # returns the parsed JSON dict and never raises.
    def request_vpn_tunnel(
        self,
        *,
        tunnel_type: str = "sstp",
        router_id: int | str = "",
        label: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        """Ask the panel to provision a tunnel and return its SSTP user/pass.

        The credentials in the response are for one-time local injection only —
        the caller MUST NOT persist the raw password (see migration 110).
        """
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required",
                    "error": {"code": "https_required", "message": "طلب النفق يتطلب رابط لوحة آمن HTTPS."}}
        return self._post_bridge_payload(
            path=VPN_TUNNEL_REQUEST_PATH,
            payload=self._license_check_payload({
                "tunnel_type": str(tunnel_type or "sstp").strip(),
                "router_id": str(router_id or "").strip(),
                "label": str(label or "").strip(),
                "notes": str(notes or "").strip(),
            }),
            sanitize=False,  # service needs the one-time SSTP password (never persisted)
        )

    def fetch_vpn_tunnels(self) -> dict[str, Any]:
        """List the customer's tunnels from the panel (incl. manual PPTP/L2TP/IPsec)."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=VPN_TUNNELS_PATH,
            payload=self._license_check_payload(),
            sanitize=False,  # passwords for un-acked tunnels arrive here for one-time injection
        )

    def ack_vpn_tunnels(self, names: list[str]) -> dict[str, Any]:
        """Tell the panel which tunnel names were stored locally.

        After ack the panel stops re-sending the tunnel passwords.
        """
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=VPN_TUNNELS_ACK_PATH,
            payload=self._license_check_payload({
                "tunnel_names": [str(n).strip() for n in (names or []) if str(n).strip()],
            }),
        )

    def post_admins_report(self, *, admins: list[dict[str, Any]],
                           full_snapshot: bool = False) -> dict[str, Any]:
        """Report the local admin inventory so the panel can decide super-admins
        AND prune deleted admins from its «managed admins» view.

        Carries only non-secret identity fields (id/username/role/flags). Never
        sends password hashes.

        ``full_snapshot=True`` (admins-report v2) tells the panel the ``admins``
        list is authoritative: any admin whose id is NOT present is treated as
        deleted on the instance and pruned from the panel. Set False for
        differential/tombstone updates (e.g. ``[{"id": 7, "deleted": true}]``
        for a single revoke) so the panel prunes only the listed tombstones.

        Safety: an empty ``admins:[]`` is REJECTED before sending (an accidental
        empty-with-full_snapshot would delete every admin from the panel).
        """
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required",
                    "error": {"code": "https_required",
                              "message": "تقرير المدراء يتطلب رابط لوحة آمن HTTPS."}}
        admins_list = list(admins or [])
        if not admins_list:
            # A full-snapshot with an empty list would delete every admin from
            # the panel. Refuse — the caller MUST always include at least the
            # primary/local admin (invariant of admins-report v2).
            return {"ok": False, "status": "empty_admins",
                    "error": {"code": "empty_admins",
                              "message": "قائمة المدراء فارغة — رفض التقرير حماية من حذف جماعي غير مقصود."}}
        return self._post_bridge_payload(
            path=ADMINS_REPORT_PATH,
            payload=self._license_check_payload({
                "admins": admins_list,
                "full_snapshot": bool(full_snapshot),
            }),
        )

    def poll_restore_requests(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_bridge_payload(path=RESTORE_POLL_PATH, payload=payload)

    def post_restore_status(self, *, reference: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe_reference = urllib.parse.quote(str(reference), safe="")
        path = RESTORE_STATUS_PATH_TEMPLATE.format(reference=safe_reference)
        return self._post_bridge_payload(path=path, payload=payload)

    def poll_service_activations(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_bridge_payload(path=SERVICE_ACTIVATION_POLL_PATH, payload=payload)

    def poll_notifications(self, *, tenant_id: int = 1,
                           since: str = "") -> dict[str, Any]:
        """Fetch notifications the panel has queued for this instance.

        Returns the parsed bridge response (never raises); the items live under
        ``notifications`` (or ``items``). ``since`` is an opaque cursor the panel
        understands (last acked ref/seq) so it only returns fresh ones.
        """
        payload = self._license_check_payload(
            {"tenant_id": int(tenant_id), "since": str(since or "")})
        return self._post_bridge_payload(path=NOTIFICATIONS_POLL_PATH, payload=payload)

    def ack_notifications(self, *, refs: list[str],
                          tenant_id: int = 1) -> dict[str, Any]:
        """Acknowledge stored notification refs so the panel stops re-sending."""
        payload = self._license_check_payload(
            {"tenant_id": int(tenant_id), "acked_refs": [str(r) for r in refs]})
        return self._post_bridge_payload(path=NOTIFICATIONS_ACK_PATH, payload=payload)

    def post_support_ticket(self, *, tenant_id: int = 1, subject: str,
                            body: str, category: str = "general",
                            priority: str = "normal",
                            local_ref: str = "") -> dict[str, Any]:
        """Forward a customer support ticket/complaint to the licensing panel."""
        payload = self._license_check_payload({
            "tenant_id": int(tenant_id),
            "subject": str(subject or ""),
            "body": str(body or ""),
            "category": str(category or "general"),
            "priority": str(priority or "normal"),
            "local_ref": str(local_ref or ""),
        })
        return self._post_bridge_payload(path=CUSTOMER_SUPPORT_TICKET_PATH, payload=payload)

    # ── Central FCM push (thin forwarder) ───────────────────────────────────
    # All three are signed bridge POSTs mirroring the WhatsApp/notifications
    # helpers. The panel owns the Firebase credential + device-token store and
    # performs the real FCM send; this module never holds the key. Each returns
    # the parsed JSON dict and never raises.
    def register_push_token(self, *, token: str, platform: str = "",
                            app_version: str = "",
                            external_user_id: str = "") -> dict[str, Any]:
        """Forward the global app's FCM token registration to the panel."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=PUSH_REGISTER_TOKEN_PATH,
            payload=self._license_check_payload({
                "token": str(token or "").strip(),
                "platform": str(platform or "").strip(),
                "app_version": str(app_version or "").strip(),
                "external_user_id": str(external_user_id or "").strip(),
            }),
        )

    def unregister_push_token(self, *, token: str) -> dict[str, Any]:
        """Forward the global app's FCM token removal (logout) to the panel."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=PUSH_UNREGISTER_TOKEN_PATH,
            payload=self._license_check_payload({"token": str(token or "").strip()}),
        )

    def forward_push(self, *, title: str, body: str, link: str = "",
                     ntype: str = "system", data: dict[str, Any] | None = None,
                     mode: str = "async") -> dict[str, Any]:
        """Forward a notification's push request to the panel for FCM dispatch.

        ``mode="sync"`` asks the panel to dispatch inline and return the result
        (used by «أرسل إشعار تجريبي» so the owner sees it); the default async
        mode queues off-thread on the panel so a normal notification never
        blocks on the FCM network."""
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {"ok": False, "status": "https_required"}
        return self._post_bridge_payload(
            path=PUSH_SEND_PATH,
            payload=self._license_check_payload({
                "title": str(title or ""),
                "body": str(body or ""),
                "link": str(link or ""),
                "type": str(ntype or "system"),
                "data": dict(data or {}),
                "mode": str(mode or "async"),
            }),
        )

    def post_service_activation_status(self, *, reference: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe_reference = urllib.parse.quote(str(reference), safe="")
        path = SERVICE_ACTIVATION_STATUS_PATH_TEMPLATE.format(reference=safe_reference)
        return self._post_bridge_payload(path=path, payload=payload)

    def post_bridge_token_report(
        self, *, token: str, issued_at: str | None = None
    ) -> dict[str, Any]:
        """Report a locally-generated bridge token to the panel.

        The raw ``token`` value is sent over HTTPS (required) inside the
        signed envelope and MUST NOT be logged by the caller.  The panel
        responds with ``{ok: true, seq: "<version>"}`` on acceptance.
        """
        if not str(self.config.base_url or "").lower().startswith("https://"):
            return {
                "ok": False,
                "status": "https_required",
                "error": {
                    "code": "https_required",
                    "message": "Bridge token report requires an HTTPS panel URL.",
                },
            }
        now = _utcnow()
        payload = self._license_check_payload(
            {
                "bridge_token": str(token or ""),
                "bridge_token_issued_at": str(issued_at or now),
                "bridge_token_source": "customer",
            }
        )
        return self._post_bridge_payload(path=BRIDGE_TOKEN_REPORT_PATH, payload=payload)

    def _post_bridge_payload(
        self, *, path: str, payload: dict[str, Any], sanitize: bool = True
    ) -> dict[str, Any]:
        """POST a signed payload and return the parsed response. Never raises.

        ``sanitize`` masks sensitive keys (the default, safe for snapshots/UI).
        Tunnel request/sync set it to False so the service can read the
        one-time SSTP credential — that caller must NOT persist the raw secret.
        """
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
            "response": response if not sanitize else sanitize_bridge_payload(response),
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
            # Raw unsanitized response — for immediate transient processing only;
            # do NOT persist or log this value.  Used by sync_runtime_contract_once
            # to extract secrets (like bridge_token) before the sanitized snapshot
            # masks them.
            "_raw_response": response,
            "state": fallback_state(),
            "snapshot": snapshot,
        }

    def _headers(self) -> dict[str, str]:
        """Outbound headers — SIMPLE_LINK bearer-in-body only.

        Per the panel's Simple_Link contract the ``license_key`` is the
        single bearer secret, carried in BOTH the Authorization header
        AND the request body (the body copy is authoritative — reverse
        proxies sometimes strip custom request headers).
        """
        headers = {
            "Accept": "application/json",
            "User-Agent": "HobeRadius-AdminBridge/1",
        }
        if self.config.license_key:
            headers["Authorization"] = "Bearer " + self.config.license_key
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
    bridge_token = payload.get("bridge_token")
    if bridge_token is not None and not isinstance(bridge_token, dict):
        problems.append("bridge_token must be an object when present")
    if isinstance(bridge_token, dict) and "token" in bridge_token:
        if not isinstance(bridge_token["token"], str):
            problems.append("bridge_token.token must be a string")
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
    """Return a canonical status string from a panel response payload.

    Always returns the raw status (lowercased, stripped) so callers receive
    the exact value from the panel rather than losing it by mapping to "unknown".
    The only special case is a missing/empty status on a success response.
    """
    status = str(payload.get("status") or "").strip().lower()
    if not status:
        return "ok" if payload.get("ok") is True else "unknown"
    return status


def _server_fingerprint() -> str:
    """Compute the server fingerprint sent to the license panel on every check.

    Priority order (highest → lowest):
    1. HOBERADIUS_SERVER_FINGERPRINT / HOBERADIUS_INSTANCE_FINGERPRINT env var
    2. license_admin_bridge.server_fingerprint  DB setting  (set from the UI)
    3. Stable hash of (INSTANCE_ID | hostname | DB_PATH)

    Using option 1 or 2 guarantees the fingerprint never changes across
    hostname changes, container restarts, or OS reinstalls — eliminating
    the need for manual fingerprint resets in the license panel.
    """
    # 1 — explicit env var (highest priority, always wins)
    explicit = (
        env_settings.env("HOBERADIUS_SERVER_FINGERPRINT")
        or env_settings.env("HOBERADIUS_INSTANCE_FINGERPRINT")
        or ""
    ).strip()
    if explicit:
        return explicit[:255]

    # 2 — DB setting written via the admin UI (no terminal needed)
    db_fingerprint = bridge_setting("license_admin_bridge.server_fingerprint", "").strip()
    if db_fingerprint:
        return db_fingerprint[:255]

    # 3 — generate a RANDOM, stable fingerprint once and persist it, so it never
    # changes (no hostname/path drift), needs no manual entry, and never needs a
    # reset. First call mints + saves it; later calls read it back via option 2.
    generated = "hr-" + secrets.token_hex(16)
    try:
        from app.radius.core.tenant import DEFAULT_TENANT_ID
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(DEFAULT_TENANT_ID, "license_admin_bridge.server_fingerprint", generated)
        return generated
    except Exception:  # noqa: BLE001 — DB unavailable: fall back to a stable hash
        seed = "|".join(
            part
            for part in (
                env_settings.env("HOBERADIUS_INSTANCE_ID", "").strip(),
                _hostname(),
                env_settings.env("HOBERADIUS_DB_PATH", "").strip(),
            )
            if part
        ) or (_hostname() or "hoberadius-local-instance")
        return f"hr-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def _hostname() -> str:
    try:
        return socket.gethostname()[:255]
    except OSError:
        return ""


def _module_version() -> str:
    return (
        env_settings.env("HOBERADIUS_BUILD_SHA")
        or env_settings.env("HOBERADIUS_VERSION")
        or "radius-module"
    )[:80]


def _install_id() -> str:
    return (
        env_settings.env("HOBERADIUS_INSTANCE_ID")
        or env_settings.env("HOBERADIUS_INSTALL_ID")
        or _server_fingerprint()
    )[:120]


def _public_domain() -> str:
    raw = (
        env_settings.env("HOBERADIUS_PUBLIC_URL")
        or env_settings.env("HOBERADIUS_DOMAIN")
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
