"""Card print template API foundation."""
from __future__ import annotations
from app.i18n_text import N_, _tr

from flask import Blueprint, Response, g, render_template, request

from ...radius.core.errors import (
    RadiusConflict,
    RadiusError,
    RadiusNotFound,
    RadiusValidationError,
)
from ...radius.services.card_renderer import build_card_render_model, render_card_svg
from ...radius.services.cards import get_cards_service
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
    bp.add_url_rule("/print-templates/cleanup-fixtures",
                    "print_templates_cleanup_fixtures",
                    require_api_token(print_templates_cleanup_fixtures), methods=["POST"])
    # «طباعة الكروت» في تطبيق الجوال: معاينة بلا حفظ من نفس محرّك الطباعة،
    # وضغط صورة الخلفية بنفس دالة الويب، وآخر إعدادات الورقة.
    bp.add_url_rule("/print-templates/preview.pdf",
                    "print_templates_preview_pdf",
                    require_api_token(print_templates_preview_pdf), methods=["POST"])
    bp.add_url_rule("/print-templates/quick-elements",
                    "print_templates_quick_elements",
                    require_api_token(print_templates_quick_elements), methods=["POST"])
    bp.add_url_rule("/print-templates/quick-save",
                    "print_templates_quick_save",
                    require_api_token(print_templates_quick_save), methods=["POST"])
    bp.add_url_rule("/print-templates/background",
                    "print_templates_background",
                    require_api_token(print_templates_background), methods=["POST"])
    bp.add_url_rule("/print-templates/last-settings",
                    "print_templates_last_settings_get",
                    require_api_token(print_templates_last_settings_get), methods=["GET"])
    bp.add_url_rule("/print-templates/last-settings",
                    "print_templates_last_settings_put",
                    require_api_token(print_templates_last_settings_put), methods=["PUT"])
    bp.add_url_rule("/print-jobs",
                    "print_jobs_list",
                    require_api_token(print_jobs_list), methods=["GET"])
    bp.add_url_rule("/print-templates/<int:template_id>",
                    "print_templates_update",
                    require_api_token(print_templates_update), methods=["PATCH"])
    bp.add_url_rule("/print-templates/<int:template_id>",
                    "print_templates_get",
                    require_api_token(print_templates_get), methods=["GET"])
    bp.add_url_rule("/print-templates/<int:template_id>/background",
                    "print_templates_background_image",
                    require_api_token(print_templates_background_image), methods=["GET"])
    bp.add_url_rule("/print-templates/<int:template_id>/thumbnail.svg",
                    "print_templates_thumbnail_svg",
                    require_api_token(print_templates_thumbnail_svg), methods=["GET"])
    bp.add_url_rule("/print-templates/<int:template_id>",
                    "print_templates_delete",
                    require_api_token(print_templates_delete), methods=["DELETE"])
    bp.add_url_rule("/print-templates/<int:template_id>/set-default",
                    "print_templates_set_default",
                    require_api_token(print_templates_set_default), methods=["POST"])
    bp.add_url_rule("/print-templates/<int:template_id>/render",
                    "print_templates_render",
                    require_api_token(print_templates_render), methods=["POST"])
    bp.add_url_rule("/print-templates/<int:template_id>/preview-fragment",
                    "print_templates_preview_fragment",
                    require_api_token(print_templates_preview_fragment), methods=["GET"])
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
    bp.add_url_rule("/print-jobs/<int:job_id>/cancel",
                    "print_jobs_cancel",
                    require_api_token(print_jobs_cancel), methods=["POST"])
    bp.add_url_rule("/print-jobs/<int:job_id>",
                    "print_jobs_cancel_delete",
                    require_api_token(print_jobs_cancel), methods=["DELETE"])
    # (fix2 10) /print-jobs/abc answered an HTML 405: any id that is not a
    # number is simply a job that does not exist — JSON 404, all verbs.
    for _suffix, _name in (("", "print_jobs_bad_id"),
                           ("/download", "print_jobs_bad_id_download"),
                           ("/cancel", "print_jobs_bad_id_cancel")):
        bp.add_url_rule(f"/print-jobs/<job_ref>{_suffix}", _name,
                        require_api_token(_print_job_not_found),
                        methods=["GET", "POST", "DELETE"])


