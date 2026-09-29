"""Dashboard route.

RM-H2: يضيف `metrics` (مُجمَّعة في sections) بجانب `snap` الـ legacy.
كلاهما يُمرَّر للقالب — القالب يستخدم snap للـ KPI strip القديم و metrics
للأقسام الجديدة (alerts / subscribers / cards / plans / system)."""
from __future__ import annotations

from flask import Blueprint, g, render_template

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
    """مخزون الكروت للوحة — يفوَّض للمصدر الوحيد المشترك مع ``/api/v1/dashboard``
    (``dashboard_metrics.card_batch_dashboard_summary``): بلا حزم مؤرشفة/محذوفة
    ولا كروتها."""
    from ..services.dashboard_metrics import card_batch_dashboard_summary
    return card_batch_dashboard_summary(tenant_id)


def register_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/", "dashboard", dashboard_view, methods=["GET"])
    bp.add_url_rule("/dashboard", "dashboard_alias", dashboard_view, methods=["GET"])


def dashboard_view():
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
