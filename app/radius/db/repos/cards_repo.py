"""Card batches + Cards repo."""
from __future__ import annotations

import sqlite3
import secrets
import string
from datetime import datetime, timedelta, timezone
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
        # 0 = وراثة الافتراض العام للكروت (لا نُجبِره على 1 بعد mig154).
        device_count=int(_g(r, "device_count", 0) or 0),
        device_limit_mode=_g(r, "device_limit_mode", "") or "",
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
        source_type=_g(r, "source_type", "generated") or "generated",
        original_count=int(_g(r, "original_count", r["count"]) or 0),
        settlement_count=int(_g(r, "settlement_count", _g(r, "original_count", r["count"])) or 0),
        archive_source=_g(r, "archive_source", "") or "",
        archive_policy_id=_g(r, "archive_policy_id", None),
        retention_expires_at=parse_dt(_g(r, "retention_expires_at", None)),
        auto_archive_at=parse_dt(_g(r, "auto_archive_at", None)),
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
        locked_mac=_g(r, "locked_mac", "") or "",
        disabled_reason=_g(r, "disabled_reason", "") or "",
        disabled_at=parse_dt(_g(r, "disabled_at", None)),
        disabled_by=_g(r, "disabled_by", "") or "",
        created_at=parse_dt(r["created_at"]),
        # migration 024 — safe getter so any pre-migration snapshot still loads
        card_speed_down_kbps=int(_g(r, "card_speed_down_kbps", 0) or 0),
        card_speed_up_kbps=int(_g(r, "card_speed_up_kbps", 0) or 0),
        # migration 154 — per-card device-limit override
        device_limit_mode=_g(r, "device_limit_mode", "") or "",
        device_count=int(_g(r, "device_count", 0) or 0),
        # migration 025 — freeze + soft delete
        frozen_remaining_seconds=int(_g(r, "frozen_remaining_seconds", 0) or 0),
        deleted_at=parse_dt(_g(r, "deleted_at", None)),
        deleted_by=_g(r, "deleted_by", "") or "",
        delete_reason=_g(r, "delete_reason", "") or "",
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


def _batch_operations_base_sql() -> str:
    # تطبيع طوابع radacct (صيغتا FreeRADIUS «مسافة» وISO معًا) لمقارنة نافذة
    # الحياة — المقارنة المعجمية الخام مكسورة عبر الصيغتين.
    from ...services.device_limit import acct_norm_sql
    norm_last = acct_norm_sql("COALESCE(r.acctupdatetime, r.acctstarttime)")
    return f"""
        WITH card_stats AS (
            SELECT
                batch_id,
                COUNT(*) AS total_cards,
                COALESCE(SUM(CASE WHEN deleted_at IS NULL AND revoked = 1 THEN 1 ELSE 0 END), 0) AS revoked_count,
                COALESCE(SUM(CASE
                    WHEN deleted_at IS NULL AND revoked = 0 AND used = 1
                     AND (expire_at IS NULL OR expire_at >= ?)
                    THEN 1 ELSE 0 END), 0) AS active_count,
                COALESCE(SUM(CASE WHEN deleted_at IS NULL AND used = 1 THEN 1 ELSE 0 END), 0) AS used_count,
                COALESCE(SUM(CASE
                    WHEN deleted_at IS NULL AND revoked = 0 AND used = 0
                     AND (expire_at IS NULL OR expire_at >= ?)
                    THEN 1 ELSE 0 END), 0) AS available_count,
                COALESCE(SUM(CASE
                    WHEN deleted_at IS NULL AND revoked = 0 AND expire_at IS NOT NULL AND expire_at < ?
                    THEN 1 ELSE 0 END), 0) AS expired_count
                ,COALESCE(SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS archived_count,
                COALESCE(SUM(CASE
                    WHEN deleted_at IS NULL AND revoked = 0 AND expire_at IS NOT NULL AND expire_at < ?
                    THEN 1 ELSE 0 END), 0) AS pending_archive_count
            FROM cards
            WHERE tenant_id = ?
            GROUP BY batch_id
        ),
        acct_stats AS (
            SELECT
                c.batch_id,
                COUNT(r.radacctid) AS sessions_count,
                COUNT(DISTINCT NULLIF(r.callingstationid, '')) AS unique_macs,
                -- «متصل الآن» الحقيقيّ: كان `CASE WHEN r.acctstoptime IS NULL`
                -- فوق LEFT JOIN — كرت بلا أيّ جلسات يولّد صفًّا فارغًا
                -- acctstoptime=NULL فيُحسب «متصلًا»! (حزمة 2000 كرت جديدة
                -- ظهرت «نشطة الآن 2000»). الشرطان الواجبان: صفّ radacct فعليّ
                -- (radacctid IS NOT NULL) + ضمن نافذة الحياة (لا زومبي).
                SUM(CASE WHEN r.radacctid IS NOT NULL
                          AND (r.acctstoptime IS NULL OR r.acctstoptime = '')
                          AND {norm_last} >= ?
                     THEN 1 ELSE 0 END) AS online_sessions,
                COUNT(DISTINCT CASE WHEN r.radacctid IS NOT NULL
                          AND (r.acctstoptime IS NULL OR r.acctstoptime = '')
                          AND {norm_last} >= ?
                     THEN c.username END) AS online_cards_now
            FROM cards c
            LEFT JOIN radacct r
              ON r.tenant_id = c.tenant_id AND r.username = c.username
            WHERE c.tenant_id = ?
            GROUP BY c.batch_id
        ),
        speed_stats AS (
            SELECT
                card_batch_id,
                COUNT(*) AS speed_rules_count,
                COALESCE(SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END), 0) AS active_speed_rules
            FROM bandwidth_schedules
            WHERE tenant_id = ? AND target_type = 'card_batch'
            GROUP BY card_batch_id
        )
    """


def _batch_operations_base_params(tenant_id: int) -> list:
    """معاملات CTEs الأساس بترتيبها: card_stats(now×4, tid) ثم
    acct_stats(cutoff×2, tid) ثم speed_stats(tid). cutoff = عتبة نافذة الحياة
    بصيغة «مسافة» مُطبَّعة (نفس نافذة live_sessions)."""
    from ...services.device_limit import to_space_ts
    from ...services.live_sessions import _cutoff_dt
    now = now_iso()
    cutoff = to_space_ts(_cutoff_dt(None).isoformat())
    return [now, now, now, now, int(tenant_id),
            cutoff, cutoff, int(tenant_id), int(tenant_id)]


