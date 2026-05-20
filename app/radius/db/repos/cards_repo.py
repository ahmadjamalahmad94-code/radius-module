"""Card batches + Cards repo."""
from __future__ import annotations

import secrets
import string
from datetime import datetime
from typing import Any, Optional

from ...core.types import Card, CardBatch
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt, row_to_dict


def _g(row: Any, key: str, default):
    """Safe getter for sqlite3.Row — fallback for snapshots before migration 013."""
    try:
        v = row[key]
        return default if v is None else v
    except (KeyError, IndexError):
        return default


def _batch_row(r) -> CardBatch:
    return CardBatch(
        id=r["id"], batch_code=r["batch_code"], package_name=r["package_name"] or "",
        plan_id=r["plan_id"], tenant_id=r["tenant_id"],
        count=r["count"], generated=r["generated"], used=r["used"],
        price_per_card=r["price_per_card"] or 0.0, price_bulk=r["price_bulk"] or 0.0,
        total_quota_mb=r["total_quota_mb"] or 0,
        username_prefix=r["username_prefix"] or "", username_suffix=r["username_suffix"] or "",
        username_length=r["username_length"] or 8,
        include_batch_number=bool(r["include_batch_number"]),
        password_length=r["password_length"] or 6,
        password_charset=r["password_charset"] or "digits",
        expire_at=parse_dt(r["expire_at"]),
        validity_after_first_login_days=r["validity_after_first_login_days"] or 0,
        count_by_seconds=bool(r["count_by_seconds"]),
        count_from_first_connect=bool(r["count_from_first_connect"]),
        on_quota_exhaust=r["on_quota_exhaust"] or "stop",
        switch_to_mac_on_connect=bool(r["switch_to_mac_on_connect"]),
        lock_to_mac_on_close=bool(r["lock_to_mac_on_close"]),
        phone_only_login=bool(r["phone_only_login"]),
        service_name=r["service_name"] or "", notes=r["notes"] or "",
        manager_id=r["manager_id"] or 0,
        created_by=r["created_by"] or "",
        status=r["status"] or "active",
        # RM-H4 fields (migration 013)
        password_generation_type=_g(r, "password_generation_type", "medium") or "medium",
        random_generation_enabled=bool(_g(r, "random_generation_enabled", 1)),
        starts_with_or_ends_with=_g(r, "starts_with_or_ends_with", "") or "",
        prefix_or_suffix_value=_g(r, "prefix_or_suffix_value", "") or "",
        time_value=_g(r, "time_value", 0) or 0,
        time_unit=_g(r, "time_unit", "days") or "days",
        device_count=_g(r, "device_count", 1) or 1,
        duration_mode=_g(r, "duration_mode", "time_unit") or "time_unit",
        auto_renew_after_first_use=bool(_g(r, "auto_renew_after_first_use", 0)),
        transfer_to_student_status_on_connect=bool(_g(r, "transfer_to_student_status_on_connect", 0)),
        close_user_session_on_disconnect=bool(_g(r, "close_user_session_on_disconnect", 0)),
        allow_entry_by_previous_card_palestine=bool(_g(r, "allow_entry_by_previous_card_palestine", 0)),
        total_price=_g(r, "total_price", 0.0) or 0.0,
        metadata=_g(r, "metadata", "{}") or "{}",
        deleted_at=parse_dt(_g(r, "deleted_at", None)),
        deleted_by=_g(r, "deleted_by", "") or "",
        delete_reason=_g(r, "delete_reason", "") or "",
        assigned_to=_g(r, "assigned_to", "") or "",
        distributor_id=_g(r, "distributor_id", None),
        created_at=parse_dt(r["created_at"]),
    )


def _card_row(r) -> Card:
    return Card(
        id=r["id"], tenant_id=r["tenant_id"], batch_id=r["batch_id"],
        username=r["username"], password=r["password"], plan_id=r["plan_id"],
        used=bool(r["used"]), first_used_at=parse_dt(r["first_used_at"]),
        used_by_mac=r["used_by_mac"] or "",
        used_by_subscriber_id=r["used_by_subscriber_id"],
        expire_at=parse_dt(r["expire_at"]),
        revoked=bool(r["revoked"]),
        created_at=parse_dt(r["created_at"]),
    )


