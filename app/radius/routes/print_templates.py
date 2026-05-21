"""Web UI for card print template operations room."""
from __future__ import annotations

from flask import Blueprint, Response, flash, g, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.cards import get_cards_service
from ..services.operations import get_operations_service


def register_print_template_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/print-templates", "print_templates", print_templates, methods=["GET"])
    bp.add_url_rule("/print-templates", "print_templates_create", print_templates_create, methods=["POST"])
    bp.add_url_rule(
        "/print-templates/<int:template_id>/preview",
        "print_templates_preview",
        print_templates_preview,
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


def _payload() -> dict:
    layout = {
        "preview_mode": "visual_design_room",
        "card_width_mm": _float("card_width_mm", 85),
        "card_height_mm": _float("card_height_mm", 54),
        "design_preset": request.form.get("design_preset") or "modern",
        "gradient_start": request.form.get("gradient_start") or "#0f172a",
        "gradient_end": request.form.get("gradient_end") or "#22a7bd",
        "accent_color": request.form.get("accent_color") or "#f59e0b",
        "text_color": request.form.get("text_color") or request.form.get("color") or "#ffffff",
        "surface_color": request.form.get("surface_color") or "#e8f7fb",
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


def print_templates_export_pdf(template_id: int):
    batch_id_raw = request.args.get("batch_id") or ""
    try:
        batch_id = int(batch_id_raw) if batch_id_raw else None
    except ValueError:
        flash("معرّف الحزمة غير صحيح.", "error")
        return redirect(url_for("radius.print_templates"))
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
