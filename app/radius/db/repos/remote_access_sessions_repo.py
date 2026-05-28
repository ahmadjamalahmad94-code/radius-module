"""remote_access_sessions repo (Sprint 5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso


ALLOWED_STATUS = frozenset({"active", "expired", "closed", "failed"})
ALLOWED_PROTOCOLS = frozenset({"http", "https", "winbox", "ssh"})

# Default internal port per protocol — operator can override.
DEFAULT_PORT = {"http": 80, "https": 443, "winbox": 8291, "ssh": 22}


def _row(r) -> dict:
    return {
        "id":            int(r["id"]),
        "tenant_id":     int(r["tenant_id"]),
        "device_id":     int(r["device_id"]),
        "router_id":     int(r["router_id"]),
        "requested_by":  r["requested_by"] or "",
        "protocol":      r["protocol"] or "http",
        "internal_ip":   r["internal_ip"] or "",
        "internal_port": int(r["internal_port"] or 0),
        "external_port": int(r["external_port"] or 0),
        "status":        r["status"] or "active",
        "created_at":    r["created_at"] or "",
        "expires_at":    r["expires_at"] or "",
        "closed_at":     r["closed_at"] or "",
        "audit_ip":      r["audit_ip"] or "",
        "notes":         r["notes"] or "",
    }


def _utc_iso_in(minutes: int) -> str:
    """ISO timestamp `minutes` from now, UTC, matching SQLite's
    `datetime('now')` format (`'YYYY-MM-DD HH:MM:SS'`)."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=int(minutes))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def create(
    *,
    tenant_id: int,
    device_id: int,
    router_id: int,
    requested_by: str,
    protocol: str,
    internal_ip: str,
    internal_port: int,
    external_port: int,
    ttl_minutes: int = 30,
    audit_ip: str = "",
    notes: str = "",
) -> int:
    proto = protocol if protocol in ALLOWED_PROTOCOLS else "http"
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO remote_access_sessions ("
            "  tenant_id, device_id, router_id, requested_by,"
            "  protocol, internal_ip, internal_port,"
            "  external_port, status, created_at, expires_at,"
            "  audit_ip, notes"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (
                int(tenant_id), int(device_id), int(router_id),
                str(requested_by or "")[:120],
                proto,
                str(internal_ip or "").strip(),
                int(internal_port),
                int(external_port),
                now_iso(),
                _utc_iso_in(ttl_minutes),
                str(audit_ip or "")[:64],
                str(notes or "")[:500],
            ),
        )
        return int(cur.lastrowid)


def get(tenant_id: int, session_id: int) -> Optional[dict]:
    cur = db().execute(
        "SELECT * FROM remote_access_sessions "
        "WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(session_id)),
    )
    r = cur.fetchone()
    return _row(r) if r else None


def list_for_device(tenant_id: int, device_id: int,
                    limit: int = 25) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM remote_access_sessions "
        "WHERE tenant_id = ? AND device_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (int(tenant_id), int(device_id), int(limit)),
    )
    return [_row(r) for r in cur.fetchall()]


def list_active(tenant_id: int) -> list[dict]:
    cur = db().execute(
        "SELECT * FROM remote_access_sessions "
        "WHERE tenant_id = ? AND status = 'active' "
        "ORDER BY expires_at",
        (int(tenant_id),),
    )
    return [_row(r) for r in cur.fetchall()]


def list_expired_active(now_str: Optional[str] = None) -> list[dict]:
    """Every still-marked-active row whose expires_at is in the
    past. Used by the cron sweep to expire+cleanup. No tenant
    filter — the worker is system-scoped."""
    now = now_str or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    cur = db().execute(
        "SELECT * FROM remote_access_sessions "
        "WHERE status = 'active' AND expires_at <= ?",
        (now,),
    )
    return [_row(r) for r in cur.fetchall()]


def mark_closed(session_id: int, *, status: str = "closed") -> None:
    s = status if status in ALLOWED_STATUS else "closed"
    with transaction() as conn:
        conn.execute(
            "UPDATE remote_access_sessions "
            "SET status = ?, closed_at = ? "
            "WHERE id = ?",
            (s, now_iso(), int(session_id)),
        )


def next_free_external_port(device_id: int,
                            base: int = 40000,
                            window: int = 20000) -> int:
    """Deterministic + collision-avoiding port pick.

    Start from `base + (device_id % window)`. If that port is
    already used by another ACTIVE session, walk forward until
    free. Caps the search at 200 attempts; raises ValueError
    if all are busy (vanishingly unlikely with 20 000 slots).
    """
    desired = int(base) + (int(device_id) % int(window))
    cur = db().execute(
        "SELECT external_port FROM remote_access_sessions "
        "WHERE status = 'active'"
    )
    busy = {int(r["external_port"]) for r in cur.fetchall()}
    for offset in range(200):
        candidate = desired + offset
        if candidate > 65535:
            candidate = base + (candidate - 65536)
        if candidate not in busy:
            return candidate
    raise ValueError("no free external port available")