def _batch_operations_conditions(*, status: str = "", q: str = "",
                                 plan_id: Optional[int] = None,
                                 manager: str = "",
                                 distributor_id: Optional[int] = None,
                                 owner_admin_id: Optional[int] = None) -> tuple[list[str], list[Any]]:
    where = ["b.tenant_id = ?"]
    vals: list[Any] = []
    # عزل مِلكية المدير على الحِزم: حِزمه المباشرة (manager_id) ∪ حِزم موزّعيه
    # (distributor_id ضمن موزّعيه). يُطبَّق خادميًّا حين «عرض كل حزم البطاقات» مُطفأة.
    if owner_admin_id is not None:
        where.append(
            "(b.manager_id = ? OR b.distributor_id IN ("
            "SELECT id FROM distributors WHERE tenant_id = b.tenant_id AND admin_id = ?))"
        )
        vals.extend([int(owner_admin_id), int(owner_admin_id)])

    status = (status or "").strip().lower()
    if status in {"deleted", "archived"}:
        where.append("(b.deleted_at IS NOT NULL OR b.status IN ('deleted', 'cancelled', 'canceled', 'revoked'))")
    elif status == "all":
        pass
    else:
        where.append("b.deleted_at IS NULL")
        if status == "active":
            where.append("COALESCE(b.status, 'active') = 'active'")
        elif status == "exhausted":
            where.append("COALESCE(cs.total_cards, 0) > 0")
            where.append("COALESCE(cs.available_count, 0) = 0")
        elif status == "available":
            where.append("COALESCE(cs.available_count, 0) > 0")
        elif status in {"used", "in_use"}:
            where.append("COALESCE(cs.active_count, 0) > 0")
        elif status == "expired":
            where.append("COALESCE(cs.expired_count, 0) > 0")
        elif status in {"revoked", "cancelled", "canceled"}:
            where.append("COALESCE(cs.revoked_count, 0) > 0")

    if q:
        like = f"%{q.strip()}%"
        where.append(
            "(b.batch_code LIKE ? OR b.package_name LIKE ? OR b.service_name LIKE ? "
            "OR b.created_by LIKE ? OR p.name LIKE ?)"
        )
        vals.extend([like, like, like, like, like])
    if plan_id:
        where.append("b.plan_id = ?")
        vals.append(plan_id)
    if manager:
        like = f"%{manager.strip()}%"
        where.append("(CAST(b.manager_id AS TEXT) = ? OR b.created_by LIKE ?)")
        vals.extend([manager.strip(), like])
    if distributor_id:
        where.append("b.distributor_id = ?")
        vals.append(distributor_id)
    return where, vals


def _operation_status_from_row(item: dict) -> str:
    status = (item.get("status") or "active").strip().lower()
    if item.get("deleted_at"):
        return "deleted"
    if status in {"deleted", "cancelled", "canceled", "revoked"}:
        return status
    if int(item.get("total_cards") or 0) and int(item.get("available_count") or 0) == 0:
        return "exhausted"
    return status or "active"


def list_batch_operations(
    tenant_id: int,
    *,
    q: str = "",
    status: str = "",
    plan_id: Optional[int] = None,
    manager: str = "",
    distributor_id: Optional[int] = None,
    owner_admin_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    now = now_iso()
    where, vals = _batch_operations_conditions(
        status=status,
        q=q,
        plan_id=plan_id,
        manager=manager,
        distributor_id=distributor_id,
        owner_admin_id=owner_admin_id,
    )
    sql = _batch_operations_base_sql() + f"""
        SELECT
            b.*,
            p.name AS plan_name,
            p.currency AS plan_currency,
            p.speed_down_kbps AS plan_speed_down_kbps,
            p.speed_up_kbps AS plan_speed_up_kbps,
            p.quota_total_mb AS plan_quota_total_mb,
            p.duration_minutes AS plan_duration_minutes,
            -- MT72 — «مدة البطاقة» في القائمة كانت تسقط على الدقائق الخامّة
            -- («360 دقيقة»)، وتُعرض «—» لباقةٍ مدّتها تقويميّة (أسبوعيّ/شهريّ:
            -- duration_minutes=0 والمدة في validity_days). نُصدّر الصلاحية
            -- كي يَعرضها القالب «7 أيّام» بدل شرطة.
            p.validity_days AS plan_validity_days,
            COALESCE(NULLIF(mo.full_name, ''), mo.username,
                     CASE WHEN b.created_by IN ('card_marketplace', 'card_marketplace_backfill')
                          THEN 'سوق البطاقات الإلكتروني'
                          ELSE NULLIF(b.created_by, '') END,
                     CAST(NULLIF(b.manager_id, 0) AS TEXT)) AS manager_display_name,
            d.display_name AS distributor_display_name,
            d.name AS distributor_name,
            COALESCE(cs.total_cards, 0) AS total_cards,
            COALESCE(cs.available_count, 0) AS available_count,
            COALESCE(cs.active_count, 0) AS active_count,
            COALESCE(cs.used_count, 0) AS used_count,
            COALESCE(cs.expired_count, 0) AS expired_count,
            COALESCE(cs.revoked_count, 0) AS revoked_count,
            COALESCE(cs.archived_count, 0) AS archived_count,
            COALESCE(cs.pending_archive_count, 0) AS pending_archive_count,
            COALESCE(cs.available_count, 0) AS remaining_count,
            COALESCE(cs.available_count, 0) + COALESCE(cs.active_count, 0) AS operational_remaining_count,
            COALESCE(a.sessions_count, 0) AS sessions_count,
            COALESCE(a.unique_macs, 0) AS unique_macs,
            COALESCE(a.online_sessions, 0) AS online_sessions,
            COALESCE(a.online_cards_now, 0) AS online_cards_now,
            COALESCE(ss.speed_rules_count, 0) AS speed_rules_count,
            COALESCE(ss.active_speed_rules, 0) AS active_speed_rules,
            CASE
                WHEN COALESCE(b.price_per_card, 0) > 0 THEN b.price_per_card
                WHEN COALESCE(b.total_price, 0) > 0 AND COALESCE(b.generated, 0) > 0
                    THEN b.total_price * 1.0 / b.generated
                ELSE 0
            END AS estimated_unit_price
        FROM card_batches b
        LEFT JOIN access_plans p
          ON p.tenant_id = b.tenant_id AND p.id = b.plan_id
        LEFT JOIN admins mo
          ON mo.id = b.manager_id
        LEFT JOIN distributors d
          ON d.tenant_id = b.tenant_id AND d.id = b.distributor_id
        LEFT JOIN card_stats cs ON cs.batch_id = b.id
        LEFT JOIN acct_stats a ON a.batch_id = b.id
        LEFT JOIN speed_stats ss ON ss.card_batch_id = b.id
        WHERE {" AND ".join(where)}
        ORDER BY b.id DESC
        LIMIT ? OFFSET ?
    """
    params = [*_batch_operations_base_params(tenant_id), tenant_id, *vals, limit, offset]
    rows = [row_to_dict(row) for row in db().execute(sql, params).fetchall()]
    for item in rows:
        item["operational_status"] = _operation_status_from_row(item)
    return rows


def count_batch_operations(
    tenant_id: int,
    *,
    q: str = "",
    status: str = "",
    plan_id: Optional[int] = None,
    manager: str = "",
    distributor_id: Optional[int] = None,
    owner_admin_id: Optional[int] = None,
) -> int:
    now = now_iso()
    where, vals = _batch_operations_conditions(
        status=status,
        q=q,
        plan_id=plan_id,
        manager=manager,
        distributor_id=distributor_id,
        owner_admin_id=owner_admin_id,
    )
    sql = _batch_operations_base_sql() + f"""
        SELECT COUNT(*) AS c
        FROM card_batches b
        LEFT JOIN access_plans p
          ON p.tenant_id = b.tenant_id AND p.id = b.plan_id
        LEFT JOIN card_stats cs ON cs.batch_id = b.id
        WHERE {" AND ".join(where)}
    """
    row = db().execute(
        sql,
        [*_batch_operations_base_params(tenant_id), tenant_id, *vals],
    ).fetchone()
    return int(row["c"] or 0) if row else 0


def batch_operations_totals(
    tenant_id: int,
    *,
    q: str = "",
    status: str = "",
    plan_id: Optional[int] = None,
    manager: str = "",
    distributor_id: Optional[int] = None,
    owner_admin_id: Optional[int] = None,
) -> dict:
    now = now_iso()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    month = datetime.utcnow().strftime("%Y-%m")
    year = datetime.utcnow().strftime("%Y")
    where, vals = _batch_operations_conditions(
        status=status,
        q=q,
        plan_id=plan_id,
        manager=manager,
        distributor_id=distributor_id,
        owner_admin_id=owner_admin_id,
    )
    sql = _batch_operations_base_sql() + f"""
        SELECT
            COUNT(DISTINCT b.id) AS batch_count,
            -- MT73 — عدّ الكروت عبر **كل** الحزم المُرشَّحة. بطاقة «إجمالي
            -- الكروت» كانت تُجمَع في القالب بحلقةٍ على الصفحة المعروضة فقط،
            -- فتُظهر 1,099 بدل 7,555 وتتغيّر بتغيّر الصفحة — رقمُ جردٍ ومالٍ
            -- مُضلِّل. DISTINCT ضروريّ: الاستعلام يَربط الكروت فتتضاعف الصفوف.
            COUNT(DISTINCT c.id) AS total_cards,
            COALESCE(SUM(CASE WHEN b.total_price > 0 THEN b.total_price ELSE b.price_per_card * b.generated END), 0) AS configured_value,
            COALESCE(SUM(CASE WHEN c.used = 1 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 10) = ? THEN 1 ELSE 0 END), 0) AS used_today,
            COALESCE(SUM(CASE WHEN c.used = 1 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 7) = ? THEN 1 ELSE 0 END), 0) AS used_month,
            COALESCE(SUM(CASE WHEN c.used = 1 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 4) = ? THEN 1 ELSE 0 END), 0) AS used_year,
            COALESCE(SUM(CASE
                WHEN c.used = 1 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 10) = ?
                THEN CASE
                    WHEN b.price_per_card > 0 THEN b.price_per_card
                    WHEN b.total_price > 0 AND b.generated > 0 THEN b.total_price * 1.0 / b.generated
                    ELSE 0
                END ELSE 0 END), 0) AS value_today,
            COALESCE(SUM(CASE
                WHEN c.used = 1 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 7) = ?
                THEN CASE
                    WHEN b.price_per_card > 0 THEN b.price_per_card
                    WHEN b.total_price > 0 AND b.generated > 0 THEN b.total_price * 1.0 / b.generated
                    ELSE 0
                END ELSE 0 END), 0) AS value_month,
            COALESCE(SUM(CASE
                WHEN c.used = 1 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 4) = ?
                THEN CASE
                    WHEN b.price_per_card > 0 THEN b.price_per_card
                    WHEN b.total_price > 0 AND b.generated > 0 THEN b.total_price * 1.0 / b.generated
                    ELSE 0
                END ELSE 0 END), 0) AS value_year
        FROM card_batches b
        LEFT JOIN access_plans p
          ON p.tenant_id = b.tenant_id AND p.id = b.plan_id
        LEFT JOIN card_stats cs ON cs.batch_id = b.id
        LEFT JOIN cards c
          ON c.tenant_id = b.tenant_id AND c.batch_id = b.id
        WHERE {" AND ".join(where)}
    """
    params = [
        *_batch_operations_base_params(tenant_id),
        today, month, year, today, month, year,
        tenant_id, *vals,
    ]
    row = db().execute(sql, params).fetchone()
    data = row_to_dict(row) if row else {}
    return {
        "batch_count": int(data.get("batch_count") or 0),
        # MT73 — عدّ الكروت عبر كل الحزم المُرشَّحة (القاموس يُبنى بمفاتيح
        # صريحة، فأيّ عمودٍ جديد يُسقَط ما لم يُضَف هنا أيضًا).
        "total_cards": int(data.get("total_cards") or 0),
        "configured_value": float(data.get("configured_value") or 0),
        "used_today": int(data.get("used_today") or 0),
        "used_month": int(data.get("used_month") or 0),
        "used_year": int(data.get("used_year") or 0),
        "value_today": float(data.get("value_today") or 0),
        "value_month": float(data.get("value_month") or 0),
        "value_year": float(data.get("value_year") or 0),
    }


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
                time_value, time_unit, device_count, device_limit_mode, duration_mode,
                auto_renew_after_first_use, transfer_to_student_status_on_connect,
                close_user_session_on_disconnect, allow_entry_by_previous_card_palestine,
                total_price, metadata)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
              b.time_value, b.time_unit, b.device_count, b.device_limit_mode, b.duration_mode,
              int(b.auto_renew_after_first_use), int(b.transfer_to_student_status_on_connect),
              int(b.close_user_session_on_disconnect), int(b.allow_entry_by_previous_card_palestine),
              b.total_price, b.metadata or "{}"))
        new_id = cur.lastrowid
        original_count = int(getattr(b, "original_count", 0) or b.count or 0)
        settlement_count = int(getattr(b, "settlement_count", 0) or original_count)
        conn.execute(
            """
            UPDATE card_batches
            SET source_type = ?, original_count = ?, settlement_count = ?,
                distributor_id = ?, assigned_to = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                getattr(b, "source_type", "") or "generated",
                original_count,
                settlement_count,
                getattr(b, "distributor_id", None),
                str(getattr(b, "distributor_id", "") or ""),
                b.tenant_id,
                new_id,
            ),
        )
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
    "device_limit_mode",
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
            SET deleted_at = ?, deleted_by = ?, delete_reason = ?, status = 'deleted',
                archive_source = 'manual', archive_policy_id = NULL,
                retention_expires_at = NULL, auto_archive_at = NULL
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
        """, (now_iso(), actor or "system", (reason or "")[:300], tenant_id, batch_id))
        return cur.rowcount > 0


