"""Dashboard aggregate — JSON wrapper around build_dashboard_metrics().

GET /api/v1/dashboard
    → { ok: true, data: { subscribers, cards, plans, nas, system, alerts,
                          recent_batches } }

The shape mirrors the dict produced for the web template at
radius/services/dashboard_metrics.build_dashboard_metrics, so the same
service feeds both the HTML admin UI and the Flutter clients.
"""
from __future__ import annotations

from flask import Blueprint

from ..auth import require_api_token
from ..responses import ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/dashboard", "dashboard_get",
                    require_api_token(dashboard_get), methods=["GET"])


def dashboard_get():
    from ...radius.services.dashboard_metrics import build_dashboard_metrics
    return ok(build_dashboard_metrics())
