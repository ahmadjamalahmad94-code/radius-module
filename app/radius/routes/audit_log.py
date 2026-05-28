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
from ..services.mt_permissions import (
    PERM_AUDIT_VIEW, requires_perm,
)

_SEVERITY_LABELS = {
    "info": "معلومة",
    "warning": "تحذير",
    "critical": "حرجة",
}

_RESULT_LABELS = {
    "success": "نجحت",
    "failed": "فشلت",
    "partial": "جزئية",
    "cancelled": "ملغاة",
}

_ACTION_LABELS = {
    "mt.programming.hotspot.apply": "تطبيق إعدادات Hotspot",
    "mt.programming.ppp.apply": "تطبيق إعدادات PPPoE",
    "mt.programming.interface.apply": "تعديل واجهة",
    "mt.backup.create": "إنشاء نسخة احتياطية",
    "mt.deploy": "نشر إعدادات",
    "mt.apply": "تطبيق إعداد",
    "mt.toggle": "تبديل حالة",
}


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_audit_log_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/audit", "audit_log_index",
        requires_perm(PERM_AUDIT_VIEW)(audit_log_index),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/audit/<int:audit_id>", "audit_log_detail",
        requires_perm(PERM_AUDIT_VIEW)(audit_log_detail),
        methods=["GET"],
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


def _tone_for_severity(value: str | None) -> str:
    if value == "critical":
        return "danger"
    if value == "warning":
        return "warning"
    return "info"


def _tone_for_result(value: str | None) -> str:
    if value == "success":
        return "success"
    if value in {"failed", "cancelled"}:
        return "danger"
    if value == "partial":
        return "warning"
    return "muted"


def _action_label(action: str | None) -> str:
    raw = action or ""
    if raw in _ACTION_LABELS:
        return _ACTION_LABELS[raw]
    tail = raw.split(".")[-1].replace("_", " ").replace("-", " ").strip()
    return tail or "عملية"


def _decorate_row(row: dict) -> dict:
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    preview_keys = [k for k in payload.keys() if k not in ("ok",)][:4]
    severity = row.get("severity") or "info"
    result_status = row.get("result_status") or ""
    return {
        **row,
        "payload": payload,
        "preview_keys": preview_keys,
        "action_label": _action_label(row.get("action")),
        "severity_label": _SEVERITY_LABELS.get(severity, severity),
        "severity_tone": _tone_for_severity(severity),
        "result_label": _RESULT_LABELS.get(
            result_status, result_status or "غير محددة"),
        "result_tone": _tone_for_result(result_status),
        "target_label": (
            f"{row.get('target_type') or 'target'}"
            f"#{row.get('target_id') or '—'}"
        ),
    }


def audit_log_index():
    filters = {
        "router_id": _int_arg("router_id"),
        "action": _str_arg("action"),
        "severity": _str_arg("severity"),
        "result_status": _str_arg("result_status"),
        "search": _str_arg("q"),
    }
    rows = audit_repo.recent(_tid(), limit=200, **filters)
    decorated = [_decorate_row(r) for r in rows]
    summary = {
        "total": len(decorated),
        "critical": sum(
            1 for r in decorated if r.get("severity") == "critical"),
        "warnings": sum(1 for r in decorated if r.get("severity") == "warning"),
        "failed": sum(
            1 for r in decorated if r.get("result_status") == "failed"),
        "success": sum(
            1 for r in decorated if r.get("result_status") == "success"),
        "routers": len({
            r.get("router_id") for r in decorated if r.get("router_id")
        }),
        "active_filters": sum(1 for v in filters.values() if v not in (None, "")),
    }
    return render_template(
        "radius/audit_log_index.html",
        rows=decorated,
        filters=filters,
        summary=summary,
        # Surface the values the UI dropdowns need.
        severities=["info", "warning", "critical"],
        result_statuses=["success", "failed", "partial", "cancelled"],
        severity_labels=_SEVERITY_LABELS,
        result_labels=_RESULT_LABELS,
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
    row = _decorate_row(row)
    return render_template("radius/audit_log_detail.html",
                           entry=row)
