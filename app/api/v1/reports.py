"""Customer service report API foundations."""
from __future__ import annotations

from flask import Blueprint, Response, g, request

from ...radius.core.errors import RadiusValidationError
from ...radius.services.accounting import service_from_context
from ..auth import require_api_token
from ..responses import fail, ok


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
        try:
            items = service_from_context().reports(report_type=report_type)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        return ok({"items": items, "count": len(items), "report_type": report_type})

    _view.__name__ = f"reports_{report_type}_view"
    return _view


def _report_csv_view(report_type: str, slug: str):
    def _view():
        try:
            csv_text = service_from_context().report_csv(report_type=report_type)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        return Response(
            csv_text,
            mimetype="text/csv; charset=utf-8",
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
        raise RadiusValidationError("limit/offset must be int")
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
    body = request.get_json(silent=True) or {}
    report_type = str(body.get("report_type") or "").strip()
    if not report_type:
        return fail("validation_error", "report_type is required", status=422)
    try:
        snapshot = service_from_context().create_report_snapshot(
            report_type=report_type,
            actor=_actor(),
            date_from=str(body.get("date_from") or ""),
            date_to=str(body.get("date_to") or ""),
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
