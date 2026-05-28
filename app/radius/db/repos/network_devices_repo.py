"""network_devices — Device Registry repo (Sprint 1).

The inventory backing the «تابع أجهزة الشبكة» page. See
docs/network_operations/NETWORK_OPERATIONS_PLAN.md for the
column-by-column intent.

Surface is intentionally small for Sprint 1:
  • list_for_tenant  — list view, optionally filtered by router
  • get_by_id        — load one (tenant-scoped, no leaks)
  • create / update / delete  — CRUD with timestamp bookkeeping
  • set_last_check   — Sprint 2 hook (cron-side ping)

Every query is tenant-scoped — passing the wrong tenant_id
returns an empty list / None, never another tenant's row.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso


# Device-type whitelist — UI passes one of these, anything else
# is stored as `other`. Kept in Python so a future addition
# doesn't need a migration.
ALLOWED_DEVICE_TYPES = frozenset({
    "ap", "router", "switch", "camera", "nvr", "server", "other",
})

# Last-status enum — populated by Sprint 2's cron monitor.
ALLOWED_LAST_STATUS = frozenset({"up", "down", "unknown"})


# ── normalisation helpers ──────────────────────────────────────

def _norm_device_type(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in ALLOWED_DEVICE_TYPES else "other"


def _norm_mac(value: Any) -> str:
    """Normalise MAC to lower-case colon-separated form.

    Accepts the usual operator-typed shapes:
      AA:BB:CC:DD:EE:FF  → aa:bb:cc:dd:ee:ff
      AA-BB-CC-DD-EE-FF  → aa:bb:cc:dd:ee:ff
      aabbccddeeff       → aa:bb:cc:dd:ee:ff
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    # Strip every separator, take what's left
    hex_only = "".join(ch for ch in raw if ch in "0123456789abcdef")
    if len(hex_only) != 12:
        # Not a recognisable MAC — keep the raw value so the
        # operator can see what they typed and correct it.
        return raw
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def _norm_ip(value: Any) -> str:
    return str(value or "").strip()


def _norm_port(value: Any, default: int = 80) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    if not (1 <= port <= 65535):
        return default
    return port


def _norm_bool(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    s = str(value or "").strip().lower()
    return 1 if s in ("1", "true", "yes", "on") else 0


def _row(r) -> dict:
    """Convert a sqlite Row to a plain dict the routes/templates
    can read without worrying about column-access semantics."""
    return {
        "id":               int(r["id"]),
        "tenant_id":        int(r["tenant_id"]),
        "router_id":        int(r["router_id"]),
        "name":             r["name"] or "",
        "device_type":      r["device_type"] or "other",
        "ip_address":       r["ip_address"] or "",
        "mac_address":      r["mac_address"] or "",
        "location":         r["location"] or "",
        "management_port":  int(r["management_port"] or 80),
        "notes":            r["notes"] or "",
        "is_critical":      bool(r["is_critical"]),
        "watch_enabled":    bool(r["watch_enabled"]),
        "alert_enabled":    bool(r["alert_enabled"]),
        "last_status":      r["last_status"] or "unknown",
        "last_checked_at":  r["last_checked_at"] or "",
        "last_latency_ms":  float(r["last_latency_ms"]) if r["last_latency_ms"] is not None else None,
        "created_at":       r["created_at"] or "",
        "updated_at":       r["updated_at"] or "",
    }


# ── queries ────────────────────────────────────────────────────

def list_for_tenant(
    tenant_id: int,
    *,
    router_id: Optional[int] = None,
    watch_only: bool = False,
) -> list[dict]:
    """Every registered device this tenant owns.

    `router_id` filter — show only devices behind one router.
    `watch_only` — used by the Sprint-2 cron worker so it
    doesn't scan rows the operator deliberately left
    untracked.
    """
    sql = "SELECT * FROM network_devices WHERE tenant_id = ?"
    args: list = [int(tenant_id)]
    if router_id is not None:
        sql += " AND router_id = ?"
        args.append(int(router_id))
    if watch_only:
        sql += " AND watch_enabled = 1"
    sql += " ORDER BY id"
    cur = db().execute(sql, args)
    return [_row(r) for r in cur.fetchall()]


def get_by_id(tenant_id: int, device_id: int) -> Optional[dict]:
    cur = db().execute(
        "SELECT * FROM network_devices "
        "WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(device_id)),
    )
    r = cur.fetchone()
    return _row(r) if r else None


# ── mutations ──────────────────────────────────────────────────

def create(
    *,
    tenant_id: int,
    router_id: int,
    name: str,
    device_type: str = "other",
    ip_address: str = "",
    mac_address: str = "",
    location: str = "",
    management_port: int = 80,
    notes: str = "",
    is_critical: bool = False,
    watch_enabled: bool = False,
    alert_enabled: bool = False,
) -> int:
    """Insert a row, return the new id."""
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO network_devices ("
            "  tenant_id, router_id, name, device_type, ip_address,"
            "  mac_address, location, management_port, notes,"
            "  is_critical, watch_enabled, alert_enabled,"
            "  created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(tenant_id), int(router_id),
                str(name or "").strip(),
                _norm_device_type(device_type),
                _norm_ip(ip_address),
                _norm_mac(mac_address),
                str(location or "").strip(),
                _norm_port(management_port),
                str(notes or "").strip(),
                _norm_bool(is_critical),
                _norm_bool(watch_enabled),
                _norm_bool(alert_enabled),
                now, now,
            ),
        )
        return int(cur.lastrowid)