def restore_batch(tenant_id: int, batch_id: int, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute("""
            UPDATE card_batches
            SET deleted_at = NULL, deleted_by = '', delete_reason = '', status = 'active',
                archive_source = '', archive_policy_id = NULL,
                retention_expires_at = NULL, auto_archive_at = NULL
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
            COALESCE(SUM(CASE WHEN deleted_at IS NULL AND revoked = 1 THEN 1 ELSE 0 END), 0) AS revoked_count,
            COALESCE(SUM(CASE
                WHEN deleted_at IS NULL AND revoked = 0 AND used = 1 AND (expire_at IS NULL OR expire_at >= ?)
                THEN 1 ELSE 0 END), 0) AS active_count,
            COALESCE(SUM(CASE
                WHEN deleted_at IS NULL AND revoked = 0 AND used = 0 AND (expire_at IS NULL OR expire_at >= ?)
                THEN 1 ELSE 0 END), 0) AS available_count,
            COALESCE(SUM(CASE
                WHEN deleted_at IS NULL AND revoked = 0 AND expire_at IS NOT NULL AND expire_at < ?
                THEN 1 ELSE 0 END), 0) AS expired_count
            ,COALESCE(SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS archived_count,
            COALESCE(SUM(CASE
                WHEN deleted_at IS NULL AND revoked = 0 AND expire_at IS NOT NULL AND expire_at < ?
                THEN 1 ELSE 0 END), 0) AS pending_archive_count
        FROM cards
        WHERE tenant_id = ? AND batch_id = ?
        """,
        (now, now, now, now, tenant_id, batch_id),
    ).fetchone()
    total_cards = int(row["total_cards"] or 0)
    available_count = int(row["available_count"] or 0)
    active_count = int(row["active_count"] or 0)
    expired_count = int(row["expired_count"] or 0)
    revoked_count = int(row["revoked_count"] or 0)
    archived_count = int(row["archived_count"] or 0)
    pending_archive_count = int(row["pending_archive_count"] or 0)

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
        "source_type": batch.source_type,
        "original_count": batch.original_count or batch.count or batch.generated,
        "settlement_count": batch.settlement_count or batch.original_count or batch.count,
        "generated_count": batch.generated,
        "used_counter": batch.used,
        "total_cards": total_cards,
        "available_count": available_count,
        "active_count": active_count,
        "expired_count": expired_count,
        "revoked_count": revoked_count,
        "archived_count": archived_count,
        "pending_archive_count": pending_archive_count,
        "remaining_count": available_count,
        "operational_remaining_count": available_count + active_count,
        "deleted_at": dt_to_iso(batch.deleted_at),
        "deleted_by": batch.deleted_by or None,
        "delete_reason": batch.delete_reason or None,
        "archive_source": batch.archive_source or None,
        "archive_policy_id": batch.archive_policy_id,
        "retention_expires_at": dt_to_iso(batch.retention_expires_at),
        "created_at": dt_to_iso(batch.created_at),
        "expires_at": dt_to_iso(batch.expire_at),
    }


