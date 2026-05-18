"""Subscribers repo."""
from __future__ import annotations

from typing import Optional

from ...core.types import Subscriber
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt


_COLS = (
    "username","password","user_type","service_type","plan_id","photo_url",
    "pppoe_username","pppoe_password","pppoe_ip",
    "full_name","father_name","mobile","email","address","city","district","state","zip",
    "coordinates","national_id","account_type",
    "balance","auto_renewal","status","manager_id","group_name","pool",
    "first_login_at","expire_at","last_login_at","last_seen_at",
    "mac_lock","static_ip","vlan_id","override_concurrent",
    "used_seconds","used_bytes_in","used_bytes_out","online_count",
    "beneficiary_ref","card_batch_id","remark","created_by","updated_by",
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
        balance=r["balance"] or 0.0, auto_renewal=bool(r["auto_renewal"]),
        status=r["status"], manager_id=r["manager_id"],
        group=r["group_name"] or "", pool=r["pool"] or "",
        first_login_at=parse_dt(r["first_login_at"]),
        expire_at=parse_dt(r["expire_at"]),
        last_login_at=parse_dt(r["last_login_at"]),
        last_seen_at=parse_dt(r["last_seen_at"]),
        mac_lock=r["mac_lock"], static_ip=r["static_ip"],
        vlan_id=r["vlan_id"], override_concurrent=r["override_concurrent"],
        used_seconds=r["used_seconds"] or 0,
        used_bytes_in=r["used_bytes_in"] or 0,
        used_bytes_out=r["used_bytes_out"] or 0,
        online_count=r["online_count"] or 0,
        beneficiary_ref=r["beneficiary_ref"] or "",
        card_batch_id=r["card_batch_id"],
        remark=r["remark"] or "",
        created_by=r["created_by"] or 0, updated_by=r["updated_by"] or 0,
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
    )


def list_subscribers(tenant_id: int, *,
                      status: Optional[str] = None,
                      limit: int = 500, offset: int = 0) -> list[Subscriber]:
    sql = "SELECT * FROM subscribers WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    cur = db().execute(sql, vals)
    return [_row(r) for r in cur.fetchall()]


def get_subscriber(tenant_id: int, username: str) -> Optional[Subscriber]:
    cur = db().execute(
        "SELECT * FROM subscribers WHERE tenant_id = ? AND username = ?",
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
        s.balance, int(s.auto_renewal), s.status, s.manager_id, s.group, s.pool,
        dt_to_iso(s.first_login_at), dt_to_iso(s.expire_at),
        dt_to_iso(s.last_login_at), dt_to_iso(s.last_seen_at),
        s.mac_lock, s.static_ip, s.vlan_id, s.override_concurrent,
        s.used_seconds, s.used_bytes_in, s.used_bytes_out, s.online_count,
        s.beneficiary_ref, s.card_batch_id, s.remark, s.created_by, s.updated_by,
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


def delete_subscriber(tenant_id: int, username: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM subscribers WHERE tenant_id = ? AND username = ?",
                     (tenant_id, username))


def reset_password(tenant_id: int, username: str, new_password: str) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE subscribers SET password = ?, updated_at = ? WHERE tenant_id = ? AND username = ?",
            (new_password, now_iso(), tenant_id, username)
        )


def count_subscribers(tenant_id: int, *, status: Optional[str] = None) -> int:
    sql = "SELECT COUNT(*) AS c FROM subscribers WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    return db().execute(sql, vals).fetchone()["c"]
