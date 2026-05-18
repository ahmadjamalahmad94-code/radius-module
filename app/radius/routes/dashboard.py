"""Dashboard route."""
from __future__ import annotations

from flask import Blueprint, render_template

from ..services.dashboard import get_dashboard_service


def register_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/", "dashboard", dashboard_view, methods=["GET"])


def dashboard_view():
    snap = get_dashboard_service().snapshot()
    return render_template("radius/dashboard.html", snap=snap)
