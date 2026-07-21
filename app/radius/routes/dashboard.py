"""Dashboard route.

RM-H2: يضيف `metrics` (مُجمَّعة في sections) بجانب `snap` الـ legacy.
كلاهما يُمرَّر للقالب — القالب يستخدم snap للـ KPI strip القديم و metrics
للأقسام الجديدة (alerts / subscribers / cards / plans / system)."""
from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..services.dashboard import get_dashboard_service
from ..services.dashboard_metrics import build_dashboard_metrics
from ..services.dashboard_reports import DashboardReportsService


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _card_batch_dashboard_summary(tenant_id: int) -> dict:
    electronic_filter = """
        LOWER(COALESCE(b.metadata, '')) NOT LIKE '%printed%'
        AND (
            LOWER(COALESCE(b.metadata, '')) LIKE '%electronic%'
            OR LOWER(COALESCE(b.batch_code, '')) LIKE '%online%'
            OR LOWER(COALESCE(b.package_name, '')) LIKE '%online%'
            OR LOWER(COALESCE(b.package_name, '')) LIKE '%electronic%'
            OR COALESCE(b.package_name, '') LIKE '%إلكترون%'
            OR COALESCE(b.package_name, '') LIKE '%الكترون%'
        )
    """
    common = """
        WITH purchased_cards AS (
            SELECT tenant_id, card_id
            FROM card_user_purchases
            WHERE tenant_id=? AND status='completed' AND card_id IS NOT NULL
            GROUP BY tenant_id, card_id
        ),
        online_cards AS (
            SELECT tenant_id, username
            FROM radacct
            WHERE tenant_id=? AND acctstoptime IS NULL
            GROUP BY tenant_id, username
        )
        SELECT
            COUNT(DISTINCT b.id) AS batches,
            COUNT(c.id) AS total,
            COALESCE(SUM(CASE
                WHEN c.deleted_at IS NULL
                 AND c.revoked=0
                 AND c.used=0
                 AND pc.card_id IS NULL
                 AND (c.expire_at IS NULL OR c.expire_at >= datetime('now'))
                THEN 1 ELSE 0 END), 0) AS available,
            COUNT(DISTINCT CASE
                WHEN c.deleted_at IS NULL
                 AND c.revoked=0
                 AND oc.username IS NOT NULL
                THEN c.id END) AS connected,
            COALESCE(SUM(CASE
                WHEN c.deleted_at IS NULL
                 AND c.used=1
                 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 10) = date('now')
                THEN 1 ELSE 0 END), 0) AS used_today,
            COALESCE(SUM(CASE
                WHEN pc.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS sold_total
        FROM card_batches b
        LEFT JOIN cards c
          ON c.tenant_id=b.tenant_id AND c.batch_id=b.id
        LEFT JOIN purchased_cards pc
          ON pc.tenant_id=c.tenant_id AND pc.card_id=c.id
        LEFT JOIN online_cards oc
          ON oc.tenant_id=c.tenant_id AND oc.username=c.username
        WHERE b.tenant_id=?
          AND b.deleted_at IS NULL
          AND {filter_clause}
    """
    sold_today_sql = """
        SELECT COUNT(*) AS c
        FROM card_user_purchases p
        JOIN cards c
          ON c.tenant_id=p.tenant_id AND c.id=p.card_id
        JOIN card_batches b
          ON b.tenant_id=c.tenant_id AND b.id=c.batch_id
        WHERE p.tenant_id=?
          AND p.status='completed'
          AND SUBSTR(COALESCE(p.created_at, ''), 1, 10) = date('now')
          AND b.deleted_at IS NULL
          AND {filter_clause}
    """

    def one(kind: str) -> dict:
        filter_clause = electronic_filter if kind == "electronic" else f"NOT ({electronic_filter})"
        try:
            row = db().execute(common.format(filter_clause=filter_clause), (tenant_id, tenant_id, tenant_id)).fetchone()
            sold_today = db().execute(
                sold_today_sql.format(filter_clause=filter_clause),
                (tenant_id,),
            ).fetchone()
        except Exception:
            return {"batches": 0, "total": 0, "available": 0, "connected": 0, "sold_today": 0}
        used_today = int(row["used_today"] or 0) if row else 0
        marketplace_sold_today = int(sold_today["c"] or 0) if sold_today else 0
        return {
            "batches": int(row["batches"] or 0) if row else 0,
            "total": int(row["total"] or 0) if row else 0,
            "available": int(row["available"] or 0) if row else 0,
            "connected": int(row["connected"] or 0) if row else 0,
            "sold_today": marketplace_sold_today if kind == "electronic" else used_today,
        }

    return {"printed": one("printed"), "electronic": one("electronic")}


def register_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/", "dashboard", dashboard_view, methods=["GET"])
    bp.add_url_rule("/dashboard", "dashboard_alias", dashboard_view, methods=["GET"])



def _provider_root_landing() -> bool:
    """هل نحن المالك الرئيسي على المسار الجذر في وضع الاستضافة المفتوحة؟"""
    try:
        from flask import request, session
        from ..auth.session_helpers import is_super_admin
        from ..core.hosting_mode import open_hosting
        if not open_hosting() or not is_super_admin():
            return False
        # مسارٌ باسم شبكة (SCRIPT_NAME مقشور) = دعمٌ داخل شبكة → لا تحويل
        return not request.environ.get("hoberadius.tenant_slug")
    except Exception:  # noqa: BLE001 — لا نكسر اللوحة على فحصٍ تجميليّ
        return False


def dashboard_view():
    # MT35 — في وضع الاستضافة، مالك المنصّة على المسار الجذر ليس مشغّل شبكة:
    # لا مشتركين له ولا كروت. لوحته هي «إدارة الاستضافة». الشبكات تُدار من
    # روابطها (/<slug>/admin/radius/) وهناك تظهر لوحة الريديوس طبيعيّةً —
    # فيبقى دخوله للدعم سليمًا ولا يرى صفرًا لا معنى له على الجذر.
    if _provider_root_landing():
        return redirect(url_for("radius.provider_home"))
    snap = get_dashboard_service().snapshot()
    # metrics لا يرفع — fallback لكل قسم
    try: metrics = build_dashboard_metrics()
    except Exception:
        metrics = {"alerts": [], "subscribers": {}, "cards": {},
                    "recent_batches": [], "plans": {}, "nas": {}, "system": {}}
    try: executive = DashboardReportsService().executive_summary()
    except Exception:
        executive = {}
    try: card_dashboard = _card_batch_dashboard_summary(_tid())
    except Exception:
        card_dashboard = {
            "printed": {"batches": 0, "total": 0, "available": 0, "connected": 0, "sold_today": 0},
            "electronic": {"batches": 0, "total": 0, "available": 0, "connected": 0, "sold_today": 0},
        }
    return render_template(
        "radius/dashboard.html",
        snap=snap,
        metrics=metrics,
        executive=executive,
        card_dashboard=card_dashboard,
    )
