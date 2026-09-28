"""State shared by every process of one install (migration 177).

The panel runs several gunicorn worker processes, the background workers run
in their own process and FreeRADIUS auth has its own gunicorn — a module-level
dict is private to ONE of them. What must be seen by all (a login lockout, the
router live state, a job's progress, an exclusive operation) lives here, in
small SQLite tables, instead.

Every helper degrades to process-local memory when the tables are missing
(a unit test without migrations, a DB that is read-only for a moment): the old
single-process behaviour, never an exception on the request path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .connection import db, transaction

_mem_lock = threading.Lock()
_mem_rate: dict[tuple[str, str], list[float]] = {}
_mem_kv: dict[tuple[str, str], tuple[Any, float]] = {}
_mem_locks: dict[str, tuple[str, float]] = {}

HOLDER = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _missing_table(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc)


# ─────────────── sliding-window events (lockouts, rate limits) ───────────────

def rate_hit(scope: str, key: str, *, window: float, now: Optional[float] = None) -> int:
    """Record one event and return how many fall inside ``window`` seconds."""
    ts = time.time() if now is None else float(now)
    cutoff = ts - float(window)
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM rate_events WHERE scope = ? AND key = ? AND ts < ?",
                         (scope, key, cutoff))
            conn.execute("INSERT INTO rate_events(scope, key, ts) VALUES(?,?,?)",
                         (scope, key, ts))
            row = conn.execute("SELECT COUNT(*) FROM rate_events WHERE scope = ? AND key = ?",
                               (scope, key)).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        hits = [t for t in _mem_rate.get((scope, key), []) if t >= cutoff]
        hits.append(ts)
        _mem_rate[(scope, key)] = hits
        return len(hits)


def rate_events(scope: str, key: str, *, window: float,
                now: Optional[float] = None) -> list[float]:
    """Timestamps of the events inside ``window`` (oldest first)."""
    ts = time.time() if now is None else float(now)
    cutoff = ts - float(window)
    try:
        rows = db().execute(
            "SELECT ts FROM rate_events WHERE scope = ? AND key = ? AND ts >= ? ORDER BY ts",
            (scope, key, cutoff)).fetchall()
        return [float(r[0]) for r in rows]
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        return sorted(t for t in _mem_rate.get((scope, key), []) if t >= cutoff)


def rate_count(scope: str, key: str, *, window: float, now: Optional[float] = None) -> int:
    return len(rate_events(scope, key, window=window, now=now))


def rate_clear(scope: str, key: Optional[str] = None) -> None:
    """Forget a key's events (success resets the counter); key=None → scope."""
    try:
        with transaction() as conn:
            if key is None:
                conn.execute("DELETE FROM rate_events WHERE scope = ?", (scope,))
            else:
                conn.execute("DELETE FROM rate_events WHERE scope = ? AND key = ?",
                             (scope, key))
        return
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        for k in [k for k in _mem_rate if k[0] == scope and (key is None or k[1] == key)]:
            _mem_rate.pop(k, None)


# ─────────────── keyed blobs with expiry ───────────────

def _enc(value: Any) -> bytes:
    if isinstance(value, bytes):
        return b"b:" + value
    return b"j:" + json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def _dec(raw: Any) -> Any:
    if raw is None:
        return None
    raw = bytes(raw)
    if raw.startswith(b"b:"):
        return raw[2:]
    if raw.startswith(b"j:"):
        return json.loads(raw[2:].decode("utf-8"))
    return raw


def kv_put(ns: str, key: str, value: Any, *, ttl: float) -> None:
    exp = time.time() + float(ttl)
    try:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO shared_kv(ns, key, value, expires_at) VALUES(?,?,?,?) "
                "ON CONFLICT(ns, key) DO UPDATE SET value = excluded.value, "
                "expires_at = excluded.expires_at", (ns, key, _enc(value), exp))
        return
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        _mem_kv[(ns, key)] = (value, exp)


def kv_put_if_absent(ns: str, key: str, value: Any, *, ttl: float) -> bool:
    """Store only if no LIVE value exists; True when this call stored it."""
    now = time.time()
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM shared_kv WHERE ns = ? AND key = ? AND expires_at < ?",
                         (ns, key, now))
            cur = conn.execute(
                "INSERT OR IGNORE INTO shared_kv(ns, key, value, expires_at) VALUES(?,?,?,?)",
                (ns, key, _enc(value), now + float(ttl)))
            return (cur.rowcount or 0) > 0
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        cur = _mem_kv.get((ns, key))
        if cur and cur[1] >= now:
            return False
        _mem_kv[(ns, key)] = (value, now + float(ttl))
        return True


def kv_get(ns: str, key: str) -> Any:
    now = time.time()
    try:
        row = db().execute("SELECT value FROM shared_kv WHERE ns = ? AND key = ? "
                           "AND expires_at >= ?", (ns, key, now)).fetchone()
        return _dec(row[0]) if row else None
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        cur = _mem_kv.get((ns, key))
        return cur[0] if cur and cur[1] >= now else None


