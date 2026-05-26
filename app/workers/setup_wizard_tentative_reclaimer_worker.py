"""setup_wizard_tentative_reclaimer_worker — periodic janitor.

Sweeps `router_provisioning_registry` every N seconds for rows
whose `tentative_expires_at` is in the past. Each expired row
gets its IP allocation released, its hr-peer-*.conf file
deleted from peers.d, and a severity=critical audit_log entry
written.

PERMANENT_STATES (vpn_verified, fully_onboarded, etc.) are
immune — the janitor never touches successfully-provisioned
routers, only the leftover tentative attempts.

Started once from `_start_workers` in `app/__init__.py`.
Interval: 300 seconds (5 min). Configurable via
`HOBERADIUS_WIZARD_RECLAIM_INTERVAL_SEC`.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat


_LOG = logging.getLogger(__name__)
_NAME = "setup_wizard_tentative_reclaimer"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL_SEC = 300.0  # 5 minutes
_INTERVAL_ENV = "HOBERADIUS_WIZARD_RECLAIM_INTERVAL_SEC"


def _interval() -> float:
    try:
        raw = float(os.environ.get(_INTERVAL_ENV, _DEFAULT_INTERVAL_SEC))
    except (TypeError, ValueError):
        raw = _DEFAULT_INTERVAL_SEC
    return max(30.0, min(3600.0, raw))


def _run_loop(*, interval_sec: float, flask_app=None) -> None:
    _LOG.info(
        "setup_wizard_tentative_reclaimer started, interval=%.1fs",
        interval_sec,
    )
    # The reclaimer service needs DB access which is set up by
    # Flask's app context — pull it in at tick time, not import
    # time, so a failure inside the service doesn't prevent the
    # worker from starting.
    from app.radius.services.setup_wizard_tentative_reclaimer import (
        SetupWizardTentativeReclaimer,
    )
    from app.radius.db.connection import db as _db_factory

    while True:
        scanned = 0
        reclaimed_count = 0
        try:
            ctx = (
                flask_app.app_context() if flask_app is not None
                else _noop_ctx()
            )
            with ctx:
                # Sweep across every tenant in one go. The
                # reclaimer's find_expired() filters out
                # permanent rows on its own.
                tenants = _all_tenant_ids(_db_factory)
                svc = SetupWizardTentativeReclaimer()
                for tid in tenants:
                    result = svc.reclaim_all_expired(
                        tenant_id=tid, actor="janitor",
                    )
                    scanned += int(result.get("scanned") or 0)
                    reclaimed_count += int(
                        result.get("reclaimed_count") or 0
                    )
                if reclaimed_count:
                    _LOG.info(
                        "tentative reclaimer: scanned=%d reclaimed=%d",
                        scanned, reclaimed_count,
                    )
        except Exception:  # noqa: BLE001
            _LOG.exception("tentative reclaimer tick failed")
        beat(_NAME, info={
            "interval_sec": interval_sec,
            "last_scanned": scanned,
            "last_reclaimed": reclaimed_count,
        })
        time.sleep(interval_sec)


def _all_tenant_ids(db_factory) -> list[int]:
    rows = db_factory().execute(
        "SELECT id FROM tenants ORDER BY id ASC",
    ).fetchall()
    return [int(r["id"]) for r in rows]


class _noop_ctx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def start_setup_wizard_tentative_reclaimer(
    *, flask_app=None,
    interval_sec: float | None = None,
) -> None:
    global _started
    with _started_lock:
        if _started:
            return
        sec = interval_sec if interval_sec is not None else _interval()
        t = threading.Thread(
            target=_run_loop,
            kwargs={"interval_sec": sec, "flask_app": flask_app},
            daemon=True,
            name="hr-tentative-reclaimer",
        )
        t.start()
        _started = True