# ── قائمة خفيفة (stress 2026-09-28، F7) ─────────────────────────────
# كانت القائمة تُعيد كلّ صورة خلفيّة base64 داخل layout_json: 12.9MB لـ42
# قالبًا تُنزَّل في كلّ فتحٍ لشاشة الطباعة على الجوّال. الآن الصفوف خفيفة:
# بدل البايتات علمٌ + رابطٌ للصورة + رابطٌ لمصغّرة SVG؛ والقالب كاملًا من
# GET /print-templates/<id>، أو القائمة القديمة كما هي بـ ?full=1.
_HEAVY_LAYOUT_KEYS = ("background_image_data_url", "logo_image_data_url")


def _light_template(row: dict) -> dict:
    item = dict(row)
    layout = dict(item.get("layout_json") or {}) if isinstance(item.get("layout_json"), dict) else {}
    tid = int(item.get("id") or 0)
    has_bg = str(layout.get("background_image_data_url") or "").startswith("data:image/")
    has_logo = str(layout.get("logo_image_data_url") or "").startswith("data:image/")
    for key in _HEAVY_LAYOUT_KEYS:
        layout.pop(key, None)
    layout["has_background_image"] = has_bg
    layout["has_logo_image"] = has_logo
    item["layout_json"] = layout
    item["has_background_image"] = has_bg
    item["background_image_url"] = (
        f"/api/v1/print-templates/{tid}/background" if has_bg else None)
    item["thumbnail_url"] = f"/api/v1/print-templates/{tid}/thumbnail.svg"
    item["full_url"] = f"/api/v1/print-templates/{tid}"
    return item


def _get_template_or_404(template_id: int):
    from ...radius.db.repos import operations_repo
    row = operations_repo.get_print_template(_tid(), template_id)
    if not row:
        return None, fail("not_found", _tr("قالب الطباعة غير موجود."), status=404)
    return row, None


def print_templates_list():
    try:
        limit = int(request.args.get("limit") or 200)
        offset = int(request.args.get("offset") or 0)
    except ValueError:
        return fail("validation_error", _tr("قيم limit و offset يجب أن تكون أرقامًا صحيحة."), status=422)
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    items = _svc().list_print_templates(tenant_id=_tid(), limit=limit, offset=offset)
    full = str(request.args.get("full") or "").strip().lower() in {"1", "true", "yes"}
    if not full:
        items = [_light_template(row) for row in items]
    return ok({"items": items, "count": len(items), "full": full})


def print_templates_get(template_id: int):
    """One template with everything (background/logo data URLs included)."""
    row, response = _get_template_or_404(template_id)
    if response:
        return response
    return ok({"template": row})


def print_templates_background_image(template_id: int):
    """The stored background as an image file (cacheable), for the light list."""
    import base64
    import binascii
    import hashlib

    row, response = _get_template_or_404(template_id)
    if response:
        return response
    layout = row.get("layout_json") if isinstance(row.get("layout_json"), dict) else {}
    url = str(layout.get("background_image_data_url") or "")
    if not url.startswith("data:image/") or ";base64," not in url:
        return fail("not_found", _tr("لا توجد صورة خلفيّة لهذا القالب."), status=404)
    head, encoded = url.split(";base64,", 1)
    try:
        raw = base64.b64decode(encoded)
    except (binascii.Error, ValueError):
        return fail("not_found", _tr("تعذّر قراءة صورة الخلفيّة المخزّنة."), status=404)
    etag = hashlib.sha1(raw).hexdigest()
    if request.headers.get("If-None-Match", "").strip('"') == etag:
        return Response(status=304)
    resp = Response(raw, mimetype=head.removeprefix("data:") or "image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["ETag"] = f'"{etag}"'
    return resp


def print_templates_thumbnail_svg(template_id: int):
    """The template's card rendered as SVG by the export's own engine."""
    row, response = _get_template_or_404(template_id)
    if response:
        return response
    sample = {"id": "", "username": "0123456789012", "password": "123456", "serial": ""}
    model = build_card_render_model(row, sample)
    svg = render_card_svg(model, mask_password=True, embed_fonts=True)
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "private, max-age=600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def print_templates_create():
    body = request.get_json(silent=True) or {}
    try:
        _optimize_body_backgrounds(body)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
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
    except RadiusConflict as e:
        return _duplicate_name(e)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"template": template}, status=201)