# ─────────────── batches ───────────────

def list_batches(tenant_id: int, *, limit: int = 100, offset: int = 0,
                 include_deleted: bool = False) -> list[CardBatch]:
    sql = "SELECT * FROM card_batches WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    cur = db().execute(
        sql,
        vals
    )
    return [_batch_row(r) for r in cur.fetchall()]


def get_batch(tenant_id: int, batch_id: int,
              include_deleted: bool = True) -> Optional[CardBatch]:
    sql = "SELECT * FROM card_batches WHERE tenant_id = ? AND id = ?"
    vals: list = [tenant_id, batch_id]
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    cur = db().execute(
        sql,
        vals
    )
    row = cur.fetchone()
    return _batch_row(row) if row else None


def _build_batch_code(tenant_id: int) -> str:
    cur = db().execute("SELECT COUNT(*) AS c FROM card_batches WHERE tenant_id = ?", (tenant_id,))
    n = cur.fetchone()["c"] + 1
    return f"B-{datetime.utcnow().strftime('%Y%m%d')}-{n:04d}"


def create_batch(b: CardBatch) -> CardBatch:
    code = b.batch_code or _build_batch_code(b.tenant_id)
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id, count, generated, used,
                price_per_card, price_bulk, total_quota_mb,
                username_prefix, username_suffix, username_length, include_batch_number,
                password_length, password_charset, expire_at, validity_after_first_login_days,
                count_by_seconds, count_from_first_connect, on_quota_exhaust,
                switch_to_mac_on_connect, lock_to_mac_on_close, phone_only_login,
                service_name, notes, manager_id, created_by, status, created_at,
                password_generation_type, random_generation_enabled,
                starts_with_or_ends_with, prefix_or_suffix_value,
                time_value, time_unit, device_count, duration_mode,
                auto_renew_after_first_use, transfer_to_student_status_on_connect,
                close_user_session_on_disconnect, allow_entry_by_previous_card_palestine,
                total_price, metadata)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (b.tenant_id, code, b.package_name, b.plan_id, b.count, 0, 0,
              b.price_per_card, b.price_bulk, b.total_quota_mb,
              b.username_prefix, b.username_suffix, b.username_length,
              int(b.include_batch_number),
              b.password_length, b.password_charset, dt_to_iso(b.expire_at),
              b.validity_after_first_login_days,
              int(b.count_by_seconds), int(b.count_from_first_connect), b.on_quota_exhaust,
              int(b.switch_to_mac_on_connect), int(b.lock_to_mac_on_close), int(b.phone_only_login),
              b.service_name, b.notes, b.manager_id, b.created_by, "active", now,
              # RM-H4 columns
              b.password_generation_type, int(b.random_generation_enabled),
              b.starts_with_or_ends_with, b.prefix_or_suffix_value,
              b.time_value, b.time_unit, b.device_count, b.duration_mode,
              int(b.auto_renew_after_first_use), int(b.transfer_to_student_status_on_connect),
              int(b.close_user_session_on_disconnect), int(b.allow_entry_by_previous_card_palestine),
              b.total_price, b.metadata or "{}"))
        new_id = cur.lastrowid
    return get_batch(b.tenant_id, new_id)


def update_batch_counters(tenant_id: int, batch_id: int, *, generated_delta: int = 0, used_delta: int = 0) -> None:
    with transaction() as conn:
        conn.execute("""
            UPDATE card_batches
            SET generated = generated + ?, used = used + ?
            WHERE tenant_id = ? AND id = ?
        """, (generated_delta, used_delta, tenant_id, batch_id))


_MUTABLE_BATCH_FIELDS = {
    "package_name",
    "plan_id",
    "count",
    "price_per_card",
    "price_bulk",
    "total_quota_mb",
    "username_prefix",
    "username_suffix",
    "username_length",
    "include_batch_number",
    "password_length",
    "password_charset",
    "expire_at",
    "validity_after_first_login_days",
    "count_by_seconds",
    "count_from_first_connect",
    "on_quota_exhaust",
    "switch_to_mac_on_connect",
    "lock_to_mac_on_close",
    "phone_only_login",
    "service_name",
    "notes",
    "manager_id",
    "status",
    "password_generation_type",
    "random_generation_enabled",
    "starts_with_or_ends_with",
    "prefix_or_suffix_value",
    "time_value",
    "time_unit",
    "device_count",
    "duration_mode",
    "auto_renew_after_first_use",
    "transfer_to_student_status_on_connect",
    "close_user_session_on_disconnect",
    "allow_entry_by_previous_card_palestine",
    "total_price",
    "metadata",
    "assigned_to",
    "distributor_id",
}


