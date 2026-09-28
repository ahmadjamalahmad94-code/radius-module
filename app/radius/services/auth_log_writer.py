"""auth_log_writer — write-behind batching for ``radpostauth`` (login log).

Stress L01 (2026-09-28): every RADIUS login committed its own radpostauth row
(plus the login-timestamp update). At hundreds of logins/s those one-row
commits queue on SQLite's single writer lock next to the panel's writes;
measured locally, dropping that one commit per login lifted auth throughput
by ~30%. The row is a LOG — nothing in the auth decision reads it — so it is
buffered in memory and a background thread writes the batch in ONE
transaction every FLUSH_INTERVAL_SEC (or at MAX_BATCH rows).

Trade-offs, deliberately accepted for a log table:
  • a row appears ≤ ~0.2 s after the login instead of immediately;
  • a hard kill (SIGKILL/OOM) loses at most the unflushed buffer (normal
    shutdown flushes via atexit);
  • if the DB stays locked, the buffer is capped at MAX_BUFFER rows (oldest
    dropped, counted, logged) — logins are never slowed down by their log.

Synchronous (the old behaviour) under pytest or HOBERADIUS_AUTHLOG_SYNC=1.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time

_LOG = logging.getLogger(__name__)

FLUSH_INTERVAL_SEC = 0.2
MAX_BATCH = 500
MAX_BUFFER = 20000

_INSERT = """
    INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas, calling_station)
    VALUES(?,?,?,?,?,?,?,?)
"""

_buf: list[tuple] = []
_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None
_dropped = 0


def _sync_mode() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")
                or (os.environ.get("HOBERADIUS_AUTHLOG_SYNC") or "").strip()
                in {"1", "true", "yes", "on"})


def _write(rows: list[tuple]) -> None:
    from ..db.connection import transaction
    with transaction() as conn:
        conn.executemany(_INSERT, rows)


def record(row: tuple) -> None:
    """Queue one radpostauth row: (tenant_id, username, pass, reply, authdate,
    class, nas, calling_station). Never raises into the auth path."""
    global _dropped
    if _sync_mode():
        _write([row])
        return
    with _lock:
        if len(_buf) >= MAX_BUFFER:
            del _buf[0]
            _dropped += 1
        _buf.append(row)
        full = len(_buf) >= MAX_BATCH
        _ensure_thread()
    if full:
        _wake.set()


def flush() -> int:
    """Write everything buffered now. Returns the number of rows written."""
    global _dropped
    with _lock:
        rows = _buf[:]
        del _buf[:]
        dropped, _dropped = _dropped, 0
    if dropped:
        _LOG.warning("auth_log_writer: dropped %d radpostauth row(s) — DB stayed "
                     "locked and the buffer hit %d", dropped, MAX_BUFFER)
    written = 0
    for i in range(0, len(rows), MAX_BATCH):
        chunk = rows[i:i + MAX_BATCH]
        try:
            _write(chunk)
            written += len(chunk)
        except Exception:  # noqa: BLE001 — put it back, retry next tick
            _LOG.warning("auth_log_writer: batch of %d not written yet — will "
                         "retry", len(chunk), exc_info=True)
            with _lock:
                _buf[0:0] = rows[i:]
                overflow = len(_buf) - MAX_BUFFER
                if overflow > 0:
                    del _buf[:overflow]
                    _dropped += overflow
            break
    return written


def _run() -> None:
    while True:
        _wake.wait(FLUSH_INTERVAL_SEC)
        _wake.clear()
        try:
            flush()
        except Exception:  # noqa: BLE001 — never kill the writer
            _LOG.exception("auth_log_writer: flush failed")


def _ensure_thread() -> None:
    """Caller holds _lock."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run, daemon=True, name="hr-authlog-writer")
    _thread.start()


def _flush_at_exit() -> None:
    try:
        flush()
    except Exception:  # noqa: BLE001
        pass


atexit.register(_flush_at_exit)
