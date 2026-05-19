"""Dashboard route.

RM-H2: يضيف `metrics` (مُجمَّعة في sections) بجانب `snap` الـ legacy.
كلاهما يُمرَّر للقالب — القالب يستخدم snap للـ KPI strip القديم و metrics
للأقسام الجديدة (alerts / subscribers / cards / plans / system)."""
from __future__ import annotations

from flask import Blueprint, render_template

from ..services.dashboard import get_dashboard_service
from ..services.dashboard_metrics import build_dashboard_metrics


def register_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/", "dashboard", dashboard_view, methods=["GET"])


def dashboard_view():
    snap = get_dashboard_service().snapshot()
    # metrics لا يرفع — fallback لكل قسم
    try: metrics = build_dashboard_metrics()
    except Exception:
        metrics = {"alerts": [], "subscribers": {}, "cards": {},
                    "recent_batches": [], "plans": {}, "nas": {}, "system": {}}
    return render_template("radius/dashboard.html", snap=snap, metrics=metrics)
