"""K9 — MikroTik per-router dashboard UI.

A single page at `/admin/radius/mt/<nas_id>/dashboard` that loads
data from the K3-K8 JSON APIs. The server-rendered shell only
needs `nas_devices.id`; everything dynamic comes through JS
fetching the existing endpoints, so the UI carries no fake data.

In K9.1 the page renders the KPI strip + empty placeholders for
the K9.2/K9.3 panels — those are filled in subsequent commits.
"""
from __future__ import annotations

import os

from flask import Blueprint, abort, g, render_template

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _ui_api_token() -> str:
    """The token the dashboard JS uses to call /api/v1/*.

    Mirrors the env-token logic in `app.api.auth._allowed_env_tokens`
    so the UI's calls succeed in the same dev / prod modes as a
    curl smoke test would. In production an operator MUST set
    `HOBERADIUS_API_TOKENS` (CSV); otherwise the UI receives an
    empty token and the JS surfaces an "auth not configured" error
    instead of silently failing.
    """
    raw = (os.environ.get("HOBERADIUS_API_TOKENS") or "").strip()
    if raw:
        return raw.split(",", 1)[0].strip()
    env = (os.environ.get("HOBERADIUS_ENV") or os.environ.get("FLASK_ENV") or "").lower()
    if env in {"prod", "production"}:
        return ""
    return "dev-token-please-change"


def register_mt_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/dashboard",
        "mt_dashboard",
        mt_dashboard,
        methods=["GET"],
    )


def mt_dashboard(nas_id: int):
    row = db().execute(
        "SELECT id, name, address, connection_mode, vpn_peer_address, "
        "       enabled "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        abort(404)
    nas = dict(row)
    return render_template(
        "radius/mt_dashboard.html",
        nas=nas,
        api_base="/api/v1",
        api_token=_ui_api_token(),
    )
