"""Repo for router_remote_sessions — time-boxed, source-IP-locked WinBox/mgmt
port-forwards over the SSTP tunnel.

The public port is allocated from the SAME host-published range
(51000-51199) the NPC remote-tunnel uses, so this repo's allocator avoids
collisions with BOTH pools (npc_remote_port_mappings + active rows here).
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso

# The host-published TCP range (must match docker-compose nginx `ports:`).
PORT_RANGE_BASE = 51000
PORT_RANGE_CEILING = 51199


def _npc_used_ports() -> set:
    """Ports reserved by the NPC remote-tunnel feature (if that table exists)."""
    try:
        rows = db().execute(
            "SELECT public_port FROM npc_remote_port_mappings").fetchall()
        return {int(r["public_port"]) for r in rows}
    except Exception:
        return set()


def _active_used_ports() -> set:
    rows = db().execute(
        "SELECT public_port FROM router_remote_sessions WHERE status='active'"
    ).fetchall()
    return {int(r["public_port"]) for r in rows}


def used_ports() -> set:
    """Union of every port currently claimed in the shared range."""
    return _npc_used_ports() | _active_used_ports()


def allocate_port(*, base: int = PORT_RANGE_BASE,
                  ceiling: int = PORT_RANGE_CEILING,
                  exclude: Optional[set] = None) -> int:
    """Lowest free port in the range, avoiding NPC + active sessions (+ extra
    `exclude` for an in-flight allocation). Raises if the range is exhausted."""
    used = used_ports() | (exclude or set())
    for p in range(int(base), int(ceiling) + 1):
        if p not in used:
            return p
    raise RuntimeError(
        f"نطاق منافذ الوصول البعيد ممتلئ ({base}-{ceiling}) — أغلق جلسات قديمة")


def create_session(*, tenant_id: int, router_id: int, service: str,
                   public_port: int, tunnel_ip: str, dst_port: int,
                   source_ip: str, opened_by: str, expires_at: str,
                   always_on: int = 0) -> int:
    ts = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO router_remote_sessions "
            "(tenant_id, router_id, service, public_port, tunnel_ip, dst_port, "
            " source_ip, opened_by, opened_at, expires_at, last_seen_at, "
            " status, always_on) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?)",
            (int(tenant_id), int(router_id), service, int(public_port),
             tunnel_ip, int(dst_port), source_ip, opened_by, ts, expires_at,
             ts, int(always_on)),
        )
        return int(cur.lastrowid)


def get(session_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM router_remote_sessions WHERE id=?", (int(session_id),)
    ).fetchone()
    return dict(row) if row else None


def list_active(tenant_id: Optional[int] = None) -> list:
    if tenant_id is None:
        rows = db().execute(
            "SELECT * FROM router_remote_sessions WHERE status='active' "
            "ORDER BY opened_at DESC").fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM router_remote_sessions WHERE status='active' "
            "AND tenant_id=? ORDER BY opened_at DESC", (int(tenant_id),)
        ).fetchall()
    return [dict(r) for r in rows]


def active_for_router(tenant_id: int, router_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM router_remote_sessions WHERE status='active' "
        "AND tenant_id=? AND router_id=? ORDER BY opened_at DESC LIMIT 1",
        (int(tenant_id), int(router_id))).fetchone()
    return dict(row) if row else None


def list_recent(tenant_id: int, limit: int = 100) -> list:
    rows = db().execute(
        "SELECT * FROM router_remote_sessions WHERE tenant_id=? "
        "ORDER BY opened_at DESC LIMIT ?", (int(tenant_id), int(limit))
    ).fetchall()
    return [dict(r) for r in rows]


def list_expired(now: Optional[str] = None) -> list:
    """Active rows whose absolute expiry has passed (across all tenants)."""
    cutoff = now or now_iso()
    rows = db().execute(
        "SELECT * FROM router_remote_sessions WHERE status='active' "
        "AND expires_at <> '' AND expires_at < ?", (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def mark_closed(session_id: int, *, reason: str = "manual") -> bool:
    status = "expired" if reason == "expired" else "closed"
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE router_remote_sessions SET status=?, closed_at=?, "
            "close_reason=? WHERE id=? AND status='active'",
            (status, now_iso(), reason, int(session_id)))
        return cur.rowcount > 0


def touch_last_seen(session_id: int) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE router_remote_sessions SET last_seen_at=? WHERE id=?",
            (now_iso(), int(session_id)))


def extend(session_id: int, *, expires_at: str) -> bool:
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE router_remote_sessions SET expires_at=? "
            "WHERE id=? AND status='active'", (expires_at, int(session_id)))
        return cur.rowcount > 0


__all__ = [
    "PORT_RANGE_BASE", "PORT_RANGE_CEILING", "used_ports", "allocate_port",
    "create_session", "get", "list_active", "active_for_router", "list_recent",
    "list_expired", "mark_closed", "touch_last_seen", "extend",
]
