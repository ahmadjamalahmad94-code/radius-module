"""NAS Devices routes — CRUD مع الحقول الكاملة."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import NAS_VENDORS, NAS_VENDOR_MIKROTIK
from ..core.errors import RadiusError
from ..core.types import NasDevice
from ..services.devices import get_nas_devices_service


def register_devices_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/devices", "devices_list", devices_list, methods=["GET"])
    bp.add_url_rule("/devices/new", "devices_new", devices_new, methods=["GET"])
    bp.add_url_rule("/devices", "devices_create", devices_create, methods=["POST"])
    bp.add_url_rule("/devices/<int:nas_id>/edit", "devices_edit", devices_edit, methods=["GET"])
    bp.add_url_rule("/devices/<int:nas_id>", "devices_update", devices_update, methods=["POST"])
    bp.add_url_rule("/devices/<int:nas_id>/delete", "devices_delete", devices_delete, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _i(name, default):
    try:
        return int(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


def _dto(*, nas_id=None) -> NasDevice:
    return NasDevice(
        id=nas_id,
        name=(request.form.get("name") or "").strip(),
        address=(request.form.get("address") or "").strip(),
        secret=(request.form.get("secret") or "").strip(),
        vendor=(request.form.get("vendor") or NAS_VENDOR_MIKROTIK).strip().lower(),
        nas_type=(request.form.get("nas_type") or "hotspot").strip().lower(),
        auth_port=_i("auth_port", 1812),
        acct_port=_i("acct_port", 1813),
        coa_port=_i("coa_port", 3799),
        location=(request.form.get("location") or "").strip(),
        coordinates=(request.form.get("coordinates") or "").strip(),
        monitoring_enabled=bool(request.form.get("monitoring_enabled")),
        description=(request.form.get("description") or "").strip(),
        enabled=bool(request.form.get("enabled")),
    )


def devices_list():
    items = get_nas_devices_service().list(limit=500)
    return render_template("radius/devices_list.html", items=items)


def devices_new():
    return render_template("radius/devices_form.html",
        device=NasDevice(id=None, name="", address="", secret="",
                         vendor=NAS_VENDOR_MIKROTIK, enabled=True, monitoring_enabled=True),
        vendors=NAS_VENDORS, is_new=True)


def devices_create():
    dto = _dto()
    try:
        saved = get_nas_devices_service().create(actor=_actor(), device=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/devices_form.html",
            device=dto, vendors=NAS_VENDORS, is_new=True), 400
    flash(f"تم إنشاء «{saved.name}».", "success")
    return redirect(url_for("radius.devices_list"))


def devices_edit(nas_id: int):
    try:
        device = get_nas_devices_service().get(nas_id)
    except RadiusError:
        abort(404)
    return render_template("radius/devices_form.html",
        device=device, vendors=NAS_VENDORS, is_new=False)


def devices_update(nas_id: int):
    dto = _dto(nas_id=nas_id)
    try:
        get_nas_devices_service().update(actor=_actor(), device=dto)
    except RadiusError as e:
        flash(e.message, "error")
        return render_template("radius/devices_form.html",
            device=dto, vendors=NAS_VENDORS, is_new=False), 400
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.devices_list"))


def devices_delete(nas_id: int):
    try:
        get_nas_devices_service().delete(actor=_actor(), nas_id=nas_id)
        flash("تم الحذف.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.devices_list"))
