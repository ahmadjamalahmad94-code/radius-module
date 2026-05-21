"""Web UI for card print template operations room."""
from __future__ import annotations

import base64
from pathlib import PurePath

from flask import Blueprint, Response, flash, g, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.cards import get_cards_service
from ..services.operations import get_operations_service


def register_print_template_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/print-templates", "print_templates", print_templates, methods=["GET"])
    bp.add_url_rule("/print-templates", "print_templates_create", print_templates_create, methods=["POST"])
    bp.add_url_rule(
        "/print-templates/export",
        "print_templates_export_center",
        print_templates_export_center,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/preview",
        "print_templates_preview",
        print_templates_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/preview-fragment",
        "print_templates_preview_fragment",
        print_templates_preview_fragment,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/delete",
        "print_templates_delete",
        print_templates_delete,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/cleanup-fixtures",
        "print_templates_cleanup_fixtures",
        print_templates_cleanup_fixtures,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/export.pdf",
        "print_templates_export_pdf",
        print_templates_export_pdf,
        methods=["GET"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or 1))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _int(name: str, default: int = 0) -> int:
    try:
        return int(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float = 0) -> float:
    try:
        return float(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


def _checked(name: str, default: bool = False) -> bool:
    if name not in request.form:
        return default
    return request.form.get(name) in {"1", "true", "on", "yes"}


def _uploaded_background() -> dict:
    upload = request.files.get("background_image")
    if not upload or not upload.filename:
        return {}
    raw = upload.read()
    if not raw:
        return {}
    if len(raw) > 1_500_000:
        raise RadiusError("حجم صورة الخلفية كبير جدًا. الحد الحالي 1.5MB.")
    mime = (upload.mimetype or "").lower()
    if mime not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        raise RadiusError("نوع الصورة غير مدعوم. استخدم PNG أو JPG أو WEBP.")
    safe_name = PurePath(upload.filename).name[:120]
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "background_image_data_url": f"data:{mime};base64,{encoded}",
        "background_image_name": safe_name,
        "background_image_mime": mime,
    }


def _payload() -> dict:
    layout = {
        "preview_mode": "visual_design_room",
        "card_width_mm": _float("card_width_mm", 85),
        "card_height_mm": _float("card_height_mm", 54),
        "card_orientation": request.form.get("card_orientation") or "horizontal",
        "design_preset": request.form.get("design_preset") or "modern",
        "gradient_start": request.form.get("gradient_start") or "#0f172a",
        "gradient_end": request.form.get("gradient_end") or "#22a7bd",
        "accent_color": request.form.get("accent_color") or "#f59e0b",
        "text_color": request.form.get("text_color") or request.form.get("color") or "#ffffff",
        "surface_color": request.form.get("surface_color") or "#e8f7fb",
        "pattern_style": request.form.get("pattern_style") or "signal",
        "image_opacity": _float("image_opacity", 0.82),
        "qr_style": request.form.get("qr_style") or "boxed",
        "brand_name": request.form.get("brand_name") or "HobeRadius",
        "card_title": request.form.get("card_title") or "بطاقة إنترنت",
        "footer_text": request.form.get("footer_text") or "",
        "hotspot_address": request.form.get("hotspot_address") or "",
        "price_text": request.form.get("price_text") or "",
        "validity_text": request.form.get("validity_text") or "",
        "instructions_text": request.form.get("instructions_text") or "",
        "background_style": request.form.get("background_style") or "gradient",
        "bleed_marks": _checked("bleed_marks"),
        "show_username": _checked("show_username", True),
        "show_password": _checked("show_password", True),
        "show_price": _checked("show_price"),
        "show_hotspot": _checked("show_hotspot", True),
        "show_validity": _checked("show_validity", True),
        "show_serial": _checked("show_serial", True),
        "show_guides": _checked("show_guides"),
        "show_brand": _checked("show_brand", True),
    }
    layout.update(_uploaded_background())
    return {
        "name": request.form.get("name"),
        "orientation": request.form.get("orientation") or "portrait",
        "cards_per_row": _int("cards_per_row", 2),
        "cards_per_column": _int("cards_per_column", 5),
        "page_size": request.form.get("page_size") or "A4",
        "show_qr": _checked("show_qr", True),
        "username_x": _float("username_x"),
        "username_y": _float("username_y"),
        "password_x": _float("password_x"),
        "password_y": _float("password_y"),
        "qr_x": _float("qr_x"),
        "qr_y": _float("qr_y"),
        "font_size": _int("font_size", 12),
        "color": request.form.get("color") or layout["text_color"],
        "layout": layout,
    }


def _page_context(*, preview: dict | None = None) -> dict:
    ops = get_operations_service()
    return {
        "templates": ops.list_print_templates(tenant_id=_tid(), limit=500),
        "batches": get_cards_service().list_batch_operations(limit=200, offset=0),
        "jobs": ops.list_print_jobs(tenant_id=_tid(), limit=30),
        "presets": ops.list_print_template_presets(),
        "preview": preview,
    }


