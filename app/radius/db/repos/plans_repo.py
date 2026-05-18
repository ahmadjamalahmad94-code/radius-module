"""Access Plans repo."""
from __future__ import annotations

from typing import Optional

from ...core.types import AccessPlan
from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, parse_dt


_COLS = (
    "name","code","plan_type","service_type","typebp","limit_type",
    "duration_value","duration_unit","duration_minutes",
    "validity_value","validity_unit","validity_days",
    "max_daily_minutes","max_weekly_minutes","max_monthly_minutes",
    "session_timeout_sec","idle_timeout_sec",
    "data_value","data_unit","quota_total_mb","quota_daily_mb","quota_monthly_mb",
    "quota_reset_strategy",
    "bandwidth_id","speed_up_kbps","speed_down_kbps","burst_up_kbps","burst_down_kbps",
    "burst_threshold_kbps","burst_time_sec","burst_raw",
    "concurrent_sessions","address_pool","framed_pool","pool_id","vlan_id","ipv6_pool",
    "bind_mac","bind_ip","force_mac_address","allowed_devices_count",
    "allowed_days","allowed_hours_from","allowed_hours_to",
    "on_login","on_logout","auto_renew","router_ids",
    "price_card","price_bulk","price","currency","plan_tier","prepaid","project",
    "description","enabled","priority","color",
)


def _row(r) -> AccessPlan:
    return AccessPlan(
        id=r["id"], tenant_id=r["tenant_id"], name=r["name"], code=r["code"] or "",
        plan_type=r["plan_type"], service_type=r["service_type"],
        typebp=r["typebp"] or "Limited", limit_type=r["limit_type"] or "Time_Limit",
        duration_value=r["duration_value"], duration_unit=r["duration_unit"] or "Mins",
        duration_minutes=r["duration_minutes"],
        validity_value=r["validity_value"], validity_unit=r["validity_unit"] or "Days",
        validity_days=r["validity_days"],
        max_daily_minutes=r["max_daily_minutes"], max_weekly_minutes=r["max_weekly_minutes"],
        max_monthly_minutes=r["max_monthly_minutes"],
        session_timeout_sec=r["session_timeout_sec"], idle_timeout_sec=r["idle_timeout_sec"],
        data_value=r["data_value"], data_unit=r["data_unit"] or "MB",
        quota_total_mb=r["quota_total_mb"], quota_daily_mb=r["quota_daily_mb"],
        quota_monthly_mb=r["quota_monthly_mb"],
        quota_reset_strategy=r["quota_reset_strategy"] or "rolling",
        bandwidth_id=r["bandwidth_id"],
        speed_up_kbps=r["speed_up_kbps"], speed_down_kbps=r["speed_down_kbps"],
        burst_up_kbps=r["burst_up_kbps"], burst_down_kbps=r["burst_down_kbps"],
        burst_threshold_kbps=r["burst_threshold_kbps"], burst_time_sec=r["burst_time_sec"],
        burst_raw=r["burst_raw"] or "",
        concurrent_sessions=r["concurrent_sessions"] or 1,
        address_pool=r["address_pool"] or "", framed_pool=r["framed_pool"] or "",
        pool_id=r["pool_id"], vlan_id=r["vlan_id"], ipv6_pool=r["ipv6_pool"] or "",
        bind_mac=bool(r["bind_mac"]), bind_ip=bool(r["bind_ip"]),
        force_mac_address=bool(r["force_mac_address"]),
        allowed_devices_count=r["allowed_devices_count"] or 1,
        allowed_days=tuple(json_load(r["allowed_days"], default=["mon","tue","wed","thu","fri","sat","sun"])),
        allowed_hours_from=r["allowed_hours_from"] or "", allowed_hours_to=r["allowed_hours_to"] or "",
        on_login=r["on_login"] or "", on_logout=r["on_logout"] or "",
        auto_renew=bool(r["auto_renew"]),
        router_ids=tuple(json_load(r["router_ids"], default=[])),
        price_card=r["price_card"] or 0.0, price_bulk=r["price_bulk"] or 0.0,
        price=r["price"] or 0.0, currency=r["currency"] or "JOD",
        plan_tier=r["plan_tier"] or "Personal", prepaid=bool(r["prepaid"]),
        project=r["project"] or "", description=r["description"] or "",
        enabled=bool(r["enabled"]), priority=r["priority"] or 100,
        color=r["color"] or "#2BAACC",
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
    )


def list_plans(tenant_id: int, *, limit: int = 200, offset: int = 0) -> list[AccessPlan]:
    cur = db().execute(
        "SELECT * FROM access_plans WHERE tenant_id = ? ORDER BY priority, id LIMIT ? OFFSET ?",
        (tenant_id, limit, offset)
    )
    return [_row(r) for r in cur.fetchall()]


def get_plan(tenant_id: int, plan_id: int) -> Optional[AccessPlan]:
    cur = db().execute(
        "SELECT * FROM access_plans WHERE tenant_id = ? AND id = ?",
        (tenant_id, plan_id)
    )
    row = cur.fetchone()
    return _row(row) if row else None


def upsert_plan(p: AccessPlan) -> AccessPlan:
    values = (
        p.name, p.code, p.plan_type, p.service_type, p.typebp, p.limit_type,
        p.duration_value, p.duration_unit, p.duration_minutes,
        p.validity_value, p.validity_unit, p.validity_days,
        p.max_daily_minutes, p.max_weekly_minutes, p.max_monthly_minutes,
        p.session_timeout_sec, p.idle_timeout_sec,
        p.data_value, p.data_unit, p.quota_total_mb, p.quota_daily_mb, p.quota_monthly_mb,
        p.quota_reset_strategy,
        p.bandwidth_id, p.speed_up_kbps, p.speed_down_kbps, p.burst_up_kbps, p.burst_down_kbps,
        p.burst_threshold_kbps, p.burst_time_sec, p.burst_raw,
        p.concurrent_sessions, p.address_pool, p.framed_pool, p.pool_id, p.vlan_id, p.ipv6_pool,
        int(p.bind_mac), int(p.bind_ip), int(p.force_mac_address), p.allowed_devices_count,
        json_dump(list(p.allowed_days)), p.allowed_hours_from, p.allowed_hours_to,
        p.on_login, p.on_logout, int(p.auto_renew), json_dump(list(p.router_ids)),
        p.price_card, p.price_bulk, p.price, p.currency, p.plan_tier, int(p.prepaid), p.project,
        p.description, int(p.enabled), p.priority, p.color,
    )
    now = now_iso()
    with transaction() as conn:
        if p.id is None:
            placeholders = ",".join(["?"] * (len(_COLS) + 2))  # +tenant_id, +created_at
            cur = conn.execute(
                f"INSERT INTO access_plans(tenant_id, {', '.join(_COLS)}, created_at) "
                f"VALUES({placeholders})",
                (p.tenant_id, *values, now)
            )
            new_id = cur.lastrowid
        else:
            sets = ", ".join(f"{c}=?" for c in _COLS)
            conn.execute(
                f"UPDATE access_plans SET {sets}, updated_at=? WHERE tenant_id=? AND id=?",
                (*values, now, p.tenant_id, p.id)
            )
            new_id = p.id
    return get_plan(p.tenant_id, new_id)


def delete_plan(tenant_id: int, plan_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM access_plans WHERE tenant_id = ? AND id = ?", (tenant_id, plan_id))
