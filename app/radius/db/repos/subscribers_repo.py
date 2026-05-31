"""Subscribers repo."""
from __future__ import annotations

from typing import Any, Optional

from ...core.types import Subscriber
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt


def _g(row: Any, key: str, default):
    """Safe getter for sqlite3.Row — يُرجع default لو العمود غير موجود (مفيد
    لتوافق rows قديمة قبل تطبيق migration 011)."""
    try:
        v = row[key]
        return default if v is None else v
    except (KeyError, IndexError):
        return default


_COLS = (
    "username","password","user_type","service_type","plan_id","photo_url",
    "pppoe_username","pppoe_password","pppoe_ip",
    "full_name","father_name","mobile","email","address","city","district","state","zip",
    "coordinates","national_id","account_type",
    "balance","custom_price","auto_renewal","status","manager_id","group_name","pool",
    "first_login_at","expire_at","last_login_at","last_seen_at",
    "mac_lock","static_ip","vlan_id","override_concurrent",
    # RM-H1: AdvRadius extension columns (تطابق migration 011)
    "bandwidth_control_enabled","download_speed_kbps","upload_speed_kbps",
    "custom_speed","temporary_speed",
    "caller_id","primary_dns_ppp","secondary_dns_ppp","device_connection_file",
    "nationality","country","payment_method","payment_reference",
    "total_connection_time_min","daily_connection_time_min",
    "download_quota_mb","upload_quota_mb","combined_quota_mb",
    "connection_time_limit_enabled","quota_limit_enabled",
    "equal_share_download","equal_share_upload",
    "working_days","connection_schedule","device_count","allowed_macs","metadata",
    # ── usage ──
    "used_seconds","used_bytes_in","used_bytes_out","online_count",
    "beneficiary_ref","card_batch_id","remark","created_by","updated_by",
    "deleted_at","deleted_by","delete_reason",
)


def _row(r) -> Subscriber:
    return Subscriber(
        id=r["id"], tenant_id=r["tenant_id"],
        username=r["username"], password=r["password"] or "",
        user_type=r["user_type"], service_type=r["service_type"] or "Hotspot",
        plan_id=r["plan_id"], photo_url=r["photo_url"] or "/user.default.jpg",
        pppoe_username=r["pppoe_username"] or "", pppoe_password=r["pppoe_password"] or "",
        pppoe_ip=r["pppoe_ip"] or "",
        full_name=r["full_name"] or "", father_name=r["father_name"] or "",
        mobile=r["mobile"] or "", email=r["email"] or "",
        address=r["address"] or "", city=r["city"] or "",
        district=r["district"] or "", state=r["state"] or "", zip=r["zip"] or "",
        coordinates=r["coordinates"] or "", national_id=r["national_id"] or "",
        account_type=r["account_type"] or "Personal",
        balance=r["balance"] or 0.0,
        custom_price=_g(r, "custom_price", 0) or 0.0,
        auto_renewal=bool(r["auto_renewal"]),
        status=r["status"], manager_id=r["manager_id"],
        group=r["group_name"] or "", pool=r["pool"] or "",
        first_login_at=parse_dt(r["first_login_at"]),
        expire_at=parse_dt(r["expire_at"]),
        last_login_at=parse_dt(r["last_login_at"]),
        last_seen_at=parse_dt(r["last_seen_at"]),
        mac_lock=r["mac_lock"], static_ip=r["static_ip"],
        vlan_id=r["vlan_id"], override_concurrent=r["override_concurrent"],
        # RM-H1 fields — read with safe defaults (columns added in migration 011)
        bandwidth_control_enabled=bool(_g(r,"bandwidth_control_enabled",0)),
        download_speed_kbps=_g(r,"download_speed_kbps",0) or 0,
        upload_speed_kbps=_g(r,"upload_speed_kbps",0) or 0,
        custom_speed=bool(_g(r,"custom_speed",0)),
        temporary_speed=bool(_g(r,"temporary_speed",0)),
        caller_id=_g(r,"caller_id","") or "",
        primary_dns_ppp=_g(r,"primary_dns_ppp","") or "",
        secondary_dns_ppp=_g(r,"secondary_dns_ppp","") or "",
        device_connection_file=_g(r,"device_connection_file","") or "",
        nationality=_g(r,"nationality","") or "",
        country=_g(r,"country","") or "",
        payment_method=_g(r,"payment_method","") or "",
        payment_reference=_g(r,"payment_reference","") or "",
        total_connection_time_min=_g(r,"total_connection_time_min",0) or 0,
        daily_connection_time_min=_g(r,"daily_connection_time_min",0) or 0,
        download_quota_mb=_g(r,"download_quota_mb",0) or 0,
        upload_quota_mb=_g(r,"upload_quota_mb",0) or 0,
        combined_quota_mb=_g(r,"combined_quota_mb",0) or 0,
        connection_time_limit_enabled=bool(_g(r,"connection_time_limit_enabled",0)),
        quota_limit_enabled=bool(_g(r,"quota_limit_enabled",0)),
        equal_share_download=bool(_g(r,"equal_share_download",0)),
        equal_share_upload=bool(_g(r,"equal_share_upload",0)),
        working_days=_g(r,"working_days","") or "",
        connection_schedule=_g(r,"connection_schedule","") or "",
        device_count=_g(r,"device_count",1) or 1,
        allowed_macs=_g(r,"allowed_macs","") or "",
        metadata=_g(r,"metadata","{}") or "{}",
        used_seconds=r["used_seconds"] or 0,
        used_bytes_in=r["used_bytes_in"] or 0,
        used_bytes_out=r["used_bytes_out"] or 0,
        online_count=r["online_count"] or 0,
        beneficiary_ref=r["beneficiary_ref"] or "",
        card_batch_id=r["card_batch_id"],
        remark=r["remark"] or "",
        created_by=r["created_by"] or 0, updated_by=r["updated_by"] or 0,
        deleted_at=parse_dt(_g(r, "deleted_at", None)),
        deleted_by=_g(r, "deleted_by", "") or "",
        delete_reason=_g(r, "delete_reason", "") or "",
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
    )


