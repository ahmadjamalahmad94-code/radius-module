"""Opt-in live license-admin bridge sync worker."""
from __future__ import annotations

import logging
import os
import threading
import time

from app.radius.services.admin_panel_client import bridge_flag, bridge_setting
from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "admin_bridge_sync_worker"
_started = False
_lock = threading.Lock()


def _interval_seconds() -> int:
    raw = os.environ.get("HOBERADIUS_ADMIN_BRIDGE_SYNC_INTERVAL_SECONDS")
    if raw is None or raw.strip() == "":
        raw = bridge_setting("license_admin_bridge.sync_interval_seconds", "300")
    try:
        return max(60, int(raw or 300))
    except ValueError:
        return 300


def _loop() -> None:
    _LOG.info("admin bridge sync worker started")
    while True:
        interval = _interval_seconds()
        if not bridge_flag("HOBERADIUS_ADMIN_BRIDGE_WORKER", "license_admin_bridge.worker_enabled"):
            beat(_NAME, info={"interval_sec": interval, "ok": True, "status": "disabled"})
            time.sleep(interval)
            continue
        info = {"interval_sec": interval, "status": "unknown"}
        try:
            result = LicenseAdminRuntimeSyncService().sync_once(tenant_id=1)
            # Report the admin roster BEFORE identity sync so the panel can
            # return fresh super-admin overrides in the same identity response.
            report_result = _maybe_report_admins()
            identity_result = _maybe_sync_identity()
            tunnel_result = _maybe_sync_tunnels()
            bridge_token_result = _maybe_sync_bridge_token()
            info = {
                "interval_sec": interval,
                "ok": bool(result.get("ok")),
                "status": result.get("status") or "unknown",
                "license_active": result.get("license_active"),
                "capacity_snapshot_id": result.get("capacity_snapshot_id"),
                "identity_ok": identity_result.get("ok") if identity_result else None,
                "identity_synced_count": identity_result.get("synced_count") if identity_result else None,
                "admins_reported": report_result.get("reported_count") if report_result else None,
                "super_overrides": identity_result.get("super_overrides") if identity_result else None,
                "tunnels_ok": tunnel_result.get("ok") if tunnel_result else None,
                "bridge_token_ok": bridge_token_result.get("ok") if bridge_token_result else None,
                "bridge_token_action": bridge_token_result.get("action") if bridge_token_result else None,
            }
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("admin bridge sync worker tick failed")
            info = {"interval_sec": interval, "ok": False, "status": "error", "error": str(exc)}
        beat(_NAME, info=info)
        time.sleep(interval)


def _maybe_sync_identity() -> dict | None:
    if not bridge_flag("HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED", "license_admin_bridge.identity_sync_enabled"):
        return None
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    return LicenseAdminIdentitySyncService().sync_once(tenant_id=1)


def _maybe_report_admins() -> dict | None:
    # Ships alongside identity sync (same gate) — the panel needs the roster to
    # compute the super-admin overrides it returns in the identity response.
    if not bridge_flag("HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED", "license_admin_bridge.identity_sync_enabled"):
        return None
    from app.radius.services.license_admin_inventory_report import LicenseAdminInventoryReportService

    return LicenseAdminInventoryReportService().report_once(tenant_id=1)


def _maybe_sync_tunnels() -> dict | None:
    if not bridge_flag("HOBERADIUS_ADMIN_TUNNEL_SYNC_ENABLED", "license_admin_bridge.tunnel_sync_enabled"):
        return None
    from app.radius.services.license_tunnel_bridge import LicenseTunnelBridgeService

    return LicenseTunnelBridgeService().sync_tunnels(tenant_id=1)


def _maybe_sync_bridge_token() -> dict | None:
    if not bridge_flag("HOBERADIUS_ADMIN_BRIDGE_WORKER", "license_admin_bridge.worker_enabled"):
        return None
    try:
        from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService
        return BridgeTokenSyncService().ensure_token_and_report_pending(tenant_id=1)
    except Exception:  # noqa: BLE001
        _LOG.warning("bridge_token sync step failed", exc_info=True)
        return None


def start_admin_bridge_sync_worker() -> None:
    """Start periodic license approval sync when explicitly enabled."""
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1":
        return
    if not bridge_flag("HOBERADIUS_ADMIN_BRIDGE_WORKER", "license_admin_bridge.worker_enabled"):
        return
    with _lock:
        if _started:
            return
        thread = threading.Thread(
            target=_loop,
            daemon=True,
            name="hr-admin-bridge-sync",
        )
        thread.start()
        _started = True
