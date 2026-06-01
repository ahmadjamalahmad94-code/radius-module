"""Audit log read endpoint — read-only.

Writes happen implicitly through each service's `_audit.record(...)` call.
This endpoint surfaces the same `audit_log` table the web admin reads at
`/admin/radius/audit`, with optional filtering by actor / action /
target_type so the Flutter screen can paginate cleanly.
"""
from __future__ import annotations

import json

from flask import Blueprint, g, request

from ...radius.db.repos import audit_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/audit", "audit_list",
                    require_api_token(audit_list), methods=["GET"])


def _serialize(row: dict) -> dict:
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or "{}")
        except (TypeError, ValueError):
            payload = {}
    return {
        "id": row.get("id"),
        "tenant_id": row.get("tenant_id"),
        "actor": row.get("actor") or "",
        "action": row.get("action") or "",
        "target_type": row.get("target_type") or "",
        "target_id": row.get("target_id") or "",
        "ip_address": row.get("ip_address") or "",
        "user_agent": row.get("user_agent") or "",
        "payload": payload,
        "created_at": row.get("created_at"),
    }


def audit_list():
    try:
        limit = min(int(request.args.get("limit") or 200), 1000)
    except ValueError:
        return fail("validation_error", "قيمة limit يجب أن تكون رقمًا صحيحًا.", status=422)

    actor = (request.args.get("actor") or "").strip().lower()
    action = (request.args.get("action") or "").strip().lower()
    target_type = (request.args.get("target_type") or "").strip().lower()
    target_id = (request.args.get("target_id") or "").strip()

    # Pull a generous slab and filter in Python — audit_repo.recent is a
    # simple LIMIT query and adding indexed filters would touch the repo.
    # Acceptable: audit_log volumes are small for now.
    fetch_cap = max(limit * 4, 800)
    rows = audit_repo.recent(_tid(), limit=fetch_cap)
    items: list[dict] = []
    for r in rows:
        if actor and actor not in (r.get("actor") or "").lower():
            continue
        if action and action not in (r.get("action") or "").lower():
            continue
        if target_type and target_type != (r.get("target_type") or "").lower():
            continue
        if target_id and target_id != (r.get("target_id") or ""):
            continue
        items.append(_serialize(r))
        if len(items) >= limit:
            break
    return ok({"items": items, "count": len(items)})
