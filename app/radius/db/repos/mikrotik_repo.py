"""MikroTik configs repo — DEPRECATED.

Phase N3 drops the `mikrotik_configs` table entirely; the
canonical post-Phase-K table is `nas_devices`. Several
background workers (accounting_puller, mt_reconciler,
device_fingerprint_sync) still import this module, so we keep
the API alive but every function is now empty-safe — when the
table is gone it returns `[]` / `None` rather than raising.

This module will be deleted once every caller has been migrated
to `nas_repo` (tracked as Phase N6 in the postmortem). Until
then, the empty-safe wrappers prevent crashes during the
transition.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso


def _table_missing(exc: sqlite3.OperationalError) -> bool:
    """SQLite raises 'no such table' as a generic OperationalError;
    distinguish it from other DB errors so we only swallow the
    one we mean to swallow."""
    return "no such table" in str(exc).lower()


def list_configs(tenant_id: int) -> list[dict]:
    try:
        cur = db().execute(
            "SELECT * FROM mikrotik_configs WHERE tenant_id = ? ORDER BY id",
            (tenant_id,))
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError as exc:
        if _table_missing(exc):
            return []
        raise


def get(tenant_id: int, cfg_id: int) -> Optional[dict]:
    try:
        row = db().execute(
            "SELECT * FROM mikrotik_configs WHERE tenant_id = ? AND id = ?",
            (tenant_id, cfg_id)).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError as exc:
        if _table_missing(exc):
            return None
        raise


def create(tenant_id: int, *, name: str, host: str, username: str, password: str,
           port: int = 8728, use_tls: bool = False, verify_tls: bool = True,
           timeout_sec: int = 10, enabled: bool = True) -> int:
    # Deliberately raises if anyone calls it after N3 — writes
    # should go through the wizard / nas_devices, not here.
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO mikrotik_configs(tenant_id, name, host, port, username, password,
                use_tls, verify_tls, timeout_sec, enabled, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (tenant_id, name, host, port, username, password,
              int(use_tls), int(verify_tls), timeout_sec, int(enabled), now, now))
        return cur.lastrowid


def update(tenant_id: int, cfg_id: int, **changes) -> Optional[dict]:
    allowed = ("name", "host", "port", "username", "password",
               "use_tls", "verify_tls", "timeout_sec", "enabled",
               "last_status", "last_seen_at")
    sets, vals = [], []
    for k, v in changes.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(int(v) if isinstance(v, bool) else v)
    if not sets:
        return get(tenant_id, cfg_id)
    sets.append("updated_at = ?")
    vals.append(now_iso())
    vals += [tenant_id, cfg_id]
    try:
        with transaction() as conn:
            conn.execute(
                f"UPDATE mikrotik_configs SET {', '.join(sets)} "
                f"WHERE tenant_id=? AND id=?",
                vals,
            )
        return get(tenant_id, cfg_id)
    except sqlite3.OperationalError as exc:
        if _table_missing(exc):
            return None
        raise


def delete(tenant_id: int, cfg_id: int) -> None:
    try:
        with transaction() as conn:
            conn.execute(
                "DELETE FROM mikrotik_configs WHERE tenant_id = ? AND id = ?",
                (tenant_id, cfg_id))
    except sqlite3.OperationalError as exc:
        if not _table_missing(exc):
            raise


def primary(tenant_id: int) -> Optional[dict]:
    try:
        cur = db().execute("""
            SELECT * FROM mikrotik_configs
            WHERE tenant_id = ? AND enabled = 1
            ORDER BY id LIMIT 1
        """, (tenant_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError as exc:
        if _table_missing(exc):
            return None
        raise
