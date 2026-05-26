"""setup_wizard_radius_reconciler_worker — periodic drift
detector for FreeRADIUS clients.conf wizard files.

The wizard writes one `wizard-run-<id>.conf` per provisioned
router, with the per-run shared secret. The
generate_unified_script call writes it atomically with the
secret generation (postmortem #20), and write_client_for_run
purges stale files for the same ipaddr. Together those two
make the wizard's primary path safe.

This worker is a SAFETY NET for everything else:

* Manual operator deletions / renames in the directory
* Bugs in future wizard slices that forget to call
  write_client_for_run
* DB restores that bring back state_json entries pointing to
  the same routers but with different secrets
* Stale files left behind from older wizard versions

Every interval (default 300s), it calls
setup_wizard_v3_radius_server_provisioning.reconcile_with_state()
which enforces the three invariants:

  INV-1: every active run (v3_state ∈ {VERIFYING,
         REGISTERING, COMPLETE}) has a matching conf file
         with the right secret.
  INV-2: every conf file corresponds to an active run.
  INV-3: no two conf files share the same ipaddr.

Touched whenever anything changes; the freeradius entrypoint
watcher picks up the reload trigger within ~5 seconds.

Started once from _start_workers in app/__init__.py.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .heartbeat import beat


_LOG = logging.getLogger(__name__)
_NAME = "setup_wizard_radius_reconciler"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL_SEC = 300.0  # 5 minutes
_INTERVAL_ENV = "HOBERADIUS_WIZARD_RADIUS_RECONCILE_INTERVAL_SEC"


def _interval() -> float:
    try:
        raw = float(
            os.environ.get(_INTERVAL_ENV, _DEFAULT_INTERVAL_SEC),
        )
    except (TypeError, ValueError):
        raw = _DEFAULT_INTERVAL_SEC
    return max(30.0, min(3600.0, raw))


def _run_loop(*, interval_sec: float, flask_app=None) -> None:
    _LOG.info(
        "setup_wizard_radius_reconciler started, interval=%.1fs",
        interval_sec,
    )
    from app.radius.services.setup_wizard_v3_radius_server_provisioning import (
        reconcile_with_state,
    )
    from app.radius.db.connection import db as _db_factory

    while True:
        actions: dict = {}
        try:
            ctx = (
                flask_app.app_context()
                if flask_app is not None
                else _NoopCtx()
            )
            with ctx:
                tenants = _all_tenant_ids(_db_factory)
                for tid in tenants:
                    result = reconcile_with_state(
                        tenant_id=int(tid),
                    )
                    actions[tid] = result
                    if (
                        result["rewritten"]
                        or result["deleted"]
                        or result["deduped"]
                    ):
                        _LOG.warning(
                            "tenant=%s radius reconcile: "
                            "rewritten=%s deleted=%s deduped=%s",
                            tid,
                            len(result["rewritten"]),
                            len(result["deleted"]),
                            len(result["deduped"]),
                        )
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "radius reconciler tick failed",
            )
        beat(_NAME, info={
            "interval_sec": interval_sec,
            "last_actions": actions,
        })
        time.sleep(interval_sec)


def _all_tenant_ids(db_factory) -> list[int]:
    rows = db_factory().execute(
        "SELECT id FROM tenants ORDER BY id ASC",
    ).fetchall()
    return [int(r["id"]) for r in rows]


class _NoopCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def start_setup_wizard_radius_reconciler(
    *, flask_app=None, interval_sec: float | None = None,
) -> None:
    global _started
    with _started_lock:
        if _started:
            return
        sec = (
            interval_sec
            if interval_sec is not None
            else _interval()
        )
        t = threading.Thread(
            target=_run_loop,
            kwargs={
                "interval_sec": sec,
                "flask_app": flask_app,
            },
            daemon=True,
            name="hr-radius-reconciler",
        )
        t.start()
        _started = True
