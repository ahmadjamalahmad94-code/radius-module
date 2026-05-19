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
    # top plans by subscriber count
    top = [dict(r) for r in db().execute("""
        SELECT p.id, p.name, p.plan_type, p.price, p.currency,
               COUNT(s.id) AS subscribers_count
        FROM access_plans p
        LEFT JOIN subscribers s ON s.plan_id = p.id AND s.tenant_id = p.tenant_id
        WHERE p.tenant_id = ?
        GROUP BY p.id
        ORDER BY subscribers_count DESC, p.priority
        LIMIT 20
    """, (tid,)).fetchall()]

    # totals
    total_plans = db().execute(
        "SELECT COUNT(*) AS c FROM access_plans WHERE tenant_id = ?", (tid,)
    ).fetchone()["c"]
    enabled_plans = db().execute(
        "SELECT COUNT(*) AS c FROM access_plans WHERE tenant_id = ? AND enabled = 1", (tid,)
    ).fetchone()["c"]

    # by type
    by_type = {r["plan_type"]: r["c"] for r in db().execute("""
        SELECT plan_type, COUNT(*) AS c FROM access_plans WHERE tenant_id = ? GROUP BY plan_type
    """, (tid,)).fetchall()}

    # revenue estimate (subscribers × plan price)
    rev = db().execute("""
        SELECT COALESCE(SUM(p.price), 0) AS rev
        FROM subscribers s JOIN access_plans p ON s.plan_id = p.id
        WHERE s.tenant_id = ? AND s.status = 'enabled'
    """, (tid,)).fetchone()["rev"] or 0.0

    return render_template("radius/plans_overview.html",
                            top=top, total_plans=total_plans, enabled_plans=enabled_plans,
                            by_type=by_type, revenue_estimate=rev)
