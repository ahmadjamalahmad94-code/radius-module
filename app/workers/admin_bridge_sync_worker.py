"""Opt-in live license-admin bridge sync worker."""
from __future__ import annotations

import logging
import os
import threading
import time

from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "admin_bridge_sync_worker"
_started = False
_lock = threading.Lock()


def _interval_seconds() -> int:
    try:
        return max(60, int(os.environ.get("HOBERADIUS_ADMIN_BRIDGE_SYNC_INTERVAL_SECONDS", "300") or 300))
    except ValueError:
        return 300


def _loop(interval: int) -> None:
    _LOG.info("admin bridge sync worker started, polling every %ss", interval)
    while True:
        info = {"interval_sec": interval, "status": "unknown"}
        try:
            result = LicenseAdminRuntimeSyncService().sync_once(tenant_id=1)
            identity_result = _maybe_sync_identity()
            info = {
                "interval_sec": interval,
                "ok": bool(result.get("ok")),
                "status": result.get("status") or "unknown",
                "license_active": result.get("license_active"),
                "capacity_snapshot_id": result.get("capacity_snapshot_id"),
                "identity_ok": identity_result.get("ok") if identity_result else None,
                "identity_synced_count": identity_result.get("synced_count") if identity_result else None,
            }
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("admin bridge sync worker tick failed")
            info = {"interval_sec": interval, "ok": False, "status": "error", "error": str(exc)}
        beat(_NAME, info=info)
        time.sleep(interval)


def _maybe_sync_identity() -> dict | None:
    if os.environ.get("HOBERADIUS_ADMIN_IDENTITY_SYNC_ENABLED") != "1":
        return None
    from app.radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

    return LicenseAdminIdentitySyncService().sync_once(tenant_id=1)


def start_admin_bridge_sync_worker() -> None:
    """Start periodic license approval sync when explicitly enabled."""
    global _started
    if os.environ.get("HOBERADIUS_NO_WORKER") == "1":
        return
    if os.environ.get("HOBERADIUS_ADMIN_BRIDGE_WORKER") != "1":
        return
    with _lock:
        if _started:
            return
        interval = _interval_seconds()
        thread = threading.Thread(
            target=_loop,
            args=(interval,),
            daemon=True,
            name="hr-admin-bridge-sync",
        )
        thread.start()
        _started = True