def print_templates_update(template_id: int):
    body = request.get_json(silent=True) or {}
    try:
        _optimize_body_backgrounds(body)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    try:
        template = _svc().update_print_template(
            tenant_id=_tid(), actor=_actor(), template_id=template_id, data=body
        )
    except RadiusConflict as e:
        return _duplicate_name(e)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"template": template})


def print_templates_presets():
    return ok({"items": _svc().list_print_template_presets()})


def print_templates_delete(template_id: int):
    try:
        deleted = _svc().delete_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            template_id=template_id,
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"deleted": bool(deleted), "template_id": template_id})


def print_templates_set_default(template_id: int):
    try:
        template = _svc().set_default_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            template_id=template_id,
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"template": template})


def print_templates_cleanup_fixtures():
    try:
        purged = _svc().purge_test_fixture_print_templates(
            tenant_id=_tid(),
            actor=_actor(),
        )
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"purged": len(purged), "items": purged})


def print_jobs_list():
    try:
        limit = min(int(request.args.get("limit") or 50), 200)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", _tr("قيم limit و offset يجب أن تكون أرقامًا صحيحة."), status=422)
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


_PREVIEW_FRAGMENT_OVERRIDE_KEYS = (
    "brand_name",
    "card_title",
    "footer_text",
    "hotspot_address",
    "price_text",
    "validity_text",
)


def print_templates_preview_fragment(template_id: int):
    template = None
    try:
        templates = _svc().list_print_templates(tenant_id=_tid(), limit=10_000)
        for row in templates:
            if int(row.get("id") or 0) == int(template_id):
                template = row
                break
    except Exception:  # pragma: no cover - defensive parity with web route
        template = None

    batch = None
    cards: list = []
    error: str | None = None

    batch_id_raw = request.args.get("batch_id") or ""
    try:
        batch_id = int(batch_id_raw) if batch_id_raw else None
    except ValueError:
        batch_id = None
        error = _tr("معرّف الحزمة غير صحيح.")

    if template is None:
        error = error or _tr("القالب غير موجود.")
    elif batch_id is not None:
        try:
            cards_service = get_cards_service()
            batch_obj = cards_service._store.get_batch(batch_id)
            if batch_obj is None:
                error = _tr("الحزمة غير موجودة.")
            else:
                batch = {
                    "id": getattr(batch_obj, "id", batch_id),
                    "batch_name": getattr(batch_obj, "batch_name", "") or "",
                    "count_to_make": getattr(batch_obj, "count_to_make", 0) or 0,
                    "created_count": getattr(batch_obj, "created_count", 0) or 0,
                }
                cards = cards_service.list_cards(batch_id=batch_id, limit=4, offset=0)
        except RadiusError as exc:
            error = exc.message
        except Exception as exc:  # pragma: no cover - defensive
            error = str(exc) or _tr("تعذّر جلب بطاقات الحزمة.")

    overrides = {
        key: (request.args.get(key) or "").strip()
        for key in _PREVIEW_FRAGMENT_OVERRIDE_KEYS
    }
    overrides = {key: value for key, value in overrides.items() if value}

    card_svgs: list[dict] = []
    if template is not None and error is None:
        if cards:
            for card in cards:
                model = build_card_render_model(template, card, overrides=overrides)
                card_svgs.append({
                    "card": card,
                    "svg": render_card_svg(model, mask_password=True),
                })
        else:
            model = build_card_render_model(template, None, overrides=overrides)
            card_svgs.append({
                "card": None,
                "svg": render_card_svg(model, mask_password=True),
            })

    html = render_template(
        "radius/_print_template_preview_fragment.html",
        template=template,
        batch=batch,
        cards=cards,
        card_svgs=card_svgs,
        overrides=overrides,
        error=error,
    )
    response = Response(html, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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
        raise RadiusValidationError(_tr("معرّف حزمة الكروت يجب أن يكون رقمًا صحيحًا.")) from exc

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
        "print_fit_mode",
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
    except RadiusConflict as e:
        return fail("conflict", e.message, status=409)
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
        from ...radius.routes.print_templates import remember_last_template
        remember_last_template(template_id)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusConflict as e:
        return fail("conflict", e.message, status=409)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"job": _print_job_payload(job)}, status=202)


def print_jobs_get(job_id: int):
    try:
        job = _svc().get_print_job(tenant_id=_tid(), job_id=job_id)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    return ok({"job": _print_job_payload(job)})


