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
    snap, executive, card_dashboard, access = _gate_dashboard(
        snap, metrics, executive, card_dashboard)
    return render_template(
        "radius/dashboard.html",
        snap=snap,
        metrics=metrics,
        executive=executive,
        card_dashboard=card_dashboard,
        dash_access=access,
    )


def _gate_dashboard(snap, metrics, executive, card_dashboard):
    """fix3 (F01 F8 / F02 H2): the legacy KPI snapshot is tenant-wide and the
    template falls back to it when a (scoped) metric is 0 — for anyone but the
    owner it is rebuilt from the scoped, key-gated ``metrics``; money needs
    ``reports.finance``; the card stock needs ``cards.view``."""
    from dataclasses import replace
    access = (metrics or {}).get("access") or {}
    if not access or all(access.values()):
        return snap, executive, card_dashboard, access or {}
    subs = metrics.get("subscribers") or {}
    cards = metrics.get("cards") or {}
    nas = metrics.get("nas") or {}
    plans = metrics.get("plans") or {}
    fin = (executive or {}).get("finance") or {}
    money = bool(access.get("finance"))
    try:
        snap = replace(
            snap,
            total_subscribers=int(subs.get("total") or 0),
            enabled_subscribers=int(subs.get("active") or 0),
            expired_subscribers=int(subs.get("expired") or 0),
            online_now=int(subs.get("online") or 0),
            total_cards=int(cards.get("total") or 0),
            used_cards=int(cards.get("used") or 0),
            nas_total=int(nas.get("total") or 0),
            nas_online=int(nas.get("enabled") or 0),
            plans_total=int(plans.get("total") or 0),
            admins_total=0, bytes_today_in=0, bytes_today_out=0,
            revenue_today=float(fin.get("revenue_today") or 0) if money else 0.0,
            revenue_month=float(fin.get("revenue_month") or 0) if money else 0.0,
            recent_actions=(), top_plans=())
    except Exception:  # noqa: BLE001 — never leak the tenant snapshot
        snap = None
    if not money and isinstance(executive, dict):
        executive = dict(executive)
        executive["finance"] = {"hidden": True}
    if not access.get("cards"):
        empty = {"batches": 0, "total": 0, "available": 0, "connected": 0, "sold_today": 0}
        card_dashboard = {"printed": dict(empty), "electronic": dict(empty)}
    return snap, executive, card_dashboard, access
