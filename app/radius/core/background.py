"""Post-commit side effects off the request thread.

Re-test R01/R12 (2026-09-29): a balance top-up took p95 5.4 s and a web create
up to 21 s although the database work is a few milliseconds. The request
thread waited on the network after COMMIT: a CoA rate push to the subscriber's
NAS (5 s socket timeout when the router does not answer on 3799) and the
notification fan-out of the ``account.*`` webhook event (SMS / WhatsApp /
Telegram HTTP calls). Neither result is shown to the operator, so they now run
on a small pool of long-lived daemon threads that carry the app context and
the tenant.

A pool, not a thread per call: a fresh thread opens a fresh SQLite connection,
and the first statement on a new connection parses the whole schema (hundreds
of tables) — measured 120–450 ms added to every request. Pool threads keep
their thread-local connection.

Under pytest (``PYTEST_CURRENT_TEST``) or with ``HOBERADIUS_SYNC_SIDE_EFFECTS=1``
the function runs inline, so tests keep asserting the effect right after the
call.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

_LOG = logging.getLogger(__name__)

_POOL_SIZE = 4
_pool: Optional[ThreadPoolExecutor] = None
_pool_pid: Optional[int] = None
_pool_lock = threading.Lock()


def run_inline() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")
                or os.environ.get("HOBERADIUS_SYNC_SIDE_EFFECTS") == "1")


def _executor() -> ThreadPoolExecutor:
    """One pool per process (re-created after a fork — gunicorn workers)."""
    global _pool, _pool_pid
    pid = os.getpid()
    if _pool is None or _pool_pid != pid:
        with _pool_lock:
            if _pool is None or _pool_pid != pid:
                _pool = ThreadPoolExecutor(max_workers=_POOL_SIZE,
                                           thread_name_prefix="side-effect")
                _pool_pid = pid
    return _pool


def run_detached(fn: Callable[[], object], *, name: str = "side-effect") -> None:
    """Run ``fn`` after the response path: inline in tests, else on the side
    effect pool with the current app context and ``g.tenant_id``. Never raises."""
    if run_inline():
        try:
            fn()
        except Exception:  # noqa: BLE001 — a side effect never breaks the caller
            _LOG.exception("side effect %s failed", name)
        return
    app = None
    tenant = None
    try:
        from flask import current_app, g, has_app_context
        if has_app_context():
            app = current_app._get_current_object()
            tenant = getattr(g, "tenant_id", None)
    except Exception:  # noqa: BLE001
        app = None

    def _run() -> None:
        try:
            if app is not None:
                with app.app_context():
                    if tenant is not None:
                        from flask import g
                        g.tenant_id = tenant
                    fn()
            else:
                fn()
        except Exception:  # noqa: BLE001
            _LOG.exception("side effect %s failed", name)
        finally:
            try:  # never leave a transaction open on a reused pool connection
                from ..db.connection import release_leaked_transaction
                release_leaked_transaction()
            except Exception:  # noqa: BLE001
                pass

    try:
        _executor().submit(_run)
    except Exception:  # noqa: BLE001 — cannot schedule: do it inline rather than drop it
        try:
            fn()
        except Exception:  # noqa: BLE001
            _LOG.exception("side effect %s failed", name)