def _print_job_not_found(job_ref: str = ""):
    return fail("not_found", _tr("مهمة الطباعة غير موجودة."), status=404)


def _duplicate_name(e: RadiusError):
    """HTTP 409 + code ``duplicate_name`` for a template name clash (the app
    opens its overwrite dialog on 409 — it used to get a 422)."""
    return fail("duplicate_name", e.message, status=409, details=e.details or None)


def print_jobs_cancel(job_id: int):
    """POST /print-jobs/<id>/cancel (or DELETE /print-jobs/<id>) — drop a
    queued job or stop a running one at its next checkpoint."""
    try:
        job = _svc().cancel_print_job(tenant_id=_tid(), job_id=job_id, actor=_actor())
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusConflict as e:
        return fail("conflict", e.message, status=409)
    return ok({"job": _print_job_payload(job)})


def print_jobs_download(job_id: int):
    try:
        payload, file_name = _svc().get_print_job_file(tenant_id=_tid(), job_id=job_id)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusConflict as e:
        return fail("conflict", e.message, status=409)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    return Response(
        payload,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


# ─── «طباعة الكروت» (تطبيق الجوال) ──────────────────────────────────

_BACKGROUND_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
_PRINT_SETTING_KEYS = (
    "print_page_size", "print_orientation", "print_columns", "print_rows",
    "print_margin_mm", "print_row_gap_mm", "print_column_gap_mm",
    "print_fit_mode", "print_cut_lines",
)


def _optimize_data_url(data_url: str, name: str) -> dict:
    """Same Pillow optimizer as the web designer (longest edge 1400px, JPEG
    ~420KB or PNG with alpha) — so the app stores the very image the web
    would. Returns {} when ``data_url`` is not an image data URL."""
    import base64
    import binascii

    from ...radius.routes.print_templates import _optimize_background_image

    data_url = (data_url or "").strip()
    if not data_url.startswith("data:image/") or ";base64," not in data_url:
        return {}
    mime_part, encoded = data_url.split(";base64,", 1)
    mime = mime_part.removeprefix("data:").lower()
    if mime not in _BACKGROUND_MIMES:
        raise RadiusValidationError(_tr("نوع الصورة غير مدعوم. استخدم PNG أو JPG أو WEBP."))
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RadiusValidationError(_tr("تعذّر قراءة صورة الخلفية.")) from exc
    if not raw:
        return {}
    return _optimize_background_image(raw, name or "card-background", mime)


def _optimize_body_backgrounds(body: dict) -> None:
    """Optimize a NEW background image sent through the API (top level or in
    ``layout``). Images already optimized (flag set by the optimizer) pass
    through untouched, so re-saving never re-compresses."""
    targets = [body]
    if isinstance(body.get("layout"), dict):
        targets.append(body["layout"])
    for target in targets:
        url = target.get("background_image_data_url")
        if not isinstance(url, str) or not url.startswith("data:image/"):
            continue
        if str(target.get("background_image_optimized") or "").lower() in {"1", "true", "yes"}:
            continue
        mime = url.split(";", 1)[0].removeprefix("data:").lower()
        if mime not in _BACKGROUND_MIMES:
            # (fix2 F10.4) same answer as /background and quick-save.
            raise RadiusValidationError(_tr("نوع الصورة غير مدعوم. استخدم PNG أو JPG أو WEBP."))
        try:
            optimized = _optimize_data_url(url, str(target.get("background_image_name") or ""))
        except RadiusError:
            # Best effort: an image Pillow can't read keeps the old API
            # behaviour (stored as sent) instead of failing the save.
            continue
        if optimized:
            target.update(optimized)


def print_templates_background():
    """POST {data_url, name} → the optimized background fields, ready to put
    in a template (``background_image_optimized: true``)."""
    body = request.get_json(silent=True) or {}
    try:
        optimized = _optimize_data_url(str(body.get("data_url") or ""),
                                       str(body.get("name") or ""))
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    if not optimized:
        return fail("validation_error", _tr("أرسل صورة PNG أو JPG أو WEBP."), status=422)
    return ok({"background": optimized})


def print_templates_preview_pdf():
    """POST {template_id?, template: {...same body as create/update...},
    batch_id?, print_settings, layout_overrides, mode: page|card} → PDF.

    Same drawing code as the export, on the UNSAVED template; writes nothing."""
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}
    if isinstance(body.get("form"), dict):
        # The web «منشئ كروت PDF» fields → the web's own payload builder,
        # exactly like its live preview (designer-svg): no image re-encode on
        # the per-edit path, the chosen bitmap injected as-is.
        try:
            data = _quick_form_payload(body["form"], for_preview=True,
                                       template_id=body.get("template_id"))
        except RadiusError as e:
            return fail("validation_error", e.message, status=422)
    else:
        data = body.get("template") if isinstance(body.get("template"), dict) else {}
        try:
            _optimize_body_backgrounds(data)
        except RadiusError as e:
            return fail("validation_error", e.message, status=422)
    ids, bad = _template_and_batch_ids(body)
    if bad is not None:
        return bad
    template_id, batch_id = ids
    try:
        payload = _svc().render_print_preview_pdf(
            tenant_id=_tid(),
            template_id=template_id,
            data=data,
            batch_id=batch_id,
            print_settings=body.get("print_settings") if isinstance(body.get("print_settings"), dict) else {},
            layout_overrides=body.get("layout_overrides") if isinstance(body.get("layout_overrides"), dict) else {},
            mode=str(body.get("mode") or "page"),
        )
        elements = _final_elements(template_id=template_id, data=data, batch_id=batch_id)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        # NaN/Infinity margins are RadiusValidationError AND ValueError —
        # caught here first so the message names the right field (fix2 10).
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    except (TypeError, ValueError):
        return fail("validation_error", _tr("قيم المعاينة غير صالحة."), status=422)
    resp = Response(payload, mimetype="application/pdf")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    # (fix2 N1/N7) where the renderer actually put the username/password/QR
    # (mm, card top-left) and what it moved — the app re-syncs its sliders.
    import json as _json
    resp.headers["X-Print-Elements"] = _json.dumps(elements, ensure_ascii=True,
                                                   separators=(",", ":"))
    resp.headers["Access-Control-Expose-Headers"] = "X-Print-Elements"
    return resp