def update_batch(tenant_id: int, batch_id: int, changes: dict[str, Any]) -> Optional[CardBatch]:
    """Update mutable card-batch settings without regenerating existing cards."""
    filtered = {k: v for k, v in changes.items() if k in _MUTABLE_BATCH_FIELDS}
    if not filtered:
        return get_batch(tenant_id, batch_id)
    plan_changed = "plan_id" in filtered
    assignments = ", ".join(f"{key} = ?" for key in filtered)
    values = list(filtered.values())
    with transaction() as conn:
        cur = conn.execute(
            f"""
            UPDATE card_batches
            SET {assignments}
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
            """,
            (*values, tenant_id, batch_id),
        )
        if cur.rowcount == 0:
            return None
        if plan_changed:
            new_plan_id = int(filtered["plan_id"] or 0)
            conn.execute(
                """
                UPDATE cards
                SET plan_id = ?
                WHERE tenant_id = ? AND batch_id = ? AND used = 0 AND revoked = 0
                """,
                (new_plan_id, tenant_id, batch_id),
            )
            conn.execute(
                """
                UPDATE subscribers
                SET plan_id = ?
                WHERE tenant_id = ?
                  AND card_batch_id = ?
                  AND user_type = 'card'
                  AND first_login_at IS NULL
                  AND deleted_at IS NULL
                """,
                (new_plan_id, tenant_id, batch_id),
            )
    return get_batch(tenant_id, batch_id)


# ─────────────── cards ───────────────
def archive_batch(tenant_id: int, batch_id: int, *, actor: str, reason: str = "") -> bool:
    """Mark a card batch as deleted without removing cards."""
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE card_batches
            SET deleted_at = ?, deleted_by = ?, delete_reason = ?, status = 'deleted'
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
        """, (now_iso(), actor or "system", (reason or "")[:300], tenant_id, batch_id))
        return cur.rowcount > 0


def restore_batch(tenant_id: int, batch_id: int, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE card_batches
            SET deleted_at = NULL, deleted_by = '', delete_reason = '', status = 'active'
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NOT NULL
        """, (tenant_id, batch_id))
        return cur.rowcount > 0


def batch_operational_summary(tenant_id: int, batch_id: int) -> Optional[dict]:
    """Return read-only operational counts for a card batch."""
    batch = get_batch(tenant_id, batch_id, include_deleted=True)
    if not batch:
        return None

    now = now_iso()
    row = db().execute(
        """
        SELECT
            COUNT(*) AS total_cards,
            COALESCE(SUM(CASE WHEN revoked = 1 THEN 1 ELSE 0 END), 0) AS revoked_count,
            COALESCE(SUM(CASE
                WHEN revoked = 0 AND used = 1 AND (expire_at IS NULL OR expire_at >= ?)
                THEN 1 ELSE 0 END), 0) AS active_count,
            COALESCE(SUM(CASE
                WHEN revoked = 0 AND used = 0 AND (expire_at IS NULL OR expire_at >= ?)
                THEN 1 ELSE 0 END), 0) AS available_count,
            COALESCE(SUM(CASE
                WHEN revoked = 0 AND expire_at IS NOT NULL AND expire_at < ?
                THEN 1 ELSE 0 END), 0) AS expired_count
        FROM cards
        WHERE tenant_id = ? AND batch_id = ?
        """,
        (now, now, now, tenant_id, batch_id),
    ).fetchone()
    total_cards = int(row["total_cards"] or 0)
    available_count = int(row["available_count"] or 0)
    active_count = int(row["active_count"] or 0)
    expired_count = int(row["expired_count"] or 0)
    revoked_count = int(row["revoked_count"] or 0)

    normalized_status = (batch.status or "active").strip().lower()
    if batch.deleted_at:
        operational_status = "deleted"
    elif normalized_status in {"deleted", "cancelled", "canceled", "revoked"}:
        operational_status = normalized_status
    elif total_cards and available_count == 0:
        operational_status = "exhausted"
    else:
        operational_status = normalized_status or "active"

    return {
        "batch_id": batch.id,
        "batch_code": batch.batch_code,
        "plan_id": batch.plan_id,
        "status": batch.status,
        "operational_status": operational_status,
        "configured_count": batch.count,
        "generated_count": batch.generated,
        "used_counter": batch.used,
        "total_cards": total_cards,
        "available_count": available_count,
        "active_count": active_count,
        "expired_count": expired_count,
        "revoked_count": revoked_count,
        "remaining_count": available_count,
        "deleted_at": dt_to_iso(batch.deleted_at),
        "deleted_by": batch.deleted_by or None,
        "delete_reason": batch.delete_reason or None,
        "created_at": dt_to_iso(batch.created_at),
        "expires_at": dt_to_iso(batch.expire_at),
    }


