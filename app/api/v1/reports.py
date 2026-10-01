"""Customer service report API foundations."""
from __future__ import annotations

from flask import Blueprint, Response, g, request

from ...radius.core.errors import RadiusValidationError
from ...radius.services.accounting import AccountingService, service_from_context
from ..auth import require_api_token
from ..json_input import json_object
from ..responses import fail, ok
from .paging import PagingError, page_args


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/reports/snapshots",
        "reports_snapshots_list",
        require_api_token(reports_snapshots_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/reports/snapshots",
        "reports_snapshots_create",
        require_api_token(reports_snapshots_create),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/reports/snapshots/<int:snapshot_id>",
        "reports_snapshots_get",
        require_api_token(reports_snapshots_get),
        methods=["GET"],
    )
    for slug, report_type in (
        ("sales", "daily"),
        ("sales/daily", "daily"),
        ("sales/monthly", "monthly"),
        ("sales/yearly", "yearly"),
        ("payments", "subscriber_payments"),
        ("loans", "loans"),
        ("activations", "activations"),
        ("card-sales", "card_sales"),
        ("profit-loss", "profit_loss"),
        ("distributor-debts", "distributor_debts"),
    ):
        bp.add_url_rule(
            f"/reports/{slug}",
            "reports_" + slug.replace("/", "_").replace("-", "_"),
            require_api_token(_report_view(report_type)),
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/reports/{slug}/export.csv",
            "reports_" + slug.replace("/", "_").replace("-", "_") + "_export_csv",
            require_api_token(_report_csv_view(report_type, slug)),
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/reports/{slug}/export.xlsx",
            "reports_" + slug.replace("/", "_").replace("-", "_") + "_export_xlsx",
            require_api_token(_report_xlsx_view(report_type, slug)),
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/reports/{slug}/export.pdf",
            "reports_" + slug.replace("/", "_").replace("-", "_") + "_export_pdf",
            require_api_token(_report_pdf_view(report_type, slug)),
            methods=["GET"],
        )

def _report_view(report_type: str):
    def _view():
        if report_type == "subscriber_payments":
            return _subscriber_payments_view()
        try:
            items = service_from_context().reports(report_type=report_type)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        # ``columns`` = تسميات عربيّة لكلّ مفتاح (المفاتيح نفسها لا تتغيّر).
        return ok({"items": items, "count": len(items), "report_type": report_type,
                   "columns": AccountingService.report_columns(report_type, items)})

    _view.__name__ = f"reports_{report_type}_view"
    return _view


def _subscriber_payments_view():
    """«دفعات المستفيدين» — كل الدافعين (كان مقصوصًا على 200 بلا ترقيم).
    بلا ``limit`` يُعاد الكلّ؛ مع ``limit``/``offset`` صفحةٌ (1..1000) — والإجماليّ
    (عدد الدافعين والمجموع) محسوبٌ في SQL على الكلّ دائمًا."""
    limit = None
    offset = 0
    if request.args.get("limit") not in (None, "") or request.args.get("offset") not in (None, ""):
        try:
            limit, offset = page_args(default=200, maximum=1000)
        except PagingError as e:
            return fail("validation_error", e.message, status=422)
    page = service_from_context().subscriber_payments_page(limit=limit, offset=offset)
    items, totals = page["items"], page["totals"]
    return ok({
        "items": items, "count": len(items), "report_type": "subscriber_payments",
        "total_count": totals["payers"], "totals": totals,
        "has_more": limit is not None and offset + len(items) < totals["payers"],
        "columns": AccountingService.report_columns("subscriber_payments", items),
    })


def _report_csv_view(report_type: str, slug: str):
    def _view():
        try:
            csv_text = service_from_context().report_csv(report_type=report_type)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="hoberadius-{slug.replace("/", "-")}.csv"',
            },
        )

    _view.__name__ = f"reports_{report_type}_export_csv_view"
    return _view


def _report_xlsx_view(report_type: str, slug: str):
    def _view():
        try:
            xlsx_bytes = service_from_context().report_xlsx(report_type=report_type)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        return Response(
            xlsx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="hoberadius-{slug.replace("/", "-")}.xlsx"',
            },
        )

    _view.__name__ = f"reports_{report_type}_export_xlsx_view"
    return _view


def _report_pdf_view(report_type: str, slug: str):
    def _view():
        try:
            pdf_bytes = service_from_context().report_pdf(report_type=report_type)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="hoberadius-{slug.replace("/", "-")}.pdf"',
            },
        )

    _view.__name__ = f"reports_{report_type}_export_pdf_view"
    return _view


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _page_args(default_limit: int = 50) -> tuple[int, int]:
    try:
        limit = min(max(int(request.args.get("limit") or default_limit), 1), 200)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        raise RadiusValidationError("قيم limit و offset يجب أن تكون أرقامًا صحيحة.")
    return limit, offset


def reports_snapshots_list():
    try:
        limit, offset = _page_args()
        items = service_from_context().list_report_snapshots(
            report_type=(request.args.get("report_type") or "").strip(),
            limit=limit,
            offset=offset,
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"items": items, "count": len(items)})


def reports_snapshots_create():
    # جسم مصفوفة/قيمة مفردة كان يُسقط 500 (body.get على list) → 422 عربيّ.
    body, err = json_object()
    if err:
        return err
    report_type = body.get("report_type")
    report_type = report_type.strip() if isinstance(report_type, str) else ""
    if not report_type:
        return fail("validation_error", "نوع التقرير مطلوب.", status=422)
    try:
        # نطاقٌ مقلوب أو تاريخٌ غير صالح → 422 (كان يُحفظ لقطةً فارغة 201؛ الويب يرفضه).
        date_from, date_to = AccountingService.validate_report_range(
            body.get("date_from"), body.get("date_to"))
        snapshot = service_from_context().create_report_snapshot(
            report_type=report_type,
            actor=_actor(),
            date_from=date_from,
            date_to=date_to,
            parameters=body.get("parameters") if isinstance(body.get("parameters"), dict) else {},
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return ok({"snapshot": snapshot}, status=201)


def reports_snapshots_get(snapshot_id: int):
    try:
        snapshot = service_from_context().get_report_snapshot(snapshot_id)
    except RadiusValidationError as e:
        return fail("not_found", e.message, status=404)
    return ok({"snapshot": snapshot})