def print_templates():
    return render_template("radius/print_templates.html", **_page_context())


def print_templates_export_center():
    return render_template("radius/print_templates_export.html", **_page_context())


def print_templates_create():
    try:
        get_operations_service().create_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            data=_payload(),
        )
        flash("تم حفظ قالب التصميم. يمكنك الآن تصدير PDF عينة أو ربطه بحزمة بطاقات فعلية.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.print_templates"))


def print_templates_preview(template_id: int):
    try:
        preview = get_operations_service().render_print_template_preview(
            tenant_id=_tid(),
            template_id=template_id,
            sample={
                "username": request.form.get("sample_username") or "CARD1234",
                "has_password": True,
                "qr_payload": request.form.get("sample_username") or "CARD1234",
            },
        )
        return render_template("radius/print_templates.html", **_page_context(preview=preview))
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.print_templates"))


_PREVIEW_FRAGMENT_OVERRIDE_KEYS = (
    "brand_name",
    "card_title",
    "footer_text",
    "hotspot_address",
    "price_text",
    "validity_text",
)


def print_templates_preview_fragment(template_id: int):
    """Live mini-preview for the export center: renders up to 4 real cards
    from the chosen batch using the chosen template's layout. Returns an
    HTML fragment, not a full page — the export UI swaps it into a
    placeholder via fetch(). Password is never included; the fragment
    masks it to ••••••••, same as the live designer canvas.
    """
    ops = get_operations_service()
    template = None
    try:
        templates = ops.list_print_templates(tenant_id=_tid(), limit=10_000)
        for row in templates:
            if int(row.get("id") or 0) == int(template_id):
                template = row
                break
    except Exception:  # pragma: no cover — defensive
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
                # CardBatch is a dataclass; the template only reads a few
                # attributes so we expose it as a dict for simpler Jinja.
                batch = {
                    "id": getattr(batch_obj, "id", batch_id),
                    "batch_name": getattr(batch_obj, "batch_name", "") or "",
                    "count_to_make": getattr(batch_obj, "count_to_make", 0) or 0,
                    "created_count": getattr(batch_obj, "created_count", 0) or 0,
                }
                cards = cards_service.list_cards(batch_id=batch_id, limit=4, offset=0)
        except RadiusError as exc:
            error = exc.message
        except Exception as exc:  # pragma: no cover — defensive
            error = str(exc) or "تعذّر جلب بطاقات الحزمة."

    overrides = {
        key: (request.args.get(key) or "").strip()
        for key in _PREVIEW_FRAGMENT_OVERRIDE_KEYS
    }
    overrides = {k: v for k, v in overrides.items() if v}

    return render_template(
        "radius/_print_template_preview_fragment.html",
        template=template,
        batch=batch,
        cards=cards,
        overrides=overrides,
        error=error,
    )


def print_templates_delete(template_id: int):
    try:
        get_operations_service().delete_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            template_id=template_id,
        )
        flash("تم حذف القالب.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
    next_url = request.form.get("next") or url_for("radius.print_templates")
    return redirect(next_url)


def print_templates_cleanup_fixtures():
    try:
        purged = get_operations_service().purge_test_fixture_print_templates(
            tenant_id=_tid(),
            actor=_actor(),
        )
    except RadiusError as exc:
        flash(exc.message, "error")
        return redirect(url_for("radius.print_templates"))
    if purged:
        flash(
            f"تم تنظيف {len(purged)} قالب اختبار من القائمة.",
            "success",
        )
    else:
        flash("لا توجد قوالب اختبار للتنظيف.", "info")
    next_url = request.form.get("next") or url_for("radius.print_templates")
    return redirect(next_url)


def print_templates_export_pdf(template_id: int):
    batch_id_raw = request.args.get("batch_id") or ""
    try:
        batch_id = int(batch_id_raw) if batch_id_raw else None
    except ValueError:
        flash("معرّف الحزمة غير صحيح.", "error")
        return redirect(url_for("radius.print_templates"))
    layout_overrides = {
        key: (request.args.get(key) or "").strip()
        for key in (
            "brand_name",
            "card_title",
            "footer_text",
            "hotspot_address",
            "price_text",
            "validity_text",
        )
    }
    layout_overrides = {key: value for key, value in layout_overrides.items() if value}
    try:
        payload = get_operations_service().export_print_template_pdf(
            tenant_id=_tid(),
            template_id=template_id,
            sample={
                "username": request.args.get("sample_username") or "CARD1234",
                "password": request.args.get("sample_password") or "********",
                "qr_payload": request.args.get("sample_username") or "CARD1234",
            },
            batch_id=batch_id,
            layout_overrides=layout_overrides,
            actor=_actor(),
        )
    except RadiusError as exc:
        flash(exc.message, "error")
        return redirect(url_for("radius.print_templates"))
    suffix = f"batch-{batch_id}" if batch_id else f"template-{template_id}"
    return Response(
        payload,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="cards-{suffix}.pdf"'},
    )