_CHARSETS = {
    "digits": string.digits,
    "alpha": string.ascii_lowercase,
    "mixed": string.ascii_lowercase + string.digits,
    # MT88 — «قوي» كان يسقط على mixed فيُنتج نفس «متوسط» تمامًا، والواجهة
    # تَعِد بـ«حروف+أرقام+رموز». الرموز مستبعَدةٌ عمدًا: كلمة الكرت تُطبع
    # وتُكتب يدويًّا في بوابة الهوتسبوت، والرمز يُخطئ فيه الزبون ويَكسر
    # بعض تدفّقات الإدخال. القوّة تأتي من حالتَي الحرف بدلًا منها.
    "strong": string.ascii_letters + string.digits,
}


def _random_str(n: int, *, charset: str = "digits") -> str:
    alpha = _CHARSETS.get(charset, _CHARSETS["mixed"])
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


def get_card(tenant_id: int, card_id: int) -> Optional[Card]:
    cur = db().execute(
        "SELECT * FROM cards WHERE tenant_id = ? AND id = ? LIMIT 1",
        (tenant_id, card_id),
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
            c.locked_mac AS locked_mac,
            c.disabled_reason AS disabled_reason,
            c.disabled_at AS disabled_at,
            c.disabled_by AS disabled_by,
            c.used_by_subscriber_id AS used_by_subscriber_id,
            c.expire_at AS card_expire_at,
            c.revoked AS card_revoked,
            c.created_at AS card_created_at,
            -- منحةُ المشغّل ونقطةُ آخر تصفير — يقرأهما card_checker:
            -- الأولى تُضاف للميزانية، والثانية تحدّ ما يُقرأ من radacct.
            c.extra_seconds AS card_extra_seconds,
            c.usage_reset_at AS card_usage_reset_at,
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
            b.price_per_card AS batch_price_per_card,
            b.price_bulk     AS batch_price_bulk,
            b.total_price    AS batch_total_price,
            -- Accounting-mode flags + validity budget carried on the batch.
            -- These are the source of truth for the card's accounting mode
            -- and time budget (resolved by card_accounting in check_card).
            b.count_from_first_connect        AS batch_count_from_first_connect,
            b.count_by_seconds                AS batch_count_by_seconds,
            b.validity_after_first_login_days AS batch_validity_after_first_login_days,
            b.time_value                      AS batch_time_value,
            b.time_unit                       AS batch_time_unit,
            -- Owner manager (FK admins.id) — show full_name then fall
            -- back to username then to the raw id.
            mo.full_name     AS batch_manager_full_name,
            mo.username      AS batch_manager_username,
            -- Creator manager — b.created_by is stored as a username
            -- string, so we LEFT JOIN admins ON that to pick up a
            -- friendlier full_name.
            mc.full_name     AS batch_created_by_full_name,
            -- Distributor (seller) — show display_name then fall back
            -- to the canonical name.
            d.display_name   AS batch_distributor_display_name,
            d.name           AS batch_distributor_name,
            p.name AS profile_name,
            p.currency AS profile_currency,
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
        LEFT JOIN admins mo
            ON mo.id = b.manager_id
        LEFT JOIN admins mc
            ON mc.username = b.created_by
        LEFT JOIN distributors d
            ON d.id = b.distributor_id AND d.tenant_id = c.tenant_id
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


def list_card_accounting(tenant_id: int, username: str, *, limit: int = 50) -> list[dict]:
    cur = db().execute(
        """
        SELECT
            radacctid,
            acctsessionid,
            acctuniqueid,
            username,
            groupname,
            nasipaddress,
            nasportid,
            nasporttype,
            acctstarttime,
            acctupdatetime,
            acctstoptime,
            acctsessiontime,
            connectinfo_start,
            connectinfo_stop,
            acctinputoctets,
            acctoutputoctets,
            calledstationid,
            callingstationid,
            acctterminatecause,
            servicetype,
            framedprotocol,
            framedipaddress,
            framedipv6address,
            framedipv6prefix,
            framedinterfaceid,
            delegatedipv6prefix
        FROM radacct
        WHERE tenant_id = ? AND username = ?
        ORDER BY COALESCE(acctstarttime, acctupdatetime, acctstoptime, '') DESC,
                 radacctid DESC
        LIMIT ?
        """,
        (tenant_id, username, limit),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


def summarize_card_accounting(tenant_id: int, username: str,
                              *, since: str | None = None) -> dict:
    """ملخّصُ جلسات البطاقة من `radacct`.

    ``since`` = ``cards.usage_reset_at`` — حدُّ آخر تصفير. ما قبله يُتجاهل
    فلا يُبطل التصفيرَ سجلٌّ قديم، ولا تُمسح السجلّات نفسها (تبقى للتدقيق).
    ``None`` ⇒ بلا حدّ = السلوك السابق حرفيًّا.

    🔑 المقارنة نصّيّةٌ على طابعٍ ISO — لكنّ `acctstarttime` يُكتب أحيانًا
       بمسافةٍ بدل ``T`` (‏FreeRADIUS) فتفشل المقارنة الحرفيّة. نُوحّد
       الشكلين بـ`REPLACE` قبل المقارنة، وإلّا صار الحدّ ورقةً على الحائط.
    """
    where = "tenant_id = ? AND username = ?"
    params: list = [tenant_id, username]
    if since:
        where += (" AND REPLACE(COALESCE(acctstarttime,''), 'T', ' ') >= "
                  "REPLACE(?, 'T', ' ')")
        params.append(since)
    row = db().execute(
        f"""
        SELECT
            COUNT(*) AS sessions_count,
            COUNT(DISTINCT NULLIF(callingstationid, '')) AS unique_macs,
            COUNT(DISTINCT NULLIF(framedipaddress, '')) AS unique_ips,
            COUNT(DISTINCT NULLIF(nasipaddress, '')) AS unique_nas,
            SUM(CASE WHEN acctstoptime IS NULL THEN 1 ELSE 0 END) AS online_sessions,
            COALESCE(SUM(acctsessiontime), 0) AS total_session_seconds,
            COALESCE(SUM(acctinputoctets), 0) AS total_upload_bytes,
            COALESCE(SUM(acctoutputoctets), 0) AS total_download_bytes,
            MIN(acctstarttime) AS first_session_at,
            MAX(COALESCE(acctupdatetime, acctstoptime, acctstarttime)) AS last_session_at
        FROM radacct
        WHERE {where}
        """,
        params,
    ).fetchone()
    return row_to_dict(row) if row else {}


def list_card_macs(tenant_id: int, username: str, *, limit: int = 20) -> list[dict]:
    """Distinct MACs seen for `username` in radacct, newest first.

    Username comparison is case-insensitive — RADIUS clients sometimes
    fold case ('user' vs 'User'), and we don't want the Card Checker's
    MAC picker to silently miss a card's real session history just
    because of a case mismatch. The MAC value itself is also
    trimmed-and-non-empty filtered to skip stray whitespace rows.
    """
    cur = db().execute(
        """
        SELECT
            UPPER(callingstationid) AS mac,
            COUNT(*) AS sessions_count,
            MAX(COALESCE(acctupdatetime, acctstoptime, acctstarttime)) AS last_seen_at,
            SUM(CASE WHEN acctstoptime IS NULL THEN 1 ELSE 0 END) AS online_sessions
        FROM radacct
        WHERE tenant_id = ?
          AND LOWER(username) = LOWER(?)
          AND COALESCE(TRIM(callingstationid), '') != ''
        GROUP BY UPPER(callingstationid)
        ORDER BY last_seen_at DESC
        LIMIT ?
        """,
        (tenant_id, username, limit),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


def set_card_locked_mac(tenant_id: int, card_id: int, mac: str, *, actor: str = "") -> bool:
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE cards
            SET locked_mac = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (mac.strip(), tenant_id, card_id),
        )
        return bool(cur.rowcount)


def realign_batch_card_windows(tenant_id: int, batch_id: int, *,
                               window_seconds: int) -> dict:
    """MT113 — تعديل مدّة الحزمة يَسري على بطاقاتها المولَّدة.

    صنفان لا واحد:

    • **لم تبدأ** (`first_used_at IS NULL`): تُفرَّغ `expire_at` فتأخذ المدّة
      الجديدة عند أوّل دخول. وهذا يُنقذ أيضًا الحِزم القديمة التي خُتمت
      بساعة حائطٍ من التوليد قبل MT112 — يكفي أن يحفظ المشغّل الحزمة.

    • **بدأت** (`first_used_at` موجود): تُعاد الحسبة من **أوّل دخولها هي**
      (`first_used_at + المدّة الجديدة`)، لا من الآن. لو حسبناها من الآن
      لكافأنا من استهلك ثلاث ساعاتٍ بمدّةٍ كاملةٍ جديدة، ولعاقبنا من لم
      يستهلك شيئًا. والمرآة في `subscribers` تُحدَّث معها لأنّها التي
      يُنفَّذ منها الرفض.

    البطاقات المحذوفة والمجمَّدة (`frozen_remaining_seconds > 0`) لا تُمَسّ:
    للمجمَّدة رصيدٌ محفوظ يُستعاد عند التفعيل، وإعادةُ الحساب تمحوه.

    تُعيد: {"pending": عدد ما فُرِّغ، "started": عدد ما أُعيد حسابه}
    """
    if window_seconds <= 0:
        return {"pending": 0, "started": 0}

    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE cards
               SET expire_at = NULL
             WHERE tenant_id = ? AND batch_id = ?
               AND deleted_at IS NULL
               AND first_used_at IS NULL
               AND COALESCE(frozen_remaining_seconds, 0) = 0
               AND expire_at IS NOT NULL
            """,
            (tenant_id, batch_id),
        )
        pending = cur.rowcount or 0

        rows = conn.execute(
            """
            SELECT id, username, first_used_at
              FROM cards
             WHERE tenant_id = ? AND batch_id = ?
               AND deleted_at IS NULL
               AND first_used_at IS NOT NULL
               AND COALESCE(frozen_remaining_seconds, 0) = 0
            """,
            (tenant_id, batch_id),
        ).fetchall()

        started = 0
        for r in rows:
            base = parse_dt(r["first_used_at"])
            if not base:
                continue
            new_expire = dt_to_iso(base + timedelta(seconds=int(window_seconds)))
            conn.execute(
                "UPDATE cards SET expire_at = ? WHERE tenant_id = ? AND id = ?",
                (new_expire, tenant_id, r["id"]),
            )
            conn.execute(
                "UPDATE subscribers SET expire_at = ? "
                " WHERE tenant_id = ? AND username = ?",
                (new_expire, tenant_id, r["username"]),
            )
            started += 1

    return {"pending": pending, "started": started}


def set_card_password(tenant_id: int, card_id: int, password: str) -> bool:
    """MT107 — تغيير كلمة مرور بطاقةٍ واحدة في جدول الكروت.

    البطاقات الطويلة (أسبوعيّة/شهريّة) تُتداول أسابيعَ، فتُصوَّر كلمتها
    وتُسرَّب. لم يكن ثمّة سبيلٌ لتغييرها إلّا بحذف البطاقة وتوليد غيرها —
    والزبون يكون قد دفع. هذه الدالّة تُغيّر المخزَّن هنا فقط؛ والمُزامنة
    مع FreeRADIUS/المايكروتيك مسؤوليّة الخدمة (`change_card_password`)
    كي لا يبقى الجدولان متخالفَين.
    """
    pwd = (password or "").strip()
    if not pwd:
        return False
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE cards
            SET password = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (pwd, tenant_id, card_id),
        )
        return bool(cur.rowcount)


def set_card_revoked(tenant_id: int, card_id: int, revoked: bool, *,
                     actor: str = "", reason: str = "") -> bool:
    now = now_iso() if revoked else None
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE cards
            SET revoked = ?,
                disabled_reason = ?,
                disabled_at = ?,
                disabled_by = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (1 if revoked else 0, reason if revoked else "", now, actor if revoked else "", tenant_id, card_id),
        )
        return bool(cur.rowcount)


def reset_card_usage(tenant_id: int, card_id: int) -> bool:
    """تصفيرُ استخدام البطاقة — مع **حدٍّ زمنيّ** لا مسحِ سجلّات.

    🔴 مسحُ هذه الحقول وحدها لا يكفي: فاحصُ البطاقة يشتقّ «أوّل اتّصال»
       بسقوطٍ احتياطيٍّ إلى `MIN(acctstarttime)` من `radacct`، وهي لا تُمسح.
       فيُعيد بناء البداية من جلسةٍ قديمة ويحسب المتبقّي صفرًا — أي أنّ
       التصفير يُلغى في نفس اللحظة التي يقع فيها، والمشغّل يقرأ «تمّ» ولا
       يتغيّر شيء. (بطاقة 232241 · 2026-08-08.)

    🔑 `usage_reset_at` يجعل كلّ ما يُشتقّ من `radacct` يتجاهل ما قبله.
       فتبقى الجلسات للتدقيق والمحاسبة، ويرى المشغّل بطاقةً نظيفة.
    """
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE cards
            SET used = 0,
                first_used_at = NULL,
                used_by_mac = '',
                used_by_subscriber_id = NULL,
                expire_at = NULL,
                usage_reset_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (now_iso(), tenant_id, card_id),
        )
        return bool(cur.rowcount)


def grant_card_time(tenant_id: int, card_id: int, delta_seconds: int) -> dict | None:
    """يمنح البطاقةَ وقتًا (أو يخصم منها) — **ولو كانت منتهيةً أو لم تبدأ**.

    🔑 المنحة تُخزَّن في `cards.extra_seconds` وتُضاف إلى **الميزانية**، لا
       إلى `expire_at`. وهذا هو الفرق الجوهريّ: بطاقات «من أوّل اتّصال»
       تحسب المتبقّي = `أوّل اتّصال + الميزانية − الآن` ولا تقرأ `expire_at`
       إطلاقًا، فتعديلُه كان يعدّل حقلًا لا يراه أحد.

    ⚠️ ولا نضيف `delta` إلى المنحة مباشرةً: نافذةٌ أُغلقت قبل ٨ ساعات
       + ساعةٌ = ما زالت منتهيةً بسبع، فلا يشعر الزبون بشيء. بل نحسب
       المنحة كي تقع نهاية النافذة على:

           max(الآن, النهاية الحاليّة) + delta

       فالمنتهيةُ تأخذ `delta` **كاملةً من الآن**، والحيّةُ تُمدَّد من
       نهايتها فلا نسرق منها ما تبقّى. (قرارُ المالك 2026-08-08: «من الآن».)

    يُعيد {granted_seconds, extra_seconds_old, extra_seconds_new,
    remaining_before, remaining_after} أو None إن لم توجد البطاقة.
    """
    from datetime import timedelta

    from ..helpers import parse_dt
    from ...services.card_accounting import (MODE_FROM_FIRST_CONNECT,
                                             budget_seconds, remaining_seconds)

    if not delta_seconds:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with transaction() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.first_used_at, c.expire_at, c.extra_seconds,
                   b.count_from_first_connect, b.count_by_seconds,
                   b.time_value, b.time_unit,
                   b.validity_after_first_login_days
              FROM cards c
              LEFT JOIN card_batches b
                ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
             WHERE c.tenant_id = ? AND c.id = ?
            """,
            (tenant_id, card_id),
        ).fetchone()
        if row is None:
            return None

        base_budget = budget_seconds(
            validity_after_first_login_days=row["validity_after_first_login_days"] or 0,
            time_value=row["time_value"] or 0,
            time_unit=row["time_unit"] or "days",
            duration_minutes=0, validity_days=0,
        )
        old_extra = int(row["extra_seconds"] or 0)
        mode = (MODE_FROM_FIRST_CONNECT if row["count_from_first_connect"]
                else "by_seconds")
        first_conn = parse_dt(row["first_used_at"])

        def _remaining(extra: int):
            return remaining_seconds(
                mode=mode, budget=base_budget + extra, now=now,
                first_connection_at=first_conn, accounted_seconds=0,
                expire_at=parse_dt(row["expire_at"]),
            )

        rem_before = _remaining(old_extra) or 0
        if mode == MODE_FROM_FIRST_CONNECT and first_conn is not None:
            # النهاية الحاليّة = أوّل اتّصال + الميزانية. نريدها عند
            # max(الآن, النهاية) + delta ⇒ نحلّ للمنحة الجديدة.
            end = first_conn + timedelta(seconds=base_budget + old_extra)
            target = max(now, end) + timedelta(seconds=delta_seconds)
            new_extra = int((target - first_conn).total_seconds()) - base_budget
        else:
            # لم تبدأ بعد (أو محاسبةٌ بالثانية): المتبقّي = الميزانية − المستهلك،
            # فزيادةُ المنحة بـdelta تزيد المتبقّي بـdelta تمامًا.
            new_extra = old_extra + delta_seconds
        # لا نُنزل الميزانية الكلّيّة تحت الصفر مهما خُصم.
        new_extra = max(new_extra, -base_budget)

        conn.execute(
            "UPDATE cards SET extra_seconds = ? WHERE tenant_id = ? AND id = ?",
            (new_extra, tenant_id, card_id),
        )
        return {
            "granted_seconds":    delta_seconds,
            "extra_seconds_old":  old_extra,
            "extra_seconds_new":  new_extra,
            "remaining_before":   rem_before,
            "remaining_after":    _remaining(new_extra) or 0,
        }


def adjust_card_expire_at(tenant_id: int, card_id: int, delta_seconds: int):
    """Shift the card's `expire_at` by +/- delta_seconds.

    Returns a dict {expire_at_old, expire_at_new, remaining_seconds} on
    success, or None if the card doesn't exist OR isn't activated yet
    (expire_at IS NULL — there's no anchor to shift from). Callers should
    surface a clear error in that case.

    Negative delta_seconds is allowed (subtraction). The new expire_at
    is NOT clamped to >= now: subtracting past 'now' simply renders the
    card expired, which is the correct semantic for an admin override.
    """
    if delta_seconds == 0:
        return None
    with transaction() as conn:
        row = conn.execute(
            "SELECT expire_at FROM cards WHERE tenant_id = ? AND id = ?",
            (tenant_id, card_id),
        ).fetchone()
        if row is None:
            return None
        old_expire = row["expire_at"] if isinstance(row, dict) else row[0]
        if old_expire is None:
            return None
        # SQLite datetime() accepts a signed 'N seconds' modifier.
        modifier = f"{int(delta_seconds):+d} seconds"
        cur = conn.execute(
            """
            UPDATE cards
            SET expire_at = datetime(expire_at, ?)
            WHERE tenant_id = ? AND id = ? AND expire_at IS NOT NULL
            """,
            (modifier, tenant_id, card_id),
        )
        if not cur.rowcount:
            return None
        new_row = conn.execute(
            """
            SELECT expire_at,
                   CAST(strftime('%s', expire_at) AS INTEGER)
                 - CAST(strftime('%s', 'now')   AS INTEGER) AS remaining
              FROM cards
             WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, card_id),
        ).fetchone()
        if isinstance(new_row, dict):
            new_expire  = new_row.get("expire_at")
            remaining_s = int(new_row.get("remaining") or 0)
        else:
            new_expire  = new_row[0]
            remaining_s = int(new_row[1] or 0)
        return {
            "expire_at_old":     old_expire,
            "expire_at_new":     new_expire,
            "remaining_seconds": max(0, remaining_s),
        }


def freeze_card_time(tenant_id: int, card_id: int, *, actor: str = "",
                       reason: str = "") -> dict | None:
    """Disable a card AND snapshot its remaining time.

    Step 1 reads current expire_at, computes remaining_seconds against
    'now'. Step 2 sets revoked=1, stores frozen_remaining_seconds,
    NULL-s expire_at (so the translator can't accidentally hand out
    seconds while the card is parked), records disabled_at/by/reason.

    Returns {frozen_remaining_seconds, expire_at_old} on success or
    None if the card doesn't exist. If the card was already disabled
    we return frozen_remaining_seconds unchanged (idempotent).
    """
    now = now_iso()
    with transaction() as conn:
        row = conn.execute(
            """
            SELECT expire_at, revoked, frozen_remaining_seconds,
                   CAST(strftime('%s', expire_at) AS INTEGER)
                 - CAST(strftime('%s', 'now')   AS INTEGER) AS remaining
              FROM cards
             WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, card_id),
        ).fetchone()
        if row is None:
            return None
        get = (lambda k, i: row[k] if isinstance(row, dict) else row[i])
        expire_at      = get("expire_at", 0)
        already_disabled = bool(get("revoked", 1))
        already_frozen  = int(get("frozen_remaining_seconds", 2) or 0)
        live_remaining  = int(get("remaining", 3) or 0)

        # If already disabled, keep the previously-frozen value (idempotent).
        if already_disabled and already_frozen > 0:
            frozen = already_frozen
        else:
            frozen = max(0, live_remaining)

        conn.execute(
            """
            UPDATE cards
            SET revoked = 1,
                disabled_at = COALESCE(disabled_at, ?),
                disabled_by = CASE WHEN disabled_by = '' THEN ? ELSE disabled_by END,
                disabled_reason = CASE WHEN disabled_reason = '' THEN ? ELSE disabled_reason END,
                frozen_remaining_seconds = ?,
                expire_at = NULL
            WHERE tenant_id = ? AND id = ?
            """,
            (now, actor, reason, frozen, tenant_id, card_id),
        )
        return {
            "frozen_remaining_seconds": frozen,
            "expire_at_old":            expire_at,
        }