def list_subscribers(tenant_id: int, *,
                      status: Optional[str] = None,
                      user_type: Optional[str] = None,
                      search: Optional[str] = None,
                      limit: int = 500, offset: int = 0,
                      include_deleted: bool = False) -> list[Subscriber]:
    """قائمة المشتركين مع فلاتر SQL.

    R9.0:
      - `user_type`: استبعاد سجلات mirror التي يُنشئها card generation
        (user_type='card'). Default None = جميع الأنواع للتوافق العكسي.
        صفحة "المشتركين" تمرّر 'subscriber' لتُظهر المشتركين الحقيقيين فقط.
      - `search`: pushdown إلى SQL عبر LIKE على username/full_name/mobile.
        كان سابقاً يفلتر بعد LIMIT في الـ service → مع 2000+ سجلّ يفوت
        المستخدم البحث عنه. الآن يصل لكل DB قبل LIMIT.
    """
    sql = "SELECT * FROM subscribers WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    if status:
        sql += " AND status = ?"
        vals.append(status)
    if user_type:
        sql += " AND user_type = ?"
        vals.append(user_type)
    if search:
        pat = f"%{search}%"
        sql += (" AND (username LIKE ? OR full_name LIKE ? OR mobile LIKE ?)")
        vals += [pat, pat, pat]
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    cur = db().execute(sql, vals)
    return [_row(r) for r in cur.fetchall()]


def get_subscriber(tenant_id: int, username: str, *,
                   include_deleted: bool = False) -> Optional[Subscriber]:
    sql = "SELECT * FROM subscribers WHERE tenant_id = ? AND username = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(
        sql,
        (tenant_id, username)
    )
    row = cur.fetchone()
    return _row(row) if row else None


