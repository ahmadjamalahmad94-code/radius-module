"""S2.2 — Audit log center.

Routes:
  GET /admin/radius/audit            list + filters
  GET /admin/radius/audit/<id>       detail (one entry)

The repo already redacts secrets at write time (S2.1), so the
templates can render `payload_json` / `before_json` / `after_json`
directly without further masking — the "***" lands at the
boundary, not in the view layer.
"""
from __future__ import annotations

import json

from flask import Blueprint, abort, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import audit_repo


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_audit_log_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/audit", "audit_log_index", audit_log_index,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/audit/<int:audit_id>", "audit_log_detail",
        audit_log_detail, methods=["GET"],
    )


def _int_arg(name: str) -> int | None:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _str_arg(name: str) -> str | None:
    raw = (request.args.get(name) or "").strip()
    return raw or None


def audit_log_index():
    filters = {
        "router_id": _int_arg("router_id"),
        "action": _str_arg("action"),
        "severity": _str_arg("severity"),
        "result_status": _str_arg("result_status"),
        "search": _str_arg("q"),
    }
    rows = audit_repo.recent(_tid(), limit=200, **filters)
    # Parse JSON columns once on the server so the template can
    # iterate cleanly. Pre-clip preview to ≤120 chars to keep
    # the row compact; the detail page shows the full picture.
    decorated = []
    for r in rows:
        try:
            payload = json.loads(r.get("payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        preview_keys = [k for k in payload.keys() if k not in ("ok",)][:4]
        decorated.append({
            **r,
            "payload": payload,
            "preview_keys": preview_keys,
        })
    return render_template(
        "radius/audit_log_index.html",
        rows=decorated,
        filters=filters,
        # Surface the values the UI dropdowns need.
        severities=["info", "warning", "critical"],
        result_statuses=["success", "failed", "partial", "cancelled"],
    )


def audit_log_detail(audit_id: int):
    row = audit_repo.get_by_id(_tid(), int(audit_id))
    if not row:
        abort(404)
    # Parse the three JSON columns for display.
    for col in ("payload_json", "before_json", "after_json"):
        try:
            row[col.replace("_json", "")] = \
                json.loads(row.get(col) or "{}")
        except (TypeError, ValueError):
            row[col.replace("_json", "")] = {}
    return render_template("radius/audit_log_detail.html",
                           entry=row)
