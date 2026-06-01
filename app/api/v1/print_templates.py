"""Card print template API foundation."""
from __future__ import annotations

from flask import Blueprint, Response, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ...radius.services.license_admin_capacity import (
    CapacityEnforcementService,
    capacity_error_response,
)
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.operations import get_operations_service
    return get_operations_service()


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/print-templates",
                    "print_templates_list",
                    require_api_token(print_templates_list), methods=["GET"])
    bp.add_url_rule("/print-templates",
                    "print_templates_create",
                    require_api_token(print_templates_create), methods=["POST"])
    bp.add_url_rule("/print-templates/presets",
                    "print_templates_presets",
                    require_api_token(print_templates_presets), methods=["GET"])
    bp.add_url_rule("/print-jobs",
                    "print_jobs_list",
                    require_api_token(print_jobs_list), methods=["GET"])
    bp.add_url_rule("/print-templates/<int:template_id>",
                    "print_templates_update",
                    require_api_token(print_templates_update), methods=["PATCH"])
    bp.add_url_rule("/print-templates/<int:template_id>/render",
                    "print_templates_render",
                    require_api_token(print_templates_render), methods=["POST"])
    bp.add_url_rule("/print-templates/<int:template_id>/export",
                    "print_templates_export",
                    require_api_token(print_templates_export), methods=["POST"])
    bp.add_url_rule("/print-templates/<int:template_id>/export.pdf",
                    "print_templates_export_pdf",
                    require_api_token(print_templates_export_pdf), methods=["GET", "POST"])
    bp.add_url_rule("/print-templates/<int:template_id>/export-jobs",
                    "print_templates_export_job_start",
                    require_api_token(print_templates_export_job_start), methods=["POST"])
    bp.add_url_rule("/print-jobs/<int:job_id>",
                    "print_jobs_get",
                    require_api_token(print_jobs_get), methods=["GET"])
    bp.add_url_rule("/print-jobs/<int:job_id>/download",
                    "print_jobs_download",
                    require_api_token(print_jobs_download), methods=["GET"])


def print_templates_list():
    try:
        limit = min(int(request.args.get("limit") or 200), 1000)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "قيم limit و offset يجب أن تكون أرقامًا صحيحة.", status=422)
    items = _svc().list_print_templates(tenant_id=_tid(), limit=limit, offset=offset)
    return ok({"items": items, "count": len(items)})


def print_templates_create():
    body = request.get_json(silent=True) or {}
    capacity = CapacityEnforcementService().check_create(
        tenant_id=_tid(),
        feature_key="print_templates",
        limit_path="print_templates.max_active",
        usage_metric="print_templates_count",
    )
    if not capacity.allowed:
        return capacity_error_response(capacity)
    try:
        template = _svc().create_print_template(
            tenant_id=_tid(), actor=_actor(), data=body
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"template": template}, status=201)


def print_templates_update(template_id: int):
    body = request.get_json(silent=True) or {}
    try:
        template = _svc().update_print_template(
            tenant_id=_tid(), actor=_actor(), template_id=template_id, data=body
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"template": template})


def print_templates_presets():
    return ok({"items": _svc().list_print_template_presets()})


def print_jobs_list():
    try:
        limit = min(int(request.args.get("limit") or 50), 200)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "قيم limit و offset يجب أن تكون أرقامًا صحيحة.", status=422)
    items = _svc().list_print_jobs(tenant_id=_tid(), limit=limit, offset=offset)
    return ok({"items": items, "count": len(items)})


def _print_job_payload(job: dict) -> dict:
    metadata = job.get("metadata_json") if isinstance(job.get("metadata_json"), dict) else {}
    return {
        "id": job.get("id"),
        "template_id": job.get("template_id"),
        "batch_id": job.get("batch_id"),
        "export_type": job.get("export_type"),
        "status": job.get("status"),
        "card_count": job.get("card_count") or 0,
        "file_name": job.get("file_name") or "",
        "message": job.get("message") or "",
        "progress": int(metadata.get("progress") or (100 if job.get("status") in {"success", "failed"} else 0)),
        "stage": metadata.get("stage") or job.get("status"),
        "stage_label": metadata.get("stage_label") or job.get("message") or "",
        "rendered_cards": int(metadata.get("rendered_cards") or 0),
        "total_cards": int(metadata.get("total_cards") or job.get("card_count") or 0),
        "download_ready": bool(metadata.get("download_ready")) and job.get("status") == "success",
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
    }


def print_templates_render(template_id: int):
    body = request.get_json(silent=True) or {}
    try:
        result = _svc().render_print_template_preview(
            tenant_id=_tid(),
            template_id=template_id,
            sample=body.get("sample") if isinstance(body.get("sample"), dict) else None,
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    return ok(result)


def print_templates_export(template_id: int):
    return print_templates_export_pdf(template_id)


def _export_request_payload() -> tuple[dict, int | None, dict, dict]:
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}
    batch_id = request.args.get("batch_id") or body.get("batch_id")
    try:
        batch_id_int = int(batch_id) if batch_id not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise RadiusValidationError("معرّف حزمة الكروت يجب أن يكون رقمًا صحيحًا.") from exc

    print_settings = {}
    if isinstance(body.get("print_settings"), dict):
        print_settings.update(body["print_settings"])
    for key in (
        "print_page_size",
        "print_orientation",
        "print_columns",
        "print_rows",
        "print_margin_mm",
        "print_margin_top_mm",
        "print_margin_right_mm",
        "print_margin_bottom_mm",
        "print_margin_left_mm",
        "print_row_gap_mm",
        "print_column_gap_mm",
    ):
        value = request.args.get(key)
        if value not in (None, ""):
            print_settings[key] = value
    sample = body.get("sample") if isinstance(body.get("sample"), dict) else {}
    layout_overrides = body.get("layout_overrides") if isinstance(body.get("layout_overrides"), dict) else {}
    return sample, batch_id_int, layout_overrides, print_settings


def print_templates_export_pdf(template_id: int):
    try:
        sample, batch_id_int, _layout_overrides, print_settings = _export_request_payload()
        payload = _svc().export_print_template_pdf(
            tenant_id=_tid(),
            template_id=template_id,
            sample=sample,
            batch_id=batch_id_int,
            print_settings=print_settings,
            actor=_actor(),
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return Response(
        payload,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="print-template-{template_id}.pdf"'
        },
    )


def print_templates_export_job_start(template_id: int):
    try:
        sample, batch_id_int, layout_overrides, print_settings = _export_request_payload()
        job = _svc().start_print_template_export_job(
            tenant_id=_tid(),
            template_id=template_id,
            sample=sample,
            batch_id=batch_id_int,
            layout_overrides=layout_overrides,
            print_settings=print_settings,
            actor=_actor(),
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"job": _print_job_payload(job)}, status=202)


def print_jobs_get(job_id: int):
    try:
        job = _svc().get_print_job(tenant_id=_tid(), job_id=job_id)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    return ok({"job": _print_job_payload(job)})


def print_jobs_download(job_id: int):
    try:
        payload, file_name = _svc().get_print_job_file(tenant_id=_tid(), job_id=job_id)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return Response(
        payload,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
