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
    data = build_dashboard_metrics()
    return ok(_with_flat_counter_aliases(data))


def _with_flat_counter_aliases(data: dict) -> dict:
    """Keep nested dashboard data, and add stable flat counter aliases.

    Older Flutter builds and lightweight clients historically read top-level
    names such as `online_now` and `total_cards`. The web dashboard service
    returns richer nested sections. Returning both shapes keeps the endpoint
    backward-compatible and prevents counters from rendering as zero when a
    client is deployed against a slightly different server revision.
    """
    subscribers = data.get("subscribers") if isinstance(data.get("subscribers"), dict) else {}
    cards = data.get("cards") if isinstance(data.get("cards"), dict) else {}
    plans = data.get("plans") if isinstance(data.get("plans"), dict) else {}
    nas = data.get("nas") if isinstance(data.get("nas"), dict) else {}

    merged = dict(data)
    merged.update({
        "total_subscribers": subscribers.get("total", 0),
        "active_subscribers": (
            subscribers.get("active")
            if subscribers.get("active") is not None
            else subscribers.get("enabled", 0)
        ),
        "online_now": subscribers.get("online", 0),
        "plans_total": plans.get("total", 0),
        "total_cards": cards.get("total", 0),
        "used_cards": cards.get("used", 0),
        "available_cards": cards.get("available", 0),
        "total_batches": cards.get("batches", 0),
        "nas_devices": nas.get("total", 0),
    })
    return merged