def kv_pop(ns: str, key: str) -> Any:
    """Read AND delete (one-time tokens). None when missing/expired."""
    now = time.time()
    try:
        with transaction() as conn:
            row = conn.execute("SELECT value, expires_at FROM shared_kv WHERE ns = ? AND key = ?",
                               (ns, key)).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM shared_kv WHERE ns = ? AND key = ?", (ns, key))
        return _dec(row[0]) if float(row[1]) >= now else None
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        cur = _mem_kv.pop((ns, key), None)
        return cur[0] if cur and cur[1] >= now else None


def kv_delete(ns: str, key: str) -> None:
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM shared_kv WHERE ns = ? AND key = ?", (ns, key))
        return
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        _mem_kv.pop((ns, key), None)


def kv_prune() -> None:
    """Drop expired rows (called by the WAL/maintenance worker)."""
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM shared_kv WHERE expires_at < ?", (time.time(),))
            conn.execute("DELETE FROM rate_events WHERE ts < ?", (time.time() - 86400,))
            conn.execute("DELETE FROM op_locks WHERE expires_at < ?", (time.time(),))
    except sqlite3.Error:
        pass


# ─────────────── exclusive operation leases ───────────────

class OperationBusy(RuntimeError):
    """Another process (or thread) holds the lease."""


def try_lock(name: str, *, ttl: float, holder: Optional[str] = None) -> Optional[str]:
    """Take the lease ``name`` for ``ttl`` seconds; returns the holder token or
    None when someone else holds a live lease."""
    token = holder or f"{HOLDER}-{uuid.uuid4().hex[:6]}"
    now = time.time()
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM op_locks WHERE name = ? AND expires_at < ?", (name, now))
            cur = conn.execute("INSERT OR IGNORE INTO op_locks(name, holder, expires_at) "
                               "VALUES(?,?,?)", (name, token, now + float(ttl)))
            return token if (cur.rowcount or 0) > 0 else None
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        cur = _mem_locks.get(name)
        if cur and cur[1] >= now:
            return None
        _mem_locks[name] = (token, now + float(ttl))
        return token


def unlock(name: str, token: str) -> None:
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM op_locks WHERE name = ? AND holder = ?", (name, token))
        return
    except sqlite3.Error as exc:
        if not _missing_table(exc):
            raise
    with _mem_lock:
        cur = _mem_locks.get(name)
        if cur and cur[0] == token:
            _mem_locks.pop(name, None)


@contextmanager
def op_lock(name: str, *, ttl: float = 3600) -> Iterator[str]:
    """``with op_lock("data_reset"):`` — exclusive across ALL processes.
    Raises OperationBusy when held elsewhere. The TTL frees a lease whose
    process died mid-operation."""
    token = try_lock(name, ttl=ttl)
    if token is None:
        raise OperationBusy(name)
    try:
        yield token
    finally:
        try:
            unlock(name, token)
        except Exception:  # noqa: BLE001 — the TTL frees it anyway
            pass


def reset_memory() -> None:
    """Test hook: forget the process-local fallbacks."""
    with _mem_lock:
        _mem_rate.clear()
        _mem_kv.clear()
        _mem_locks.clear()


class SharedOpLock:
    """Drop-in for a module-level ``threading.Lock`` guarding an exclusive
    admin operation (``acquire(blocking=False)`` / ``release()``), but held
    across ALL processes through ``op_locks``. ``ttl`` frees the lease of a
    process that died mid-operation."""

    def __init__(self, name: str, *, ttl: float = 7200):
        self.name = name
        self.ttl = ttl
        self._local = threading.local()

    def acquire(self, blocking: bool = False) -> bool:  # noqa: ARG002 — never blocks
        token = try_lock(self.name, ttl=self.ttl)
        if token is None:
            return False
        self._local.token = token
        return True

    def release(self) -> None:
        token = getattr(self._local, "token", None)
        if token:
            self._local.token = None
            try:
                unlock(self.name, token)
            except Exception:  # noqa: BLE001 — the TTL frees it anyway
                pass


@contextmanager
def wait_op_lock(name: str, *, ttl: float = 1800, poll: float = 1.0,
                 timeout: Optional[float] = None) -> Iterator[str]:
    """Like ``op_lock`` but WAITS (polling) for its turn — a queue shared by
    every process (e.g. one PDF export at a time on the whole install)."""
    deadline = None if timeout is None else time.time() + float(timeout)
    while True:
        token = try_lock(name, ttl=ttl)
        if token is not None:
            break
        if deadline is not None and time.time() >= deadline:
            raise OperationBusy(name)
        time.sleep(poll)
    try:
        yield token
    finally:
        try:
            unlock(name, token)
        except Exception:  # noqa: BLE001
            pass