def upsert_subscriber(s: Subscriber) -> Subscriber:
    values = (
        s.username, s.password, s.user_type, s.service_type, s.plan_id, s.photo_url,
        s.pppoe_username, s.pppoe_password, s.pppoe_ip,
        s.full_name, s.father_name, s.mobile, s.email, s.address, s.city, s.district, s.state, s.zip,
        s.coordinates, s.national_id, s.account_type,
        s.balance, s.custom_price, int(s.auto_renewal), s.status, s.manager_id, s.group, s.pool,
        dt_to_iso(s.first_login_at), dt_to_iso(s.expire_at),
        dt_to_iso(s.last_login_at), dt_to_iso(s.last_seen_at),
        s.mac_lock, s.static_ip, s.vlan_id, s.override_concurrent,
        # RM-H1 fields (تطابق ترتيب _COLS بالضبط)
        int(s.bandwidth_control_enabled), s.download_speed_kbps, s.upload_speed_kbps,
        int(s.custom_speed), int(s.temporary_speed),
        s.caller_id, s.primary_dns_ppp, s.secondary_dns_ppp, s.device_connection_file,
        s.nationality, s.country, s.payment_method, s.payment_reference,
        s.total_connection_time_min, s.daily_connection_time_min,
        s.download_quota_mb, s.upload_quota_mb, s.combined_quota_mb,
        int(s.connection_time_limit_enabled), int(s.quota_limit_enabled),
        int(s.equal_share_download), int(s.equal_share_upload),
        s.working_days, s.connection_schedule, s.device_count, s.allowed_macs, s.metadata or "{}",
        s.used_seconds, s.used_bytes_in, s.used_bytes_out, s.online_count,
        s.beneficiary_ref, s.card_batch_id, s.remark, s.created_by, s.updated_by,
        dt_to_iso(s.deleted_at), s.deleted_by, s.delete_reason,
    )
    now = now_iso()
    with transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM subscribers WHERE tenant_id = ? AND username = ?",
            (s.tenant_id, s.username)
        ).fetchone()
        if existing:
            sets = ", ".join(f"{c}=?" for c in _COLS)
            conn.execute(
                f"UPDATE subscribers SET {sets}, updated_at=? WHERE tenant_id=? AND id=?",
                (*values, now, s.tenant_id, existing["id"])
            )
            new_id = existing["id"]
        else:
            placeholders = ",".join(["?"] * (len(_COLS) + 2))
            cur = conn.execute(
                f"INSERT INTO subscribers(tenant_id, {', '.join(_COLS)}, created_at) "
                f"VALUES({placeholders})",
                (s.tenant_id, *values, now)
            )
            new_id = cur.lastrowid
    return get_subscriber(s.tenant_id, s.username)


def archive_subscriber(tenant_id: int, username: str, *, actor: str = "",
                       reason: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE subscribers
            SET deleted_at = ?, deleted_by = ?, delete_reason = ?,
                status = 'disabled', updated_at = ?
            WHERE tenant_id = ? AND username = ? AND deleted_at IS NULL
            """,
            (now_iso(), actor or "system", (reason or "")[:300],
             now_iso(), tenant_id, username),
        )
        return cur.rowcount > 0


def restore_subscriber(tenant_id: int, username: str, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE subscribers
            SET deleted_at = NULL, deleted_by = '', delete_reason = '',
                status = 'disabled', updated_at = ?
            WHERE tenant_id = ? AND username = ? AND deleted_at IS NOT NULL
            """,
            (now_iso(), tenant_id, username),
        )
        return cur.rowcount > 0


def delete_subscriber(tenant_id: int, username: str) -> None:
    archive_subscriber(tenant_id, username)


def reset_password(tenant_id: int, username: str, new_password: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE subscribers SET password = ?, updated_at = ? WHERE tenant_id = ? AND username = ?",
            (new_password, now_iso(), tenant_id, username)
        )


def count_subscribers(tenant_id: int, *, status: Optional[str] = None,
                       user_type: Optional[str] = None) -> int:
    sql = "SELECT COUNT(*) AS c FROM subscribers WHERE tenant_id = ? AND deleted_at IS NULL"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    if user_type:
        sql += " AND user_type = ?"
        vals.append(user_type)
    return db().execute(sql, vals).fetchone()["c"]