def _template_and_batch_ids(body: dict):
    """((template_id, batch_id), None) or (None, 422 response)."""
    try:
        template_id = int(body.get("template_id") or 0) or None
        batch_raw = body.get("batch_id")
        batch_id = int(batch_raw) if batch_raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError, OverflowError):
        return None, fail("validation_error", _tr("معرّف القالب أو الحزمة يجب أن يكون رقمًا."), status=422)
    return (template_id, batch_id), None


def print_templates_last_settings_get():
    from ...radius.routes.print_templates import (
        get_last_print_settings,
        last_template_id_for_admin,
    )
    # (fix2 I2) the quick print screen opens on THIS admin's last template
    # (falls back to the tenant default) — not on whatever anyone saved last.
    return ok({
        "settings": get_last_print_settings(),
        "last_template_id": last_template_id_for_admin(),
        "default_template_id": _svc().get_default_print_template_id(tenant_id=_tid()),
    })


def print_templates_last_settings_put():
    """Remember the sheet settings (same tenant setting the web quick screen
    prefills from)."""
    from ...radius.routes.print_templates import (
        _persist_last_print_settings,
        get_last_print_settings,
    )
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("validation_error", _tr("أرسل الإعدادات ككائن JSON."), status=422)
    clean = {k: body[k] for k in _PRINT_SETTING_KEYS if k in body}
    if clean:
        from ...radius.services.operations import validate_print_settings
        merged = {**get_last_print_settings(), **clean}
        try:
            # نفس قواعد التصدير — لا تُحفظ إعداداتٌ تُفشل التصدير التالي.
            validate_print_settings(merged)
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        _persist_last_print_settings(merged)
    return ok({"settings": get_last_print_settings()})