def thaw_card_time(tenant_id: int, card_id: int) -> dict | None:
    """Re-enable a card AND restore its remaining time as expire_at = now + frozen.

    Reads frozen_remaining_seconds, sets new expire_at, clears
    revoked/disabled fields and the frozen snapshot. If
    frozen_remaining_seconds is 0 (card was never frozen, OR was
    enabled before the freeze-time feature shipped), we just clear
    the revoked flag and leave expire_at untouched.

    Returns {expire_at_new, restored_seconds} on success or None.
    """
    with transaction() as conn:
        row = conn.execute(
            "SELECT frozen_remaining_seconds FROM cards "
            "WHERE tenant_id = ? AND id = ?",
            (tenant_id, card_id),
        ).fetchone()
        if row is None:
            return None
        frozen = int((row["frozen_remaining_seconds"]
                      if isinstance(row, dict) else row[0]) or 0)

        if frozen > 0:
            modifier = f"+{int(frozen)} seconds"
            conn.execute(
                """
                UPDATE cards
                SET revoked = 0,
                    disabled_at = NULL,
                    disabled_by = '',
                    disabled_reason = '',
                    frozen_remaining_seconds = 0,
                    expire_at = datetime('now', ?)
                WHERE tenant_id = ? AND id = ?
                """,
                (modifier, tenant_id, card_id),
            )
        else:
            conn.execute(
                """
                UPDATE cards
                SET revoked = 0,
                    disabled_at = NULL,
                    disabled_by = '',
                    disabled_reason = ''
                WHERE tenant_id = ? AND id = ?
                """,
                (tenant_id, card_id),
            )

        new_row = conn.execute(
            "SELECT expire_at FROM cards WHERE tenant_id = ? AND id = ?",
            (tenant_id, card_id),
        ).fetchone()
        new_expire = (new_row["expire_at"] if isinstance(new_row, dict)
                       else (new_row[0] if new_row else None))
        return {
            "expire_at_new":      new_expire,
            "restored_seconds":   frozen,
        }


