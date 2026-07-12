"""Instance health heartbeat for the V40 admin bridge.

All checks are read-only/best-effort. The service never restarts services,
never shells out, and never touches RADIUS, MikroTik, FreeRADIUS, or CoA live
paths.

يونيو 2026 — provision-on-link contract
=======================================
The panel's ``POST /api/integration/hoberadius/instance-ops/heartbeat`` now
ALSO calls ``provision_on_link(...)``, which idempotently creates the
customer's RADIUS instance + ProxyRealmRoute and mints a fresh shared
secret. The panel reads these provision fields from the heartbeat body:

    license_key       — bearer-in-body (already present)
    radius_auth_ip    — this instance's RADIUS server IP (operator-reachable)
    realm             — proxy realm for this customer
    radius_auth_port  — 1812
    radius_acct_port  — 1813

If a fresh ``shared_secret`` is returned in the response, we persist it
locally as ``license_admin_bridge.instance_radius_secret`` so the operator
can paste it into ``clients.conf`` (or future automation can apply it). The
operation is idempotent on the panel side; re-sync never duplicates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.radius.db.connection import db, db_path
from app.radius.services.admin_panel_client import (
    AdminPanelClient,
    AdminBridgeConfig,
    bridge_setting,
    mask_license_key,
    sanitize_bridge_payload,
)

_LOG = logging.getLogger(__name__)

# Standard FreeRADIUS ports the panel uses to wire ProxyRealmRoute.
RADIUS_AUTH_PORT = 1812
RADIUS_ACCT_PORT = 1813

# Settings keys for the provision contract.
SETTING_RADIUS_AUTH_IP = "instance.radius_auth_ip"
SETTING_REALM = "instance.realm"
# Where the panel-minted shared secret lands. Distinct from
# ``license_admin_bridge.shared_secret`` (legacy signed-path credential):
# this one is the FreeRADIUS clients.conf secret for the proxy realm.
SETTING_INSTANCE_RADIUS_SECRET = "license_admin_bridge.instance_radius_secret"

_REALM_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _slugify_realm(value: str) -> str:
    """Normalize an arbitrary string into a safe RADIUS realm.

    Lowercases, replaces unsupported chars with ``-``, strips edges, caps
    length. Used to build a fallback realm from the license-key prefix or
    hostname when the operator hasn't set one explicitly.
    """
    s = (value or "").strip().lower()
    s = _REALM_SAFE_RE.sub("-", s).strip("-.")
    return s[:64] or "default"


def _derive_radius_auth_ip(config: AdminBridgeConfig) -> str:
    """Compute the RADIUS server's reachable IP for the panel's ProxyRealm.

    Priority chain (highest → lowest):
      1) ``instance.radius_auth_ip`` setting (operator override).
      2) ``network.radius_server_ip`` setting (existing customer-facing IP
         used by login-designer / store APIs — same machine).
      3) ``HOBERADIUS_PUBLIC_IP`` env var.
      4) Local hostname → resolved A record (best-effort).
      5) Empty string — the panel will reject the heartbeat with a clear
         error and the operator gets a flash hint to set it.
    """
    override = bridge_setting(SETTING_RADIUS_AUTH_IP, "").strip()
    if override:
        return override
    radius_ip = bridge_setting("network.radius_server_ip", "").strip()
    if radius_ip:
        return radius_ip
    env_ip = (os.environ.get("HOBERADIUS_PUBLIC_IP") or "").strip()
    if env_ip:
        return env_ip
    try:
        return socket.gethostbyname(socket.gethostname()) or ""
    except (socket.gaierror, OSError):
        return ""


def _derive_realm(config: AdminBridgeConfig) -> str:
    """Pick the proxy realm for this customer.

    Priority chain:
      1) ``instance.realm`` setting (operator-supplied).
      2) Slugified first 8 chars of the license key — stable per customer,
         unique enough across the panel, never leaks the full key.
      3) Hostname slug.
      4) Literal ``"default"``.
    """
    override = bridge_setting(SETTING_REALM, "").strip()
    if override:
        return _slugify_realm(override)
    lk = (config.license_key or "").strip()
    if lk:
        # Unique per license key. The previous derivation used lk[:8], which for
        # an HBR-YYYY-XXXX-XXXX-XXXX key is just "HBR-YYYY" (the year) — IDENTICAL
        # for every license issued in the same year. So all customers collapsed
        # onto one realm (e.g. hr-hbr-2026) and the panel rejected the 2nd+ with a
        # UNIQUE(realm) violation on customer_radius_instances. Hash the FULL key:
        # stable per customer, unique across the panel, and never leaks the key.
        digest = hashlib.sha256(lk.encode("utf-8")).hexdigest()[:12]
        return _slugify_realm("hr-" + digest)
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    return _slugify_realm(host) or "default"


def build_provision_fields(config: AdminBridgeConfig) -> dict[str, Any]:
    """Build the provision-contract sub-dict the panel reads in
    ``provision_on_link``: license_key + radius_auth_ip + realm + ports.

    Kept as a standalone helper so the worker, the manual sync route, and
    the rich-heartbeat builder can all share the same field shape.
    """
    return {
        "license_key": config.license_key,
        "radius_auth_ip": _derive_radius_auth_ip(config),
        "realm": _derive_realm(config),
        "radius_auth_port": RADIUS_AUTH_PORT,
        "radius_acct_port": RADIUS_ACCT_PORT,
    }


def store_provisioned_secret(secret: str, *, tenant_id: int = 1) -> str:
    """Persist the panel-minted shared_secret to tenant_settings.

    Returns the stored value (or empty string when blank). Logs only the
    masked form. The operator surfaces the value via the licensing page
    (it's also retrievable for FreeRADIUS clients.conf automation).
    """
    s = str(secret or "").strip()
    if not s:
        return ""
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(int(tenant_id), SETTING_INSTANCE_RADIUS_SECRET, s, by=0)
    _LOG.info(
        "instance radius secret stored (masked=%s)",
        mask_license_key(s),
    )
    return s


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


# Bounded-by-design telemetry: heartbeat attempts used to be appended on every
# cycle (~1/minute) with no cap, growing to ~21MB. The writer now keeps only the
# latest N per tenant on each insert, so the table can never grow unbounded. The
# dashboard only ever reads latest_attempt() (a single row); the rest is recent
# diagnostic history. Override the cap with HOBERADIUS_RETENTION_LICENSE_ADMIN_
# HEARTBEAT_ATTEMPTS_KEEP (0 disables the write-time trim; the daily retention
# worker still age-prunes as a backstop).
HEARTBEAT_KEEP_PER_TENANT = 500


def _heartbeat_keep_per_tenant() -> int:
    raw = os.environ.get("HOBERADIUS_RETENTION_LICENSE_ADMIN_HEARTBEAT_ATTEMPTS_KEEP")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0, int(str(raw).strip()))
        except ValueError:
            pass
    return HEARTBEAT_KEEP_PER_TENANT


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
        # Provision-on-link fields the panel reads when minting the
        # RADIUS instance + ProxyRealmRoute. Merged at the TOP level so
        # the panel's contract matches what's documented in SIMPLE_LINK
        # (license_key + radius_auth_ip + realm + ports as bearer body).
        provision = build_provision_fields(self.config)
        payload = {
            # provision fields first (matches panel's order-independent
            # reader but keeps the body readable).
            "license_key": self.config.license_key,
            "radius_auth_ip": provision["radius_auth_ip"],
            "realm": provision["realm"],
            "radius_auth_port": provision["radius_auth_port"],
            "radius_acct_port": provision["radius_acct_port"],
            # health/operational fields (existing).
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
            # CUSTOMER_RADIUS_TUNNEL_DESIGN §3.1 — wg-radius public key +
            # interface health + applied config fingerprint. The panel reads
            # this to register our pubkey in the proxy peer set and to surface
            # the «secret in sync ✓» chip on the customer page (§6.4).
            "wg_radius": self._wg_radius_request_state(warnings),
            # Registered-inventory snapshot — the panel's licensing usage bars
            # («أجهزة NAS», «المشتركون») read these to show REAL registered
            # entity counts, NOT accounting/history and NOT the admin roster.
            # ``nas_count`` = COUNT(nas_devices) = the routers/AP records the
            # «أجهزة الشبكة/NAS» page manages; it is radacct-independent, so an
            # imported accounting history can never inflate it (see
            # license_admin_usage_metering + tests). Never raises.
            "inventory": self._inventory_snapshot(tenant_id, warnings),
            "warnings": warnings,
            "errors": [],
        }
        # If we couldn't derive a radius_auth_ip, warn the operator —
        # the panel will reject without it and we want a clear signal.
        if not provision["radius_auth_ip"]:
            warnings.append("radius_auth_ip_missing")
        payload["idempotency_key"] = self.idempotency_key(tenant_id=tenant_id, payload=payload)
        return sanitize_bridge_payload(payload)

    def _inventory_snapshot(self, tenant_id: int, warnings: list[str]) -> dict[str, Any]:
        """Registered-entity counts for the panel's licensing usage bars.

        Reuses :class:`UsageMeteringService` — the single source of truth for
        "how many REAL things exist here". Crucially ``nas_count`` counts the
        ``nas_devices`` table (registered routers/APs), which is fully
        independent of ``radacct``: an imported accounting history with many
        distinct ``nasipaddress`` rows contributes ZERO to this number.

        Best-effort: any failure yields an empty snapshot and a warning rather
        than breaking the heartbeat. The panel treats a missing/empty inventory
        as "no fresh report yet" and simply keeps the previous value.
        """
        try:
            from app.radius.services.license_admin_usage_metering import (
                UsageMeteringService,
            )

            metrics = UsageMeteringService().collect_metrics(tenant_id=tenant_id)
            return {
                "nas_count": int(metrics.get("nas_count") or 0),
                "routers_count": int(metrics.get("routers_count") or 0),
                "subscribers_total": int(metrics.get("subscribers_total") or 0),
                "subscribers_active": int(metrics.get("subscribers_active") or 0),
                "cards_generated_total": int(metrics.get("cards_generated_total") or 0),
                "active_cards": int(metrics.get("active_cards") or 0),
                "profiles_plans_count": int(metrics.get("profiles_plans_count") or 0),
                "admins_count": int(metrics.get("admins_count") or 0),
            }
        except Exception as exc:  # noqa: BLE001 — inventory must never break the heartbeat
            warnings.append(f"inventory_snapshot_failed:{type(exc).__name__}")
            return {}

    def _wg_radius_request_state(self, warnings: list[str]) -> dict[str, Any]:
        """Collect the wg-radius heartbeat block. Never raises — a failure here
        must NEVER take down the heartbeat itself; the panel just sees an
        empty pubkey and the tunnel stays in «بانتظار التقارب»."""
        try:
            from .proxy_tunnel_manager import ProxyTunnelManager
            return ProxyTunnelManager().collect_request_state()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"wg_radius_state_failed:{type(exc).__name__}")
            return {
                "public_key": "",
                "interface_up": False,
                "tunnel_ip": "",
                "last_handshake_age_s": None,
                "freeradius_proxy_client_present": False,
                "config_fingerprint": "",
            }

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

        # ── COEXISTENCE NOTE ──────────────────────────────────────────
        # Two independent things happen on every successful heartbeat
        # response; both must survive together (do NOT drop either):
        #
        #   1. provision-on-link (this branch): the panel's
        #      provision_on_link flow returns shared_secret in the response
        #      when it (re-)mints the customer's RADIUS instance + the
        #      ProxyRealmRoute. We persist it locally so the operator can
        #      paste it into clients.conf (or future automation applies it).
        #
        #   2. radius_tunnel (CUSTOMER_RADIUS_TUNNEL_DESIGN §3.2 / §6.2,
        #      merged via feat/customer-radius-tunnel-client): the panel
        #      returns a `radius_tunnel` block; we hand it to the tunnel
        #      manager, which (re)writes wg-radius.conf + the FreeRADIUS
        #      proxy-client.conf and is idempotent by SHA256 fingerprint.
        #
        # Both branches read from the SAME `result` dict via independent
        # keys (provision-on-link reads `_raw_response`; tunnel reads
        # `response.radius_tunnel`) so they can run sequentially without
        # touching each other.  Neither path raises into the heartbeat
        # flow — a failure in one leaves the other intact.
        # ──────────────────────────────────────────────────────────────

        # (1) provision-on-link — capture & persist the panel-minted secret
        provisioned_secret = ""
        if result.get("ok"):
            # CRITICAL: use the RAW response, not the sanitized one. The
            # ``shared_secret`` key is in ``SENSITIVE_KEYS`` so the public
            # ``response`` field carries it MASKED (``fres…aaaa``). The
            # transport returns the raw object under ``_raw_response`` for
            # exactly this kind of one-shot extraction. We MUST NOT persist
            # or log the raw object — only pull the secret and drop it.
            resp = result.get("_raw_response") or {}
            if isinstance(resp, dict):
                # ``shared_secret`` may appear top-level or nested under
                # ``provision``. Both shapes accepted (panel may evolve).
                candidate = resp.get("shared_secret")
                if not candidate:
                    provision_node = resp.get("provision")
                    if isinstance(provision_node, dict):
                        candidate = provision_node.get("shared_secret")
                if candidate:
                    try:
                        provisioned_secret = store_provisioned_secret(
                            str(candidate), tenant_id=tenant_id,
                        )
                    except Exception:  # noqa: BLE001
                        _LOG.warning(
                            "failed to persist provisioned shared_secret",
                            exc_info=True,
                        )

        # (2) radius_tunnel — bring up wg-radius + write proxy-client.conf
        tunnel_step: dict[str, Any] = {}
        try:
            resp_block = result.get("response") or {}
            radius_tunnel = resp_block.get("radius_tunnel") if isinstance(resp_block, dict) else None
            if radius_tunnel is not None:
                from .proxy_tunnel_manager import ProxyTunnelManager
                tunnel_step = ProxyTunnelManager().apply_response(radius_tunnel).as_dict()
        except Exception as exc:  # noqa: BLE001
            tunnel_step = {"ok": False, "reason": f"apply_failed:{type(exc).__name__}"}

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
        out = {
            "ok": bool(result.get("ok")),
            "dry_run": False,
            "status": attempt.get("status") or status,
            "payload": payload,
            "attempt": attempt,
            "response": result.get("response") or {},
            "error": result.get("error") or {},
            # Surface the tunnel manager's outcome so workers/UI can show
            # actions=[...] / warnings=[...] / fingerprint without re-applying.
            "radius_tunnel_step": tunnel_step,
        }
        if provisioned_secret:
            out["provisioned_secret_masked"] = mask_license_key(provisioned_secret)
            out["provisioned"] = True
        return out

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
            new_id = int(cur.lastrowid)
            self._trim_tenant(int(attempt.tenant_id))
            return self.get_attempt(new_id) or {}
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

    def _trim_tenant(self, tenant_id: int) -> int:
        """Keep only the latest N heartbeat attempts for this tenant; delete the
        rest. Index-backed (ix_…_latest), O(keep) per write. ``keep<=0`` disables
        the trim. Never raises — a trim failure must not break a heartbeat send."""
        keep = _heartbeat_keep_per_tenant()
        if keep <= 0:
            return 0
        try:
            cur = db().execute(
                """
                DELETE FROM license_admin_heartbeat_attempts
                WHERE tenant_id = ?
                  AND id < (
                    SELECT MIN(keep_id) FROM (
                      SELECT id AS keep_id FROM license_admin_heartbeat_attempts
                      WHERE tenant_id = ? ORDER BY id DESC LIMIT ?
                    )
                  )
                """,
                (tenant_id, tenant_id, keep),
            )
            return int(cur.rowcount or 0)
        except Exception:  # noqa: BLE001 — trimming must never break a heartbeat
            return 0

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
