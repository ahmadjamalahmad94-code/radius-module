"""Operational reports JSON API for Flutter parity."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.tenant import DEFAULT_TENANT_ID
from ...radius.db.repos import operational_reports_repo
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/operational-reports/<slug>",
        "operational_reports_detail",
        require_api_token(operational_report),
        methods=["GET"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def operational_report(slug: str):
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    if len(query) > 120:
        return fail("validation_error", "query is too long", status=422)
    try:
        payload = operational_reports_repo.list_report(
            _tid(),
            slug,
            query=query,
            limit=request.args.get("limit"),
            offset=request.args.get("offset"),
        )
    except KeyError:
        return fail(
            "not_found",
            "تقرير التشغيل المطلوب غير متاح.",
            status=404,
            details={"slug": slug, "available": sorted(operational_reports_repo.REPORT_SLUGS)},
        )
    return ok(payload)