def soft_delete_card(tenant_id: int, card_id: int, *,
                      actor: str = "", reason: str = "") -> bool:
    """Move a card to the recycle bin instead of dropping the row.

    Sets deleted_at + deleted_by + delete_reason, and also revokes the
    card so any future Access-Request is rejected immediately. The row
    stays so the existing recycle-bin screen can restore it.
    """
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE cards
            SET deleted_at = ?,
                deleted_by = ?,
                delete_reason = ?,
                revoked = 1,
                archive_source = 'manual',
                archive_policy_id = NULL,
                retention_expires_at = NULL,
                auto_archive_at = NULL
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
            """,
            (now, actor, reason, tenant_id, card_id),
        )
        return bool(cur.rowcount)


def restore_card_from_bin(tenant_id: int, card_id: int) -> bool:
    """Pull a soft-deleted card back out of the bin. Does NOT auto-enable
    it — the admin must explicitly re-enable from the checker."""
    with transaction() as conn:
        cur = conn.execute(
            """
            UPDATE cards
            SET deleted_at = NULL,
                deleted_by = '',
                delete_reason = '',
                archive_source = '',
                archive_policy_id = NULL,
                retention_expires_at = NULL,
                auto_archive_at = NULL
            WHERE tenant_id = ? AND id = ? AND deleted_at IS NOT NULL
            """,
            (tenant_id, card_id),
        )
        return bool(cur.rowcount)


def set_card_speed_override(tenant_id: int, card_id: int,
                              down_kbps: int, up_kbps: int) -> dict | None:
    """Persist a per-card speed override (or clear it when both are 0).

    Returns dict {username, down, up, was_override} on success or None if
    the card doesn't exist. `was_override` is True iff the row already had
    a non-zero override BEFORE this update — useful for the service layer
    to decide whether a CoA-revert is needed when clearing.

    Both fields are non-negative integers (kbps). The migration enforces
    NOT NULL DEFAULT 0 so we never have to worry about NULL semantics.
    """
    down = max(0, int(down_kbps or 0))
    up   = max(0, int(up_kbps   or 0))
    with transaction() as conn:
        row = conn.execute(
            "SELECT username, card_speed_down_kbps, card_speed_up_kbps "
            "FROM cards WHERE tenant_id = ? AND id = ?",
            (tenant_id, card_id),
        ).fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            username = row.get("username") or ""
            prev_d   = int(row.get("card_speed_down_kbps") or 0)
            prev_u   = int(row.get("card_speed_up_kbps") or 0)
        else:
            username = row[0] or ""
            prev_d   = int(row[1] or 0)
            prev_u   = int(row[2] or 0)
        cur = conn.execute(
            "UPDATE cards "
            "SET card_speed_down_kbps = ?, "
            "    card_speed_up_kbps   = ? "
            "WHERE tenant_id = ? AND id = ?",
            (down, up, tenant_id, card_id),
        )
        if not cur.rowcount:
            return None
        return {
            "username":     username,
            "down":         down,
            "up":           up,
            "was_override": bool(prev_d or prev_u),
        }


def delete_card_permanently(tenant_id: int, card_id: int) -> bool:
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM cards WHERE tenant_id = ? AND id = ?",
            (tenant_id, card_id),
        )
        return bool(cur.rowcount)


def _build_cards_filter(*, batch_id: Optional[int], used: Optional[bool],
                         revoked: Optional[bool],
                         search: Optional[str],
                         status: Optional[str] = None) -> tuple[str, list]:
    """يبني WHERE + values المشتركة بين list_cards و count_cards.
    R10.4: استُخرج إلى دالة مستقلة لمنع الفرع بين عداد و قائمة.
    إعادة تصميم صفحة الكروت: أُضيف `status` كفلتر حالة موحّد
    (available / used / expired / revoked) بنفس تعريفات لوحة الكروت
    (cards_overview) حتى تتطابق الأرقام والروابط بين الشاشتين."""
    where = ["tenant_id = ?"]
    vals: list = []
    if batch_id is not None:
        where.append("batch_id = ?"); vals.append(batch_id)
    if used is not None:
        where.append("used = ?"); vals.append(1 if used else 0)
    if revoked is not None:
        where.append("revoked = ?"); vals.append(1 if revoked else 0)
    if status == "available":
        # متاح = غير ملغى، غير مستخدم، ولم تنقضِ صلاحيته بعد
        where.append("revoked = 0 AND used = 0 "
                     "AND (expire_at IS NULL OR expire_at >= datetime('now'))")
    elif status == "used":
        where.append("used = 1")
    elif status == "expired":
        # منتهي = غير ملغى وانقضى تاريخ صلاحيته
        where.append("revoked = 0 AND expire_at IS NOT NULL "
                     "AND expire_at < datetime('now')")
    elif status == "revoked":
        where.append("revoked = 1")
    if search:
        s = search.strip()
        if s:
            # LIKE على username — مفهرس بـ tenant_id ضمنيًا، و LIKE
            # على text قصير سريع حتى بدون فهرس مخصّص.
            where.append("username LIKE ?"); vals.append(f"%{s}%")
    return " AND ".join(where), vals


def list_cards(tenant_id: int, *, batch_id: Optional[int] = None,
                used: Optional[bool] = None, revoked: Optional[bool] = None,
                search: Optional[str] = None, status: Optional[str] = None,
                limit: int = 200, offset: int = 0) -> list[Card]:
    """R10.4: أضفنا search (LIKE على username) + limit/offset للـ pagination."""
    where, vals = _build_cards_filter(
        batch_id=batch_id, used=used, revoked=revoked, search=search,
        status=status)
    sql = f"SELECT * FROM cards WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?"
    cur = db().execute(sql, [tenant_id, *vals, limit, offset])
    return [_card_row(r) for r in cur.fetchall()]


def count_cards(tenant_id: int, *, batch_id: Optional[int] = None,
                 used: Optional[bool] = None, revoked: Optional[bool] = None,
                 search: Optional[str] = None, status: Optional[str] = None) -> int:
    """R10.4: عدّ الكروت بنفس فلاتر list_cards (للـ pagination في الـ UI)."""
    where, vals = _build_cards_filter(
        batch_id=batch_id, used=used, revoked=revoked, search=search,
        status=status)
    row = db().execute(
        f"SELECT COUNT(*) AS c FROM cards WHERE {where}",
        [tenant_id, *vals]).fetchone()
    return int(row["c"] or 0) if row else 0


def cards_status_counts(tenant_id: int, *, batch_id: Optional[int] = None,
                         search: Optional[str] = None) -> dict:
    """عدّادات شريط الـ KPI في صفحة «كل الكروت» — استعلام تجميعي واحد
    ضمن نطاق البحث/الدفعة الحالي (بدون فلاتر الحالة نفسها حتى يرى
    المشغّل توزيع الحالات كاملًا مهما كان الفلتر المختار).
    التعريفات مطابقة لشروط `status` في `_build_cards_filter`."""
    where, vals = _build_cards_filter(
        batch_id=batch_id, used=None, revoked=None, search=search)
    row = db().execute(
        f"""
        SELECT
          COUNT(*) AS total,
          COALESCE(SUM(CASE WHEN revoked = 0 AND used = 0
             AND (expire_at IS NULL OR expire_at >= datetime('now')) THEN 1 ELSE 0 END), 0) AS available,
          COALESCE(SUM(CASE WHEN used = 1 THEN 1 ELSE 0 END), 0) AS used,
          COALESCE(SUM(CASE WHEN revoked = 0 AND expire_at IS NOT NULL
             AND expire_at < datetime('now') THEN 1 ELSE 0 END), 0) AS expired,
          COALESCE(SUM(CASE WHEN revoked = 1 THEN 1 ELSE 0 END), 0) AS revoked
        FROM cards WHERE {where}
        """,
        [tenant_id, *vals]).fetchone()
    keys = ("total", "available", "used", "expired", "revoked")
    return {k: int(row[k] or 0) for k in keys} if row else {k: 0 for k in keys}


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
                   expire_at: Optional[datetime] = None,
                   progress_callback=None) -> list[Card]:
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
    # MT80 — 🔴 كان `max(4, …)`: حدٌّ أدنى مزروع يتجاهل اختيار المشغّل **بصمت**.
    # طلب المالك ٥ خانات بمقدّمة «15» فخرجت ٦ (2+4) — بلا رسالةٍ ولا تحذير،
    # فيبدو الأمر عطبًا في الحفظ. نحترم الطول المطلوب: العشوائيّ = الطول ناقص
    # الثابت، وحدُّه الأدنى **١** (لا صفر — وإلّا صارت كل الكروت اسمًا واحدًا
    # مكرّرًا). حراسة التفرّد تبقى كما هي: عند نفاد التوليفات يَنتقل المولّد
    # تلقائيًّا إلى ١٢ محرفًا مختلطًا بدل أن يدور بلا نهاية.
    rand_len = max(1, username_length - fixed_len)
    for _ in range(count):
        for _try in range(40):
            uname = (username_prefix + _random_str(rand_len, charset="digits") + username_suffix).lower()
            if uname not in seen:
                seen.add(uname)
                break
        else:
            for _try in range(200):
                uname = (username_prefix + _random_str(12, charset="mixed") + username_suffix).lower()
                if uname not in seen:
                    seen.add(uname)
                    break
            else:
                raise RuntimeError("Unable to generate unique card usernames")
        pwd = _random_str(password_length, charset=password_charset)
        rows.append((tenant_id, batch_id, uname, pwd, plan_id, 0, dt_to_iso(expire_at), 0, now))

    # MT77 — 🔴 حادثة إنتاج (169.58.71.165، 2026-07-28): كانت **معاملةٌ واحدة**
    # تلفّ كل الدُفعات، فتُمسك قفل الكتابة طوال التوليد. وما إن صار راوترٌ حيًّا
    # يكتب المحاسبة في نفس قاعدة SQLite حتى تصادمَا: `database is locked`
    # ⇒ **تسقط الحزمة كلّها (0/120)** ولا يُنشأ كرتٌ واحد.
    # الآن: معاملةٌ قصيرة لكل دفعة (القفل يُفلَت بينها فتَمرّ المحاسبة)، مع
    # إعادة محاولةٍ متدرّجة عند القفل. والعدّاد يُحدَّث بالمُدرَج **فعلًا** لا
    # بالمطلوب — فلا تظهر حزمةٌ تقول ١٢٠ وفيها ٤٠ (نفس صنف عطب `generated`).
    import time as _time

    chunk_size = 100
    backoff = (0.2, 0.5, 1.0, 2.0, 3.0)
    inserted = 0
    for idx in range(0, len(rows), chunk_size):
        chunk = rows[idx:idx + chunk_size]
        for attempt in range(len(backoff) + 1):
            try:
                with transaction() as conn:
                    conn.executemany("""
                        INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, expire_at, revoked, created_at)
                        VALUES(?,?,?,?,?,?,?,?,?)
                    """, chunk)
                inserted += len(chunk)
                break
            except Exception as exc:  # noqa: BLE001
                if "locked" in str(exc).lower() and attempt < len(backoff):
                    _time.sleep(backoff[attempt])
                    continue
                # فشلٌ نهائيّ: نُثبّت ما أُدرج فعلًا كي يبقى العدّاد صادقًا
                if inserted:
                    update_batch_counters(tenant_id, batch_id,
                                          generated_delta=inserted)
                raise
        if progress_callback:
            progress_callback(inserted, count)
    update_batch_counters(tenant_id, batch_id, generated_delta=inserted)
    # نُرجع الكروت الجديدة
    cur = db().execute(
        "SELECT * FROM cards WHERE tenant_id = ? AND batch_id = ? ORDER BY id DESC LIMIT ?",
        (tenant_id, batch_id, count)
    )
    return [_card_row(r) for r in cur.fetchall()]


def existing_card_usernames(tenant_id: int, usernames: list[str]) -> set[str]:
    """أسماء المستخدمين الموجودة مسبقاً في جدول cards ضمن هذا المستأجر — لكشف
    «مكرر موجود في النظام» أثناء تحليل الاستيراد (قبل أيّ كتابة)."""
    names = [u for u in {(u or "").strip() for u in usernames} if u]
    if not names:
        return set()
    found: set[str] = set()
    # نُقسّم لدُفعات لتفادي حدّ متغيّرات SQLite (999).
    for i in range(0, len(names), 400):
        chunk = names[i:i + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = db().execute(
            f"SELECT DISTINCT username FROM cards WHERE tenant_id = ? AND username IN ({placeholders})",
            (tenant_id, *chunk),
        ).fetchall()
        found.update((r["username"] or "") for r in rows)
    return found


def import_cards(
    *,
    tenant_id: int,
    batch_id: int,
    plan_id: int,
    rows: list[dict[str, str]],
    expire_at: Optional[datetime] = None,
) -> tuple[list[Card], list[dict[str, str]]]:
    """Insert explicit card credentials into a fresh imported/external batch.

    The import path is intentionally dumb and side-effect free: it writes card
    rows and updates batch counters only. Any optional RADIUS account sync is a
    service-level decision so external files cannot accidentally touch NAS state.
    Duplicate usernames are skipped and returned to the caller.
    """
    now = now_iso()
    inserted = 0
    skipped: list[dict[str, str]] = []
    with transaction() as conn:
        for idx, item in enumerate(rows, start=1):
            username = (item.get("username") or "").strip()
            password = (item.get("password") or "").strip()
            if not username:
                skipped.append({"row": str(idx), "reason": "missing_username"})
                continue
            try:
                conn.execute(
                    """
                    INSERT INTO cards(
                        tenant_id, batch_id, username, password, plan_id,
                        used, expire_at, revoked, created_at
                    )
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        tenant_id,
                        batch_id,
                        username,
                        password,
                        plan_id,
                        0,
                        dt_to_iso(expire_at),
                        0,
                        now,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped.append({"row": str(idx), "username": username, "reason": "duplicate"})
        if inserted:
            conn.execute(
                """
                UPDATE card_batches
                SET generated = generated + ?
                WHERE tenant_id = ? AND id = ?
                """,
                (inserted, tenant_id, batch_id),
            )
    cards = list_cards(tenant_id, batch_id=batch_id, limit=inserted or 1, offset=0)
    return cards, skipped


def revoke_card(tenant_id: int, card_id: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE cards SET revoked = 1 WHERE tenant_id = ? AND id = ?",
                     (tenant_id, card_id))
