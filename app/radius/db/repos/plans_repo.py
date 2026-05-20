"""Access Plans repo."""
from __future__ import annotations

from typing import Any, Optional

from ...core.types import AccessPlan
from ..connection import db, transaction
from ..helpers import dt_to_iso, json_dump, json_load, now_iso, parse_dt


def _g(row: Any, key: str, default):
    """Safe getter for sqlite3.Row — fallback for older DB snapshots."""
    try:
        v = row[key]
        return default if v is None else v
    except (KeyError, IndexError):
        return default


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
    # RM-H3: AdvRadius extension cols (migration 012)
    "speed_control_enabled","cir_down_kbps","cir_up_kbps",
    "burst_enabled","nightly_unlimited_enabled",
    "monthly_download_quota_mb","monthly_upload_quota_mb","monthly_combined_quota_mb",
    "daily_download_quota_mb","daily_upload_quota_mb","daily_combined_quota_mb",
    "single_use_once","max_consumption_times","ticket_validity_days","working_hours_limit",
    "hotspot_enabled","ppp_enabled",
    "service_scope","loan_enabled","max_loan_minutes","speed_override_allowed",
    "offer_hours_from","offer_hours_to",
    "metadata",
    "deleted_at","deleted_by","delete_reason",
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
        # RM-H3 fields — safe defaults for rows from before migration 012
        speed_control_enabled=bool(_g(r,"speed_control_enabled",0)),
        cir_down_kbps=_g(r,"cir_down_kbps",0) or 0,
        cir_up_kbps=_g(r,"cir_up_kbps",0) or 0,
        burst_enabled=bool(_g(r,"burst_enabled",0)),
        nightly_unlimited_enabled=bool(_g(r,"nightly_unlimited_enabled",0)),
        monthly_download_quota_mb=_g(r,"monthly_download_quota_mb",0) or 0,
        monthly_upload_quota_mb=_g(r,"monthly_upload_quota_mb",0) or 0,
        monthly_combined_quota_mb=_g(r,"monthly_combined_quota_mb",0) or 0,
        daily_download_quota_mb=_g(r,"daily_download_quota_mb",0) or 0,
        daily_upload_quota_mb=_g(r,"daily_upload_quota_mb",0) or 0,
        daily_combined_quota_mb=_g(r,"daily_combined_quota_mb",0) or 0,
        single_use_once=bool(_g(r,"single_use_once",0)),
        max_consumption_times=_g(r,"max_consumption_times",0) or 0,
        ticket_validity_days=_g(r,"ticket_validity_days",0) or 0,
        working_hours_limit=_g(r,"working_hours_limit",0) or 0,
        hotspot_enabled=bool(_g(r,"hotspot_enabled",0)),
        ppp_enabled=bool(_g(r,"ppp_enabled",0)),
        service_scope=_g(r,"service_scope","both") or "both",
        loan_enabled=bool(_g(r,"loan_enabled",0)),
        max_loan_minutes=_g(r,"max_loan_minutes",0) or 0,
        speed_override_allowed=bool(_g(r,"speed_override_allowed",0)),
        offer_hours_from=_g(r,"offer_hours_from","") or "",
        offer_hours_to=_g(r,"offer_hours_to","") or "",
        metadata=_g(r,"metadata","{}") or "{}",
        deleted_at=parse_dt(_g(r, "deleted_at", None)),
        deleted_by=_g(r, "deleted_by", "") or "",
        delete_reason=_g(r, "delete_reason", "") or "",
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
    )


def list_plans(tenant_id: int, *, limit: int = 200, offset: int = 0,
               include_deleted: bool = False) -> list[AccessPlan]:
    sql = "SELECT * FROM access_plans WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY priority, id LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    cur = db().execute(
        sql,
        vals
    )
    return [_row(r) for r in cur.fetchall()]


def get_plan(tenant_id: int, plan_id: int,
             include_deleted: bool = False) -> Optional[AccessPlan]:
    sql = "SELECT * FROM access_plans WHERE tenant_id = ? AND id = ?"
    vals: list = [tenant_id, plan_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(
        sql,
        vals
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
        # RM-H3 values (تطابق ترتيب _COLS الجديد)
        int(p.speed_control_enabled), p.cir_down_kbps, p.cir_up_kbps,
        int(p.burst_enabled), int(p.nightly_unlimited_enabled),
        p.monthly_download_quota_mb, p.monthly_upload_quota_mb, p.monthly_combined_quota_mb,
        p.daily_download_quota_mb, p.daily_upload_quota_mb, p.daily_combined_quota_mb,
        int(p.single_use_once), p.max_consumption_times, p.ticket_validity_days, p.working_hours_limit,
        int(p.hotspot_enabled), int(p.ppp_enabled),
        p.service_scope, int(p.loan_enabled), p.max_loan_minutes, int(p.speed_override_allowed),
        p.offer_hours_from, p.offer_hours_to,
        p.metadata or "{}",
        dt_to_iso(p.deleted_at), p.deleted_by, p.delete_reason,
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
    archive_plan(tenant_id, plan_id)


def archive_plan(tenant_id: int, plan_id: int, *, actor: str = "",
                 reason: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE access_plans
            SET deleted_at = ?, deleted_by = ?, delete_reason = ?,
                enabled = 0, updated_at = ?
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
        """, (now_iso(), actor or "system", (reason or "")[:300],
              now_iso(), tenant_id, plan_id))
        return cur.rowcount > 0


def restore_plan(tenant_id: int, plan_id: int, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE access_plans
            SET deleted_at = NULL, deleted_by = '', delete_reason = '',
                enabled = 0, updated_at = ?
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NOT NULL
        """, (now_iso(), tenant_id, plan_id))
        return cur.rowcount > 0
