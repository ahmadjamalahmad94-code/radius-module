"""Operational reports JSON API for Flutter parity."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.tenant import DEFAULT_TENANT_ID
from ...radius.db.repos import operational_reports_repo
from ...radius.services.report_dates import ReportDateError, strict_int
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
        return fail("validation_error", "عبارة البحث طويلة جدًا.", status=422)
    # from/date_from و to/date_to = يوم محلّيّ شامل (كانت تُتجاهَل كليًّا)؛
    # تاريخ غير صالح/نطاق مقلوب/limit غير رقميّ → 422 عربيّ (لا صمتٌ يعيد 100).
    date_from = request.args.get("date_from") or request.args.get("from") or ""
    date_to = request.args.get("date_to") or request.args.get("to") or ""
    try:
        limit = strict_int(request.args.get("limit"), default=100, minimum=1,
                           maximum=1000, label="limit")
        offset = strict_int(request.args.get("offset"), default=0, minimum=0,
                            maximum=10**9, label="offset")
        payload = operational_reports_repo.list_report(
            _tid(),
            slug,
            query=query,
            limit=limit,
            offset=offset,
            date_from=date_from.strip(),
            date_to=date_to.strip(),
        )
    except ReportDateError as exc:
        return fail("validation_error", exc.message, status=422)
    except KeyError:
        return fail(
            "not_found",
            "تقرير التشغيل المطلوب غير متاح.",
            status=404,
            details={"slug": slug, "available": sorted(operational_reports_repo.REPORT_SLUGS)},
        )
    return ok(payload)
