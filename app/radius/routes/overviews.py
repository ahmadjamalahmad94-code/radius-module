"""
Overviews — لوحات تحليلية صغيرة لكيانات محدّدة (users / plans).
كل القيم من DB حيّة.
"""
from __future__ import annotations

from flask import Blueprint, g, render_template

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_overview_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/users/overview", "users_overview", users_overview, methods=["GET"])
    bp.add_url_rule("/plans/overview", "plans_overview", plans_overview, methods=["GET"])


# ─────────────── users overview ───────────────

def users_overview():
    tid = _tid()
    cur = db().execute("""
        SELECT status, COUNT(*) AS c FROM subscribers WHERE tenant_id = ? GROUP BY status
    """, (tid,))
    by_status = {r["status"]: r["c"] for r in cur.fetchall()}

    cur = db().execute("""
        SELECT user_type, COUNT(*) AS c FROM subscribers WHERE tenant_id = ? GROUP BY user_type
    """, (tid,))
    by_type = {r["user_type"]: r["c"] for r in cur.fetchall()}

    total = sum(by_status.values()) or 0
    bytes_in = db().execute(
        "SELECT COALESCE(SUM(used_bytes_in),0) AS s FROM subscribers WHERE tenant_id = ?",
        (tid,)).fetchone()["s"]
    bytes_out = db().execute(
        "SELECT COALESCE(SUM(used_bytes_out),0) AS s FROM subscribers WHERE tenant_id = ?",
        (tid,)).fetchone()["s"]

    # last 10 created
    last_created = [dict(r) for r in db().execute("""
        SELECT id, username, full_name, status, created_at FROM subscribers
        WHERE tenant_id = ? ORDER BY id DESC LIMIT 10
    """, (tid,)).fetchall()]

    # users expiring soon (7 days)
    expiring_soon = [dict(r) for r in db().execute("""
        SELECT id, username, full_name, expire_at FROM subscribers
        WHERE tenant_id = ? AND expire_at IS NOT NULL
              AND date(expire_at) BETWEEN date('now') AND date('now','+7 days')
        ORDER BY expire_at LIMIT 20
    """, (tid,)).fetchall()]

    return render_template("radius/users_overview.html",
                            by_status=by_status, by_type=by_type, total=total,
                            bytes_in=bytes_in, bytes_out=bytes_out,
                            last_created=last_created, expiring_soon=expiring_soon)


# ─────────────── plans overview ───────────────

def plans_overview():
    tid = _tid()
    # أكثر الباقات اشتراكًا — مع مواصفات إضافية (سرعة/كوتا/حالة) للجدول الموحّد
    top = [dict(r) for r in db().execute("""
        SELECT p.id, p.name, p.plan_type, p.price, p.currency, p.enabled,
               p.color, p.speed_down_kbps, p.speed_up_kbps,
               p.quota_total_mb, p.validity_days,
               COUNT(s.id) AS subscribers_count
        FROM access_plans p
        LEFT JOIN subscribers s ON s.plan_id = p.id AND s.tenant_id = p.tenant_id
        WHERE p.tenant_id = ? AND p.deleted_at IS NULL
        GROUP BY p.id
        ORDER BY subscribers_count DESC, p.priority
        LIMIT 20
    """, (tid,)).fetchall()]

    # الإجماليات
    total_plans = db().execute(
        "SELECT COUNT(*) AS c FROM access_plans WHERE tenant_id = ? AND deleted_at IS NULL", (tid,)
    ).fetchone()["c"]
    enabled_plans = db().execute(
        "SELECT COUNT(*) AS c FROM access_plans WHERE tenant_id = ? AND enabled = 1 AND deleted_at IS NULL", (tid,)
    ).fetchone()["c"]

    # متوسّط / أدنى / أعلى سعر (الباقات المسعَّرة فقط)
    price_row = db().execute("""
        SELECT COALESCE(AVG(price),0) AS avg_p,
               COALESCE(MIN(price),0) AS min_p,
               COALESCE(MAX(price),0) AS max_p
        FROM access_plans
        WHERE tenant_id = ? AND deleted_at IS NULL AND price > 0
    """, (tid,)).fetchone()
    avg_price, min_price, max_price = price_row["avg_p"], price_row["min_p"], price_row["max_p"]

    # توزيع حسب النوع — عدد الباقات + عدد المشتركين لكل نوع (لمسارات so-lane)
    by_type_rows = [dict(r) for r in db().execute("""
        SELECT p.plan_type, COUNT(DISTINCT p.id) AS plans_count,
               COUNT(s.id) AS subscribers_count
        FROM access_plans p
        LEFT JOIN subscribers s ON s.plan_id = p.id AND s.tenant_id = p.tenant_id
        WHERE p.tenant_id = ? AND p.deleted_at IS NULL
        GROUP BY p.plan_type
        ORDER BY plans_count DESC
    """, (tid,)).fetchall()]
    by_type = {r["plan_type"]: r["plans_count"] for r in by_type_rows}

    # المشتركون الموزَّعون على باقات + باقات بلا مشتركين
    assigned_subs = db().execute("""
        SELECT COUNT(*) AS c FROM subscribers s
        JOIN access_plans p ON s.plan_id = p.id AND p.tenant_id = s.tenant_id
        WHERE s.tenant_id = ? AND p.deleted_at IS NULL
    """, (tid,)).fetchone()["c"]
    unused_plans = db().execute("""
        SELECT COUNT(*) AS c FROM access_plans p
        WHERE p.tenant_id = ? AND p.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM subscribers s
                          WHERE s.plan_id = p.id AND s.tenant_id = p.tenant_id)
    """, (tid,)).fetchone()["c"]

    # تقدير الإيراد (مشتركون مفعَّلون × سعر باقتهم)
    rev = db().execute("""
        SELECT COALESCE(SUM(p.price), 0) AS rev
        FROM subscribers s JOIN access_plans p ON s.plan_id = p.id
        WHERE s.tenant_id = ? AND s.status = 'enabled'
    """, (tid,)).fetchone()["rev"] or 0.0

    # آخر الباقات المُنشأة (للوحة جانبية سريعة)
    recent = [dict(r) for r in db().execute("""
        SELECT id, name, plan_type, price, enabled, created_at, color
        FROM access_plans
        WHERE tenant_id = ? AND deleted_at IS NULL
        ORDER BY id DESC LIMIT 8
    """, (tid,)).fetchall()]

    return render_template("radius/plans_overview.html",
                            top=top, total_plans=total_plans, enabled_plans=enabled_plans,
                            by_type=by_type, by_type_rows=by_type_rows,
                            revenue_estimate=rev,
                            avg_price=avg_price, min_price=min_price, max_price=max_price,
                            assigned_subs=assigned_subs, unused_plans=unused_plans,
                            recent=recent)
