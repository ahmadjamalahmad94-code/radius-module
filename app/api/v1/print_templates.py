"""Card print template API foundation."""
from __future__ import annotations

from flask import Blueprint, Response, g, render_template, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
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
        error = "معرّف الحزمة غير صحيح."

    if template is None:
        error = error or "القالب غير موجود."
    elif batch_id is not None:
        try:
            cards_service = get_cards_service()
            batch_obj = cards_service._store.get_batch(batch_id)
            if batch_obj is None:
                error = "الحزمة غير موجودة."
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
            error = str(exc) or "تعذّر جلب بطاقات الحزمة."

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
        raise RadiusValidationError("نوع الصورة غير مدعوم. استخدم PNG أو JPG أو WEBP.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RadiusValidationError("تعذّر قراءة صورة الخلفية.") from exc
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
        return fail("validation_error", "أرسل صورة PNG أو JPG أو WEBP.", status=422)
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
            data = _quick_form_payload(body["form"], for_preview=True)
        except RadiusError as e:
            return fail("validation_error", e.message, status=422)
    else:
        data = body.get("template") if isinstance(body.get("template"), dict) else {}
        try:
            _optimize_body_backgrounds(data)
        except RadiusError as e:
            return fail("validation_error", e.message, status=422)
    try:
        template_id = int(body.get("template_id") or 0) or None
        batch_raw = body.get("batch_id")
        batch_id = int(batch_raw) if batch_raw not in (None, "", 0, "0") else None
        payload = _svc().render_print_preview_pdf(
            tenant_id=_tid(),
            template_id=template_id,
            data=data,
            batch_id=batch_id,
            print_settings=body.get("print_settings") if isinstance(body.get("print_settings"), dict) else {},
            layout_overrides=body.get("layout_overrides") if isinstance(body.get("layout_overrides"), dict) else {},
            mode=str(body.get("mode") or "page"),
        )
    except (TypeError, ValueError):
        return fail("validation_error", "معرّف القالب أو الحزمة يجب أن يكون رقمًا.", status=422)
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    resp = Response(payload, mimetype="application/pdf")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def print_templates_last_settings_get():
    from ...radius.routes.print_templates import get_last_print_settings
    return ok({"settings": get_last_print_settings()})


def print_templates_last_settings_put():
    """Remember the sheet settings (same tenant setting the web quick screen
    prefills from)."""
    from ...radius.routes.print_templates import (
        _persist_last_print_settings,
        get_last_print_settings,
    )
    body = request.get_json(silent=True) or {}
    clean = {k: body[k] for k in _PRINT_SETTING_KEYS if k in body}
    if clean:
        _persist_last_print_settings({**get_last_print_settings(), **clean})
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


def _quick_form_payload(fields: dict, *, for_preview: bool) -> dict:
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
        return fail("validation_error", "معرّف القالب يجب أن يكون رقمًا.", status=422)
    try:
        payload = _quick_form_payload(fields, for_preview=False)
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
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    settings = body.get("print_settings") if isinstance(body.get("print_settings"), dict) else {}
    clean = {k: settings[k] for k in _PRINT_SETTING_KEYS if k in settings}
    if clean:
        _persist_last_print_settings({**get_last_print_settings(), **clean})
    return ok({"template": template}, status=200 if template_id else 201)
