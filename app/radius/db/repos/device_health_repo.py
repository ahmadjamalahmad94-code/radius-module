"""device_health — repo for «تتبع حالة الأجهزة» (Network Device Health Monitor).

Backs the network_device_monitor_* tables (migration 115). Distinct from the
older network_devices_repo («تابع أجهزة الشبكة»). Every query is tenant-scoped;
the device row supports soft-delete (deleted_at/by/reason).

Surface:
  • devices: list_devices / get_device / find_device_by_router_ip /
    create_device / update_device / soft_delete_device / set_status
  • scopes:  list_scopes / upsert_scope
  • bindings: list_bindings / upsert_binding
  • events:  add_event / list_events
  • alerts:  add_alert / last_alert_at
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso

# Device-type whitelist — UI passes one of these; anything else → 'other'.
ALLOWED_DEVICE_TYPES = frozenset({
    "ap", "router", "link", "unifi", "litebeam",
    "switch", "server", "camera", "other",
})

# Status enum — written by the planner (apply_failed) and the poller (rest).
ALLOWED_STATUS = frozenset({
    "up", "down", "timeout", "high_latency",
    "unknown", "disabled", "apply_failed",
})

# Alert-channel whitelist — EXISTING channels only. '' = tenant default.
ALLOWED_ALERT_CHANNELS = frozenset({"", "telegram", "sms", "whatsapp"})

ALLOWED_APPLY_STATUS = frozenset({
    "pending", "already_present", "applied", "apply_failed",
})


# ── normalisers ────────────────────────────────────────────────

def norm_device_type(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in ALLOWED_DEVICE_TYPES else "other"


def norm_alert_channel(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in ALLOWED_ALERT_CHANNELS else ""


def _norm_status(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in ALLOWED_STATUS else "unknown"


def _norm_apply_status(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in ALLOWED_APPLY_STATUS else "pending"


def _b(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    return 1 if str(value or "").strip().lower() in ("1", "true", "yes", "on") else 0


def _device_row(r) -> dict:
    return {
        "id":                             int(r["id"]),
        "tenant_id":                      int(r["tenant_id"]),
        "router_id":                      int(r["router_id"]),
        "name":                           r["name"] or "",
        "device_type":                    r["device_type"] or "other",
        "interface_name":                 r["interface_name"] or "",
        "ip_address":                     r["ip_address"] or "",
        "network_cidr":                   r["network_cidr"] or "",
        "gateway_address":                r["gateway_address"] or "",
        "location":                       r["location"] or "",
        "subnet_prefix":                  int(r["subnet_prefix"] or 24),
        "gateway_last_octet":             int(r["gateway_last_octet"] or 254),
        "ping_threshold_ms":              int(r["ping_threshold_ms"] or 80),
        "netwatch_interval_sec":          int(r["netwatch_interval_sec"] or 60),
        "netwatch_timeout_sec":           int(r["netwatch_timeout_sec"] or 3),
        "alert_channel":                  r["alert_channel"] or "",
        "monitoring_enabled":             bool(r["monitoring_enabled"]),
        "status":                         r["status"] or "unknown",
        "last_latency_ms":                float(r["last_latency_ms"]) if r["last_latency_ms"] is not None else None,
        "last_checked_at":                r["last_checked_at"] or "",
        "last_status_change_at":          r["last_status_change_at"] or "",
        "last_down_at":                   r["last_down_at"] or "",
        "last_up_at":                     r["last_up_at"] or "",
        "consecutive_down_count":         int(r["consecutive_down_count"] or 0),
        "consecutive_high_latency_count": int(r["consecutive_high_latency_count"] or 0),
        "mikrotik_netwatch_id":           r["mikrotik_netwatch_id"] or "",
        "notes":                          r["notes"] or "",
        "created_at":                     r["created_at"] or "",
        "updated_at":                     r["updated_at"] or "",
    }


# ── devices: queries ───────────────────────────────────────────

def list_devices(
    tenant_id: int,
    *,
    router_id: Optional[int] = None,
    status: Optional[str] = None,
    device_type: Optional[str] = None,
    monitoring_only: bool = False,
    include_deleted: bool = False,
) -> list[dict]:
    sql = "SELECT * FROM network_device_monitor_devices WHERE tenant_id = ?"
    args: list = [int(tenant_id)]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    if router_id is not None:
        sql += " AND router_id = ?"
        args.append(int(router_id))
    if status:
        sql += " AND status = ?"
        args.append(str(status))
    if device_type:
        sql += " AND device_type = ?"
        args.append(str(device_type))
    if monitoring_only:
        sql += " AND monitoring_enabled = 1"
    sql += " ORDER BY id DESC"
    cur = db().execute(sql, args)
    return [_device_row(r) for r in cur.fetchall()]


def get_device(tenant_id: int, device_id: int,
               *, include_deleted: bool = False) -> Optional[dict]:
    sql = ("SELECT * FROM network_device_monitor_devices "
           "WHERE tenant_id = ? AND id = ?")
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(sql, (int(tenant_id), int(device_id)))
    r = cur.fetchone()
    return _device_row(r) if r else None


def find_device_by_router_ip(
    tenant_id: int, router_id: int, ip_address: str,
) -> Optional[dict]:
    """Duplicate-prevention lookup — same router + same IP among the living."""
    ip = str(ip_address or "").strip()
    if not ip:
        return None
    cur = db().execute(
        "SELECT * FROM network_device_monitor_devices "
        "WHERE tenant_id = ? AND router_id = ? AND ip_address = ? "
        "AND deleted_at IS NULL",
        (int(tenant_id), int(router_id), ip),
    )
    r = cur.fetchone()
    return _device_row(r) if r else None


# ── devices: mutations ─────────────────────────────────────────

def create_device(
    *,
    tenant_id: int,
    router_id: int,
    name: str,
    interface_name: str,
    ip_address: str,
    network_cidr: str,
    gateway_address: str,
    device_type: str = "other",
    location: str = "",
    subnet_prefix: int = 24,
    gateway_last_octet: int = 254,
    ping_threshold_ms: int = 80,
    netwatch_interval_sec: int = 60,
    netwatch_timeout_sec: int = 3,
    alert_channel: str = "",
    monitoring_enabled: bool = True,
    notes: str = "",
) -> int:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO network_device_monitor_devices ("
            "  tenant_id, router_id, name, device_type, interface_name,"
            "  ip_address, network_cidr, gateway_address, location,"
            "  subnet_prefix, gateway_last_octet, ping_threshold_ms,"
            "  netwatch_interval_sec, netwatch_timeout_sec, alert_channel,"
            "  monitoring_enabled, status, notes, created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(tenant_id), int(router_id),
                str(name or "").strip(),
                norm_device_type(device_type),
                str(interface_name or "").strip(),
                str(ip_address or "").strip(),
                str(network_cidr or "").strip(),
                str(gateway_address or "").strip(),
                str(location or "").strip(),
                int(subnet_prefix), int(gateway_last_octet),
                int(ping_threshold_ms), int(netwatch_interval_sec),
                int(netwatch_timeout_sec),
                norm_alert_channel(alert_channel),
                _b(monitoring_enabled),
                "unknown" if monitoring_enabled else "disabled",
                str(notes or "").strip(),
                now, now,
            ),
        )
        return int(cur.lastrowid)


# Columns the UI may patch, each mapped to its normaliser.
_EDITABLE = {
    "name":                  lambda v: str(v or "").strip(),
    "device_type":           norm_device_type,
    "interface_name":        lambda v: str(v or "").strip(),
    "ip_address":            lambda v: str(v or "").strip(),
    "network_cidr":          lambda v: str(v or "").strip(),
    "gateway_address":       lambda v: str(v or "").strip(),
    "location":              lambda v: str(v or "").strip(),
    "subnet_prefix":         lambda v: int(v),
    "gateway_last_octet":    lambda v: int(v),
    "ping_threshold_ms":     lambda v: int(v),
    "netwatch_interval_sec": lambda v: int(v),
    "netwatch_timeout_sec":  lambda v: int(v),
    "alert_channel":         norm_alert_channel,
    "monitoring_enabled":    _b,
    "notes":                 lambda v: str(v or "").strip(),
    "router_id":             lambda v: int(v),
}


def update_device(tenant_id: int, device_id: int, **fields: Any) -> bool:
    sets: list[str] = []
    args: list = []
    for key, raw in fields.items():
        norm = _EDITABLE.get(key)
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
            "UPDATE network_device_monitor_devices SET " + ", ".join(sets)
            + " WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
            args,
        )
        return cur.rowcount > 0


def set_monitoring(tenant_id: int, device_id: int, enabled: bool) -> bool:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE network_device_monitor_devices "
            "SET monitoring_enabled = ?, status = ?, updated_at = ? "
            "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
            (
                _b(enabled),
                # Flipping off parks the row at 'disabled'; flipping on
                # resets to 'unknown' until the next probe re-establishes it.
                "disabled" if not enabled else "unknown",
                now, int(tenant_id), int(device_id),
            ),
        )
        return cur.rowcount > 0


def soft_delete_device(tenant_id: int, device_id: int,
                       *, actor: str = "", reason: str = "") -> bool:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE network_device_monitor_devices "
            "SET deleted_at = ?, deleted_by = ?, delete_reason = ?, "
            "    monitoring_enabled = 0, updated_at = ? "
            "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
            (now, actor or "system", (reason or "")[:300], now,
             int(tenant_id), int(device_id)),
        )
        return cur.rowcount > 0


def set_status(
    *,
    tenant_id: int,
    device_id: int,
    status: str,
    latency_ms: Optional[float] = None,
    checked_at: Optional[str] = None,
) -> None:
    """Status writer used by the poller (Phase 4) and the apply path.

    Maintains last_checked_at, status-change/up/down stamps, and the
    consecutive counters. Kept here so one writer serves every caller.
    """
    new_status = _norm_status(status)
    ts = checked_at or now_iso()
    cur = db().execute(
        "SELECT status, consecutive_down_count, consecutive_high_latency_count "
        "FROM network_device_monitor_devices WHERE tenant_id = ? AND id = ?",
        (int(tenant_id), int(device_id)),
    )
    row = cur.fetchone()
    if row is None:
        return
    prev_status = row["status"] or "unknown"
    down_cnt = int(row["consecutive_down_count"] or 0)
    high_cnt = int(row["consecutive_high_latency_count"] or 0)

    if new_status in ("down", "timeout"):
        down_cnt += 1
    else:
        down_cnt = 0
    if new_status == "high_latency":
        high_cnt += 1
    else:
        high_cnt = 0

    sets = [
        "status = ?", "last_latency_ms = ?", "last_checked_at = ?",
        "consecutive_down_count = ?", "consecutive_high_latency_count = ?",
        "updated_at = ?",
    ]
    args: list = [
        new_status,
        float(latency_ms) if latency_ms is not None else None,
        ts, down_cnt, high_cnt, ts,
    ]
    if new_status != prev_status:
        sets.append("last_status_change_at = ?")
        args.append(ts)
    if new_status in ("down", "timeout"):
        sets.append("last_down_at = ?")
        args.append(ts)
    if new_status == "up":
        sets.append("last_up_at = ?")
        args.append(ts)

    args.extend([int(tenant_id), int(device_id)])
    with transaction() as conn:
        conn.execute(
            "UPDATE network_device_monitor_devices SET " + ", ".join(sets)
            + " WHERE tenant_id = ? AND id = ?",
            args,
        )


# ── network scopes ─────────────────────────────────────────────

def list_scopes(tenant_id: int, *, router_id: Optional[int] = None) -> list[dict]:
    sql = ("SELECT * FROM network_device_monitor_network_scopes "
           "WHERE tenant_id = ?")
    args: list = [int(tenant_id)]
    if router_id is not None:
        sql += " AND router_id = ?"
        args.append(int(router_id))
    sql += " ORDER BY id"
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def scopes_for_network(tenant_id: int, router_id: int,
                       network_cidr: str) -> list[dict]:
    """Every scope row carrying this subnet on the router — used to detect
    the «same subnet on more than one interface» routing-ambiguity warning."""
    cur = db().execute(
        "SELECT * FROM network_device_monitor_network_scopes "
        "WHERE tenant_id = ? AND router_id = ? AND network_cidr = ?",
        (int(tenant_id), int(router_id), str(network_cidr or "").strip()),
    )
    return [dict(r) for r in cur.fetchall()]


def upsert_scope(
    *,
    tenant_id: int,
    router_id: int,
    interface_name: str,
    network_cidr: str,
    gateway_address: str,
    apply_status: str = "pending",
    mikrotik_address_id: str = "",
) -> int:
    """Insert-or-update keyed on (tenant, router, interface, network_cidr)."""
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "SELECT id FROM network_device_monitor_network_scopes "
            "WHERE tenant_id = ? AND router_id = ? AND interface_name = ? "
            "AND network_cidr = ?",
            (int(tenant_id), int(router_id),
             str(interface_name or "").strip(), str(network_cidr or "").strip()),
        )
        row = cur.fetchone()
        if row:
            conn.execute(
                "UPDATE network_device_monitor_network_scopes "
                "SET gateway_address = ?, apply_status = ?, "
                "    mikrotik_address_id = ?, updated_at = ? WHERE id = ?",
                (str(gateway_address or "").strip(),
                 _norm_apply_status(apply_status),
                 str(mikrotik_address_id or ""), now, int(row["id"])),
            )
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO network_device_monitor_network_scopes ("
            "  tenant_id, router_id, interface_name, network_cidr,"
            "  gateway_address, mikrotik_address_id, apply_status,"
            "  created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (int(tenant_id), int(router_id),
             str(interface_name or "").strip(), str(network_cidr or "").strip(),
             str(gateway_address or "").strip(), str(mikrotik_address_id or ""),
             _norm_apply_status(apply_status), now, now),
        )
        return int(cur.lastrowid)


def set_scope_apply(
    *, tenant_id: int, router_id: int, interface_name: str, network_cidr: str,
    apply_status: str, mikrotik_address_id: str = "", error: str = "",
) -> None:
    """Record the outcome of a live apply on the matching scope row."""
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            "UPDATE network_device_monitor_network_scopes "
            "SET apply_status = ?, mikrotik_address_id = ?, apply_error = ?, "
            "    last_applied_at = ?, updated_at = ? "
            "WHERE tenant_id = ? AND router_id = ? AND interface_name = ? "
            "AND network_cidr = ?",
            (_norm_apply_status(apply_status), str(mikrotik_address_id or ""),
             str(error or "")[:500], now, now,
             int(tenant_id), int(router_id),
             str(interface_name or "").strip(), str(network_cidr or "").strip()),
        )


# ── bindings ───────────────────────────────────────────────────

def list_bindings(tenant_id: int, *, router_id: Optional[int] = None) -> list[dict]:
    sql = ("SELECT * FROM network_device_monitor_bindings WHERE tenant_id = ?")
    args: list = [int(tenant_id)]
    if router_id is not None:
        sql += " AND router_id = ?"
        args.append(int(router_id))
    sql += " ORDER BY id"
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def upsert_binding(
    *,
    tenant_id: int,
    router_id: int,
    network_cidr: str,
    binding_type: str = "bypassed",
    apply_status: str = "pending",
    mikrotik_binding_id: str = "",
) -> int:
    now = now_iso()
    btype = str(binding_type or "bypassed").strip().lower()
    with transaction() as conn:
        cur = conn.execute(
            "SELECT id FROM network_device_monitor_bindings "
            "WHERE tenant_id = ? AND router_id = ? AND network_cidr = ? "
            "AND binding_type = ?",
            (int(tenant_id), int(router_id),
             str(network_cidr or "").strip(), btype),
        )
        row = cur.fetchone()
        if row:
            conn.execute(
                "UPDATE network_device_monitor_bindings "
                "SET apply_status = ?, mikrotik_binding_id = ?, updated_at = ? "
                "WHERE id = ?",
                (_norm_apply_status(apply_status),
                 str(mikrotik_binding_id or ""), now, int(row["id"])),
            )
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO network_device_monitor_bindings ("
            "  tenant_id, router_id, network_cidr, binding_type,"
            "  mikrotik_binding_id, apply_status, created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (int(tenant_id), int(router_id),
             str(network_cidr or "").strip(), btype,
             str(mikrotik_binding_id or ""),
             _norm_apply_status(apply_status), now, now),
        )
        return int(cur.lastrowid)


def set_binding_apply(
    *, tenant_id: int, router_id: int, network_cidr: str, binding_type: str,
    apply_status: str, mikrotik_binding_id: str = "", error: str = "",
) -> None:
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            "UPDATE network_device_monitor_bindings "
            "SET apply_status = ?, mikrotik_binding_id = ?, apply_error = ?, "
            "    last_applied_at = ?, updated_at = ? "
            "WHERE tenant_id = ? AND router_id = ? AND network_cidr = ? "
            "AND binding_type = ?",
            (_norm_apply_status(apply_status), str(mikrotik_binding_id or ""),
             str(error or "")[:500], now, now,
             int(tenant_id), int(router_id),
             str(network_cidr or "").strip(),
             str(binding_type or "bypassed").strip().lower()),
        )


def set_netwatch_id(tenant_id: int, device_id: int, netwatch_id: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE network_device_monitor_devices "
            "SET mikrotik_netwatch_id = ?, updated_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (str(netwatch_id or ""), now_iso(), int(tenant_id), int(device_id)),
        )


# ── events ─────────────────────────────────────────────────────

def add_event(
    *,
    tenant_id: int,
    device_id: int,
    event_type: str,
    previous_status: str = "",
    new_status: str = "",
    latency_ms: Optional[float] = None,
    message: str = "",
) -> int:
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO network_device_monitor_events ("
            "  tenant_id, device_id, event_type, previous_status,"
            "  new_status, latency_ms, message, created_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (int(tenant_id), int(device_id), str(event_type or ""),
             str(previous_status or ""), str(new_status or ""),
             float(latency_ms) if latency_ms is not None else None,
             str(message or "")[:1000], now_iso()),
        )
        return int(cur.lastrowid)


def list_events(tenant_id: int, *, device_id: Optional[int] = None,
                limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM network_device_monitor_events WHERE tenant_id = ?"
    args: list = [int(tenant_id)]
    if device_id is not None:
        sql += " AND device_id = ?"
        args.append(int(device_id))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    return [dict(r) for r in db().execute(sql, args).fetchall()]


# ── alerts ─────────────────────────────────────────────────────

def add_alert(
    *,
    tenant_id: int,
    device_id: int,
    alert_type: str,
    channel: str,
    status: str,
    dedup_key: str = "",
    message: str = "",
) -> int:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO network_device_monitor_alerts ("
            "  tenant_id, device_id, alert_type, channel, status,"
            "  sent_at, dedup_key, message, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (int(tenant_id), int(device_id), str(alert_type or ""),
             str(channel or ""), str(status or ""),
             now if status == "sent" else None,
             str(dedup_key or ""), str(message or "")[:1000], now),
        )
        return int(cur.lastrowid)


def last_alert_at(tenant_id: int, dedup_key: str) -> Optional[str]:
    """Newest created_at for a dedup bucket (any status) — the cooldown gate."""
    cur = db().execute(
        "SELECT created_at FROM network_device_monitor_alerts "
        "WHERE tenant_id = ? AND dedup_key = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (int(tenant_id), str(dedup_key or "")),
    )
    r = cur.fetchone()
    return r["created_at"] if r else None
