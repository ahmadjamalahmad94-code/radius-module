"""device_fingerprint_worker — periodic DHCP-lease → fingerprint sync.

Every N seconds (default 120s) pulls `/ip/dhcp-server/lease/print` from
every enabled MikroTik router for every active tenant, and upserts the
parsed host-name + DHCP option-60 class-id into the device_fingerprints
table.

Background-only mode is fine: the Card Checker also reads
device_fingerprints synchronously when it renders, so a 2-minute lag is
the worst case after a client first connects to a new network.

Override via env:
    HOBERADIUS_DEVICE_FP_INTERVAL_SEC  (default 120, min 30)

Started once from app/__init__.py:_start_workers().
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "device_fingerprint_worker"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL_SEC = 120


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_DEVICE_FP_INTERVAL_SEC", "")
    try:
        v = int(raw)
        return max(v, 30)  # below 30s is wasteful for DHCP lease churn
    except ValueError:
        return _DEFAULT_INTERVAL_SEC


def _run_loop(*, interval_sec: int) -> None:
    from app.radius.services import device_fingerprint_sync

    _LOG.info("device_fingerprint_worker started, interval=%ds", interval_sec)
    while True:
        per_tenant: dict[str, int] = {}
        try:
            per_tenant = device_fingerprint_sync.sync_all_tenants()
        except Exception:  # noqa: BLE001
            _LOG.exception("device_fingerprint_worker tick failed")
        total = sum(per_tenant.values()) if per_tenant else 0
        beat(_NAME, info={
            "interval_sec": interval_sec,
            "last_macs_seen": total,
            "tenants": len(per_tenant),
        })
        time.sleep(interval_sec)


def start_device_fingerprint_worker() -> None:
    global _started
    with _started_lock:
        if _started:
            return
        interval = _interval_sec()
        t = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": interval},
            daemon=True, name="hr-devfp-worker",
        )
        t.start()
        _started = True