def _as_form(fields: dict):
    """JSON → the MultiDict an HTML form would post (bools as 1/0)."""
    from werkzeug.datastructures import MultiDict

    items = []
    for key, value in (fields or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        items.append((str(key), str(value)))
    return MultiDict(items)


def _quick_form_payload(fields: dict, *, for_preview: bool, template_id=None) -> dict:
    """Run the web designer's ``_payload()`` on the quick-form fields sent by
    the app, so a template saved/previewed from the app is normalized by the
    SAME code as one saved from the web «منشئ كروت PDF» (defaults included).

    ``for_preview`` mirrors the web live preview (designer-svg): the image is
    not re-optimized per edit; the bitmap is injected into the layout."""
    from flask import current_app

    from ...radius.routes.print_templates import _payload

    form = _as_form(fields)
    with current_app.test_request_context(method="POST", data=form):
        payload = _payload(allow_data_url_background=not for_preview)
    try:
        tid = int(template_id or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid:
        # An EXISTING template: merge what the form sent onto what is stored
        # (the quick form has no brand/footer/price/colour fields — F1).
        from ...radius.db.repos import operations_repo
        from ...radius.routes.print_templates import quick_partial_payload
        payload = quick_partial_payload(
            payload, form, stored=operations_repo.get_print_template(_tid(), tid))
    if for_preview:
        layout = dict(payload.get("layout") or {})
        bg = str(fields.get("background_image_data_url") or "").strip()
        if bg.startswith("data:image/") and (
                layout.get("background_style") == "image"
                or layout.get("preset_background_image")):
            layout["background_image_data_url"] = bg
            if layout.get("background_style") != "preset":
                layout["background_style"] = "image"
        payload["layout"] = layout
    return payload


def print_templates_quick_save():
    """POST {template_id?, form: {...web quick-form fields...},
    print_settings?: {...}} → create (no id) or update the template exactly as
    the web quick screen's «حفظ» does, and remember the sheet settings."""
    from ...radius.routes.print_templates import (
        _persist_last_print_settings,
        get_last_print_settings,
    )

    body = request.get_json(silent=True) or {}
    fields = body.get("form") if isinstance(body.get("form"), dict) else {}
    try:
        template_id = int(body.get("template_id") or 0) or None
    except (TypeError, ValueError):
        return fail("validation_error", _tr("معرّف القالب يجب أن يكون رقمًا."), status=422)
    settings = body.get("print_settings") if isinstance(body.get("print_settings"), dict) else {}
    clean = {k: settings[k] for k in _PRINT_SETTING_KEYS if k in settings}
    if clean:
        from ...radius.services.operations import validate_print_settings
        try:
            validate_print_settings({**get_last_print_settings(), **clean})
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
    try:
        payload = _quick_form_payload(fields, for_preview=False, template_id=template_id)
        if template_id:
            template = _svc().update_print_template(
                tenant_id=_tid(), actor=_actor(),
                template_id=template_id, data=payload)
        else:
            capacity = CapacityEnforcementService().check_create(
                tenant_id=_tid(),
                feature_key="print_templates",
                limit_path="print_templates.max_active",
                usage_metric="print_templates_count",
            )
            if not capacity.allowed:
                return capacity_error_response(capacity)
            template = _svc().create_print_template(
                tenant_id=_tid(), actor=_actor(), data=payload)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusConflict as e:
        return _duplicate_name(e)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    if clean:
        _persist_last_print_settings({**get_last_print_settings(), **clean})
    from ...radius.routes.print_templates import remember_last_template
    remember_last_template(template.get("id"))
    try:
        elements = _elements_payload(template, None)
    except Exception:  # noqa: BLE001 — the save itself succeeded
        elements = None
    return ok({"template": template, "elements": elements},
              status=200 if template_id else 201)


def print_templates_quick_elements():
    """POST {template_id?, form, batch_id?} → where the username / password /
    QR actually sit on the card, in mm from the card's top-left (the same
    anchor the web designer's drag writes to ``*_x``/``*_y``), plus their
    sizes. Lets the app start a position slider from the real automatic
    place, and drag the element on the preview. Writes nothing."""
    from ...radius.services.card_renderer import build_card_render_model

    body = request.get_json(silent=True) or {}
    fields = body.get("form") if isinstance(body.get("form"), dict) else {}
    ids, bad = _template_and_batch_ids(body)
    if bad is not None:
        return bad
    template_id, batch_id = ids
    try:
        data = _quick_form_payload(fields, for_preview=True, template_id=template_id)
        template = _svc().preview_print_template_row(
            tenant_id=_tid(), template_id=template_id, data=data)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except (TypeError, ValueError):
        return fail("validation_error", _tr("قيم التصميم غير صالحة."), status=422)
    return ok(_elements_payload(template, batch_id))


def _final_elements(*, template_id, data, batch_id) -> dict:
    template = _svc().preview_print_template_row(
        tenant_id=_tid(), template_id=template_id, data=data)
    return _elements_payload(template, batch_id)


def _elements_payload(template: dict, batch_id) -> dict:
    """The username / password / QR boxes the renderer ACTUALLY uses (mm from
    the card's top-left), what it adjusted, and the fonts in pt.

    🔴 (fix2 N7/F2) the QR box is the drawn SQUARE (was 34.24×36 at y 18.0
    for a 34.2×34.2 QR drawn at y 18.9); a moved element carries
    ``adjusted: true`` + ``requested`` so the app can move its slider."""
    from ...radius.services.card_renderer import build_card_render_model

    card = {"id": "", "username": "0123456789012", "password": "123456", "serial": ""}
    if batch_id:
        try:
            from ...radius.db.repos import cards_repo
            batch = cards_repo.get_batch(_tid(), batch_id, include_deleted=True)
            rows = cards_repo.list_cards(_tid(), batch_id=batch_id, used=None,
                                         revoked=None, limit=1, offset=0)
            if rows:
                no_pw = bool(getattr(batch, "login_without_password", False))
                card = {"id": rows[0].id, "username": rows[0].username,
                        "password": "" if no_pw else rows[0].password,
                        "serial": str(rows[0].id or "")}
        except Exception:  # noqa: BLE001 — a sample card is fine
            pass
    model = build_card_render_model(template, card)
    cw = float(model["canvas"]["width"])
    ch = float(model["canvas"]["height"])
    layout = template.get("layout_json") or {}
    w_mm = float(layout.get("card_width_mm") or 54)
    h_mm = float(layout.get("card_height_mm") or 85.6)
    # mm box oriented like the canvas (web designer cardMm()).
    if (ch > cw) != (h_mm > w_mm) and w_mm != h_mm:
        w_mm, h_mm = h_mm, w_mm
    sx, sy = w_mm / cw, h_mm / ch
    pt_factor = float(model.get("pt_factor") or 1.0)

    def box(x, y, w, h):
        return {"x": round(x * sx, 2), "y": round(y * sy, 2),
                "w": round(w * sx, 2), "h": round(h * sy, 2)}

    def requested_mm(prefix):
        rx, ry = float(template.get(f"{prefix}_x") or 0), float(template.get(f"{prefix}_y") or 0)
        return None if (rx == 0 and ry == 0) else {"x": round(rx, 2), "y": round(ry, 2)}

    elements: dict = {}
    for el in model.get("elements") or []:
        kind, eid = el.get("kind"), el.get("id")
        if kind == "pill" and eid in ("user", "pass"):
            prefix = "username" if eid == "user" else "password"
            item = box(el["x"], el["y"], el["width"], el["height"])
            # the value font really drawn, in the pt the API/app send back
            # (sending it back renders exactly the same — fix2 item 11).
            item["font_pt"] = round(float(el["value_font_size"]) / pt_factor, 2)
            item["font_auto"] = bool(el.get("font_auto"))
            req = requested_mm(prefix)
            item["requested"] = req
            item["adjusted"] = bool(req and (abs(req["x"] - item["x"]) > 0.2
                                             or abs(req["y"] - item["y"]) > 0.2))
            elements[prefix] = item
        elif kind == "qr":
            # the SQUARE drawn on the card (place_card_qr: top-left anchored,
            # one scale factor) — exactly where the app's drag handle sits.
            side = float(el["size"]) * min(sx, sy)
            item = {"x": round(float(el["x"]) * sx, 2), "y": round(float(el["y"]) * sy, 2),
                    "w": round(side, 2), "h": round(side, 2),
                    "size_pct": round(float(el["size"]) / cw * 100.0, 2)}
            req = requested_mm("qr")
            item["requested"] = req
            item["adjusted"] = any(a.get("element") == "qr" for a in model.get("adjustments") or [])
            elements["qr"] = item
    notes = []
    if any(a.get("element") == "qr" and a.get("action") in ("moved", "resized")
           for a in model.get("adjustments") or []):
        notes.append(N_("نُقل رمز QR (أو صُغّر) كي لا يغطّي اسم المستخدم أو كلمة المرور."))
    return {
        "card": {"width_mm": round(w_mm, 2), "height_mm": round(h_mm, 2)},
        "elements": elements,
        "qr_conflict": bool(model.get("qr_conflict")),
        "warnings": list(model.get("warnings") or []) + notes,
    }
