"""Web UI for card print templates."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.operations import get_operations_service


def register_print_template_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/print-templates", "print_templates", print_templates, methods=["GET"])
    bp.add_url_rule(
        "/print-templates",
        "print_templates_create",
        print_templates_create,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/preview",
        "print_templates_preview",
        print_templates_preview,
        methods=["POST"],
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


def _payload() -> dict:
    return {
        "name": request.form.get("name"),
        "orientation": request.form.get("orientation") or "portrait",
        "cards_per_row": _int("cards_per_row", 2),
        "cards_per_column": _int("cards_per_column", 5),
        "page_size": request.form.get("page_size") or "A4",
        "show_qr": request.form.get("show_qr") in {"1", "true", "on", "yes"},
        "username_x": _float("username_x"),
        "username_y": _float("username_y"),
        "password_x": _float("password_x"),
        "password_y": _float("password_y"),
        "qr_x": _float("qr_x"),
        "qr_y": _float("qr_y"),
        "font_size": _int("font_size", 12),
        "color": request.form.get("color") or "#1f2937",
        "layout": {
            "preview_mode": "json_layout_preview",
            "card_width_mm": _float("card_width_mm", 85),
            "card_height_mm": _float("card_height_mm", 54),
        },
    }


def print_templates():
    templates = get_operations_service().list_print_templates(tenant_id=_tid(), limit=500)
    return render_template(
        "radius/print_templates.html",
        templates=templates,
        preview=None,
    )


def print_templates_create():
    try:
        get_operations_service().create_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            data=_payload(),
        )
        flash("تم حفظ قالب الطباعة. المعاينة بصرية الآن، ولا تنشئ PDF نهائي.", "success")
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
        templates = get_operations_service().list_print_templates(tenant_id=_tid(), limit=500)
        return render_template(
            "radius/print_templates.html",
            templates=templates,
            preview=preview,
        )
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.print_templates"))
