"""MikroTik configs repo — multi-tenant + سجل آخر فحص."""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso, parse_dt


def list_configs(tenant_id: int) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM mikrotik_configs WHERE tenant_id = ? ORDER BY id",
        (tenant_id,))
    return [dict(r) for r in cur.fetchall()]


def get(tenant_id: int, cfg_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM mikrotik_configs WHERE tenant_id = ? AND id = ?",
        (tenant_id, cfg_id)).fetchone()
    return dict(row) if row else None


def create(tenant_id: int, *, name: str, host: str, username: str, password: str,
           port: int = 8728, use_tls: bool = False, verify_tls: bool = True,
           timeout_sec: int = 10, enabled: bool = True) -> int:
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
    if not sets: return get(tenant_id, cfg_id)
    sets.append("updated_at = ?"); vals.append(now_iso())
    vals += [tenant_id, cfg_id]
    with transaction() as conn:
        conn.execute(f"UPDATE mikrotik_configs SET {', '.join(sets)} WHERE tenant_id=? AND id=?", vals)
    return get(tenant_id, cfg_id)


def delete(tenant_id: int, cfg_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM mikrotik_configs WHERE tenant_id = ? AND id = ?",
                     (tenant_id, cfg_id))


def primary(tenant_id: int) -> Optional[dict]:
    cur = db().execute("""
        SELECT * FROM mikrotik_configs
        WHERE tenant_id = ? AND enabled = 1
        ORDER BY id LIMIT 1
    """, (tenant_id,))
    row = cur.fetchone()
    return dict(row) if row else None