def update(
    tenant_id: int,
    device_id: int,
    **fields: Any,
) -> bool:
    """Patch any subset of editable fields. Returns True if a row
    was actually changed (caller can flash «no changes» otherwise).
    """
    if not fields:
        return False
    # Whitelist editable columns + map each to its normaliser.
    editable = {
        "name":            lambda v: str(v or "").strip(),
        "device_type":     _norm_device_type,
        "ip_address":      _norm_ip,
        "mac_address":     _norm_mac,
        "location":        lambda v: str(v or "").strip(),
        "management_port": _norm_port,
        "notes":           lambda v: str(v or "").strip(),
        "is_critical":     _norm_bool,
        "watch_enabled":   _norm_bool,
        "alert_enabled":   _norm_bool,
    }
    sets: list[str] = []
    args: list = []
    for key, raw in fields.items():
        norm = editable.get(key)
        if norm is None:
            continue
        sets.append(f"{key} = ?")
        args.append(norm(raw))
    if not sets:
        return False
    sets.append("updated_at = ?")
    args.append(now_iso())
    args.extend([int(tenant_id), int(device_id)])
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE network_devices SET " + ", ".join(sets)
            + " WHERE tenant_id = ? AND id = ?",
            args,
        )
        return cur.rowcount > 0


def delete(tenant_id: int, device_id: int) -> bool:
    """Hard delete — Sprint 1 has no soft-delete column yet.
    Returns True if a row was removed.
    """
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM network_devices "
            "WHERE tenant_id = ? AND id = ?",
            (int(tenant_id), int(device_id)),
        )
        return cur.rowcount > 0


# ── status writer (Sprint 2 will call this) ─────────────────────

def set_last_check(
    *,
    tenant_id: int,
    device_id: int,
    status: str,
    latency_ms: Optional[float] = None,
    checked_at: Optional[str] = None,
) -> None:
    """Update the latest-status fields on the row.

    Called by Sprint 2's cron worker after each probe. Stays
    here in Sprint 1 so the manual «فحص الآن» button can call
    the same writer (one code path for both manual + auto).
    """
    status_norm = status if status in ALLOWED_LAST_STATUS else "unknown"
    ts = checked_at or now_iso()
    with transaction() as conn:
        conn.execute(
            "UPDATE network_devices SET "
            "  last_status = ?,"
            "  last_checked_at = ?,"
            "  last_latency_ms = ?,"
            "  updated_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (
                status_norm, ts,
                float(latency_ms) if latency_ms is not None else None,
                ts,
                int(tenant_id), int(device_id),
            ),
        )