def _random_str(n: int, *, charset: str = "digits") -> str:
    alpha = string.digits if charset == "digits" else (
        string.ascii_lowercase if charset == "alpha" else string.ascii_lowercase + string.digits
    )
    return "".join(secrets.choice(alpha) for _ in range(n))


def get_card_by_username(tenant_id: int, username: str) -> Optional[Card]:
    """يبحث عن كارت برقم المستخدم — لاستخدام auth path كـ fallback لما لا
    يُوجد subscriber بنفس الاسم. يُرجع الكارت حتى لو كان revoked/used كي
    يستطيع policy_engine إصدار رفض دقيق (disabled vs user_not_found)."""
    cur = db().execute(
        "SELECT * FROM cards WHERE tenant_id = ? AND username = ? LIMIT 1",
        (tenant_id, username)
    )
    row = cur.fetchone()
    return _card_row(row) if row else None


def get_card_check_record(tenant_id: int, query: str) -> Optional[dict]:
    """Return a card with the safe context needed by the Card Checker.

    This is intentionally read-only and does not expose the card password.
    The caller may search by exact username or numeric card id.
    """
    try:
        card_id = int(query)
    except (TypeError, ValueError):
        card_id = -1
    cur = db().execute(
        """
        SELECT
            c.id AS card_id,
            c.tenant_id AS tenant_id,
            c.batch_id AS batch_id,
            c.username AS username,
            c.password AS password,
            c.plan_id AS plan_id,
            c.used AS card_used,
            c.first_used_at AS first_used_at,
            c.used_by_mac AS used_by_mac,
            c.used_by_subscriber_id AS used_by_subscriber_id,
            c.expire_at AS card_expire_at,
            c.revoked AS card_revoked,
            c.created_at AS card_created_at,
            b.batch_code AS batch_code,
            b.package_name AS batch_package_name,
            b.status AS batch_status,
            b.count AS batch_count,
            b.generated AS batch_generated,
            b.used AS batch_used,
            b.manager_id AS batch_manager_id,
            b.created_by AS batch_created_by,
            b.created_at AS batch_created_at,
            b.expire_at AS batch_expire_at,
            b.deleted_at AS batch_deleted_at,
            p.name AS profile_name,
            p.code AS profile_code,
            p.service_type AS profile_service_type,
            p.plan_type AS profile_plan_type,
            p.speed_down_kbps AS profile_speed_down_kbps,
            p.speed_up_kbps AS profile_speed_up_kbps,
            p.quota_total_mb AS profile_quota_total_mb,
            p.quota_daily_mb AS profile_quota_daily_mb,
            p.quota_monthly_mb AS profile_quota_monthly_mb,
            p.duration_minutes AS profile_duration_minutes,
            p.validity_days AS profile_validity_days,
            s.username AS subscriber_username,
            s.full_name AS subscriber_full_name,
            s.mobile AS subscriber_mobile,
            s.status AS subscriber_status,
            s.last_login_at AS subscriber_last_login_at,
            s.last_seen_at AS subscriber_last_seen_at,
            s.mac_lock AS subscriber_mac_lock,
            s.static_ip AS subscriber_static_ip
        FROM cards c
        LEFT JOIN card_batches b
            ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        LEFT JOIN access_plans p
            ON p.tenant_id = c.tenant_id AND p.id = c.plan_id
        LEFT JOIN subscribers s
            ON s.tenant_id = c.tenant_id AND s.id = c.used_by_subscriber_id
        WHERE c.tenant_id = ? AND (c.username = ? OR c.id = ?)
        LIMIT 1
        """,
        (tenant_id, query, card_id),
    )
    row = cur.fetchone()
    return row_to_dict(row) if row else None


