"""
SQLite connection manager — thread-safe، WAL mode، Foreign Keys ON، row_factory=dict-like.

استخدام:
    with transaction() as conn:
        conn.execute("INSERT INTO ...", (...,))

    rows = db().execute("SELECT * FROM ...").fetchall()
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

_LOG = logging.getLogger(__name__)

_local = threading.local()
_db_path: Optional[str] = None
_init_lock = threading.Lock()


def _resolve_db_path() -> str:
    env = os.environ.get("HOBERADIUS_DB_PATH")
    if env:
        return env
    here = Path(__file__).resolve().parent.parent.parent.parent  # radius-module/
    return str(here / "instance" / "hoberadius.db")


def db_path() -> str:
    global _db_path
    env_path = os.environ.get("HOBERADIUS_DB_PATH")
    if env_path and _db_path != env_path:
        with _init_lock:
            if _db_path != env_path:
                close_thread_conn()
                _db_path = env_path
                Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    if _db_path is None:
        with _init_lock:
            if _db_path is None:
                _db_path = _resolve_db_path()
                Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    return _db_path


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), isolation_level=None, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")   # ينتظر حتى 30s قبل DB locked
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def get_conn() -> sqlite3.Connection:
    """يُرجع connection خاص بالـ thread الحالي (مُعاد استخدامه)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _make_conn()
        _local.conn = conn
    return conn


def db() -> sqlite3.Connection:
    """alias مختصر."""
    return get_conn()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """transaction سياقي. rollback تلقائي عند الاستثناء."""
    conn = get_conn()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def checkpoint_wal() -> bool:
    """Flush the WAL into the main database file (``PRAGMA wal_checkpoint``).

    The DB runs in WAL journal mode, and the default auto-checkpoint only fires
    after ~1000 dirty pages. Low-volume writes (e.g. a router management-tunnel
    ``radcheck``/``radreply`` row) can therefore sit in the ``-wal`` sidecar for
    a long time and never reach the main ``.db`` file.

    A SEPARATE process reading the same database — notably the FreeRADIUS
    container's ``rlm_sql_sqlite``, which opens ``/data/hoberadius.db`` as a
    different OS user — may not see those uncheckpointed rows (it reads the main
    file and may be unable to attach the app-owned ``-wal``/``-shm``). The
    symptom is FreeRADIUS ``sql`` returning *notfound* for a ``rtr-*`` account
    whose row plainly exists when queried through the app.

    Calling this right after a tunnel-account write forces the rows into the
    main file immediately, so the FreeRADIUS reader sees them regardless of WAL
    attach/permission quirks. TRUNCATE is best-effort: a concurrent reader can
    downgrade it to a partial checkpoint, but the committed frames are still
    copied into the main DB (which is all we need for visibility).

    Returns True on a clean checkpoint, False otherwise (never raises)."""
    try:
        conn = get_conn()
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # row = (busy, log_frames, checkpointed_frames); busy==0 => fully done.
        return bool(row is not None and row[0] == 0)
    except sqlite3.Error as exc:
        _LOG.warning("wal_checkpoint failed: %s", exc)
        return False


def close_thread_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None


def reset_for_tests(path: Optional[str] = None) -> None:
    """للاستخدام في pytest فقط."""
    global _db_path
    close_thread_conn()
    if path:
        _db_path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    else:
        _db_path = None