def get_latest_card_accounting(tenant_id: int, username: str) -> Optional[dict]:
    """Return the latest radacct row for a card username, if present."""
    cur = db().execute(
        """
        SELECT
            radacctid,
            username,
            acctstarttime,
            acctupdatetime,
            acctstoptime,
            acctsessiontime,
            nasipaddress,
            callingstationid,
            framedipaddress
        FROM radacct
        WHERE tenant_id = ? AND username = ?
        ORDER BY COALESCE(acctupdatetime, acctstoptime, acctstarttime, '') DESC,
                 radacctid DESC
        LIMIT 1
        """,
        (tenant_id, username),
    )
    row = cur.fetchone()
    return row_to_dict(row) if row else None


def list_cards(tenant_id: int, *, batch_id: Optional[int] = None,
                used: Optional[bool] = None, revoked: Optional[bool] = None,
                limit: int = 200, offset: int = 0) -> list[Card]:
    sql = "SELECT * FROM cards WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if batch_id is not None:
        sql += " AND batch_id = ?"; vals.append(batch_id)
    if used is not None:
        sql += " AND used = ?"; vals.append(1 if used else 0)
    if revoked is not None:
        sql += " AND revoked = ?"; vals.append(1 if revoked else 0)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    cur = db().execute(sql, vals)
    return [_card_row(r) for r in cur.fetchall()]


def stats(tenant_id: int) -> dict:
    total = db().execute("SELECT COUNT(*) AS c FROM cards WHERE tenant_id = ?", (tenant_id,)).fetchone()["c"]
    used = db().execute("SELECT COUNT(*) AS c FROM cards WHERE tenant_id = ? AND used = 1", (tenant_id,)).fetchone()["c"]
    batches = db().execute(
        "SELECT COUNT(*) AS c FROM card_batches WHERE tenant_id = ? AND deleted_at IS NULL",
        (tenant_id,),
    ).fetchone()["c"]
    return {"total_cards": total, "used_cards": used, "total_batches": batches}


def generate_cards(*, tenant_id: int, batch_id: int, plan_id: int, count: int,
                   username_prefix: str = "", username_suffix: str = "",
                   username_length: int = 8,
                   password_length: int = 6, password_charset: str = "digits",
                   expire_at: Optional[datetime] = None) -> list[Card]:
    if count <= 0:
        return []
    now = now_iso()
    rows = []
    seen: set[str] = set()

    # سحب الـ usernames الموجودة لمنع التضارب
    cur = db().execute("SELECT username FROM cards WHERE tenant_id = ?", (tenant_id,))
    for r in cur.fetchall():
        seen.add(r["username"])

    fixed_len = len(username_prefix) + len(username_suffix)
    rand_len = max(4, username_length - fixed_len)
    for _ in range(count):
        for _try in range(40):
            uname = (username_prefix + _random_str(rand_len, charset="digits") + username_suffix).lower()
            if uname not in seen:
                seen.add(uname)
                break
        else:
            uname = (username_prefix + _random_str(12, charset="mixed") + username_suffix).lower()
            seen.add(uname)
        pwd = _random_str(password_length, charset=password_charset)
        rows.append((tenant_id, batch_id, uname, pwd, plan_id, 0, dt_to_iso(expire_at), 0, now))

    with transaction() as conn:
        conn.executemany("""
            INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, expire_at, revoked, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, rows)
    update_batch_counters(tenant_id, batch_id, generated_delta=count)
    # نُرجع الكروت الجديدة
    cur = db().execute(
        "SELECT * FROM cards WHERE tenant_id = ? AND batch_id = ? ORDER BY id DESC LIMIT ?",
        (tenant_id, batch_id, count)
    )
    return [_card_row(r) for r in cur.fetchall()]


def revoke_card(tenant_id: int, card_id: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE cards SET revoked = 1 WHERE tenant_id = ? AND id = ?",
                     (tenant_id, card_id))
