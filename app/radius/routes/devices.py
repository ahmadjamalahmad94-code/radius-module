"""NAS Devices routes — CRUD + test-connection.

RM-H5: extended form/list with full AdvRadius fields and a /test
endpoint that does a TCP socket reachability check (timeout 2s).
"""
from __future__ import annotations

import socket

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.constants import NAS_VENDORS, NAS_VENDOR_MIKROTIK
from ..core.errors import RadiusError
from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import NasDevice
from ..db.repos import nas_repo
from ..services.devices import get_nas_devices_service


def register_devices_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/devices", "devices_list", devices_list, methods=["GET"])
    bp.add_url_rule("/devices/new", "devices_new", devices_new, methods=["GET"])
    bp.add_url_rule("/devices", "devices_create", devices_create, methods=["POST"])
    bp.add_url_rule("/devices/<int:nas_id>/edit", "devices_edit", devices_edit, methods=["GET"])
    bp.add_url_rule("/devices/<int:nas_id>", "devices_update", devices_update, methods=["POST"])
    bp.add_url_rule("/devices/<int:nas_id>/delete", "devices_delete", devices_delete, methods=["POST"])
    bp.add_url_rule("/devices/<int:nas_id>/test", "devices_test", devices_test, methods=["POST"])
    # O3 — Operations Center toggle + bulk toggle
    bp.add_url_rule(
        "/devices/<int:nas_id>/toggle",
        "devices_toggle", devices_toggle, methods=["POST"],
    )
    bp.add_url_rule(
        "/devices/bulk-toggle",
        "devices_bulk_toggle", devices_bulk_toggle, methods=["POST"],
    )


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    try:
        from flask import g
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (ImportError, RuntimeError):
        return DEFAULT_TENANT_ID


def _i(name, default):
    try: return int(request.form.get(name) or default)
    except (TypeError, ValueError): return default


def _b(name): return request.form.get(name, "") in ("1","on","true","yes")
def _s(name): return (request.form.get(name) or "").strip()


def _dto(*, nas_id=None) -> NasDevice:
    return NasDevice(
        id=nas_id,
        name=_s("name"),
        address=_s("address"),
        secret=_s("secret"),
        vendor=_s("vendor").lower() or NAS_VENDOR_MIKROTIK,
        nas_type=_s("nas_type").lower() or "hotspot",
        shortname=_s("shortname"),
        ports=_i("ports", 0),
        snmp_community=_s("snmp_community"),
        auth_port=_i("auth_port", 1812),
        acct_port=_i("acct_port", 1813),
        coa_port=_i("coa_port", 3799),
        api_port=_i("api_port", 8728),
        api_user=_s("api_user"),
        api_password=_s("api_password"),
        api_use_tls=_b("api_use_tls"),
        location=_s("location"),
        coordinates=_s("coordinates"),
        monitoring_enabled=_b("monitoring_enabled"),
        description=_s("description"),
        enabled=_b("enabled"),
        # RM-H5
        require_message_authenticator=_b("require_message_authenticator"),
        ssh_port=_i("ssh_port", 22),
        tags=_s("tags"),
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
        flash("تمت أرشفة جهاز الشبكة. يمكنك استعادته من سلة المحذوفات.", "success")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(url_for("radius.devices_list"))


def devices_test(nas_id: int):
    """RM-H5: TCP reachability check على api_port. timeout 2s.
    لا يستدعي MikroTik API الفعلي — فقط فحص socket. النتيجة تُكتب في
    last_check_at + last_check_status للعرض في list."""
    try:
        dev = get_nas_devices_service().get(nas_id)
    except RadiusError:
        abort(404)
    ip = dev.address; port = int(dev.api_port or 8728)
    status = "unknown"
    try:
        with socket.create_connection((ip, port), timeout=2.0):
            status = "reachable"
            flash(f"✓ الاتصال نجح على {ip}:{port}", "success")
    except socket.timeout:
        status = "timeout"
        flash(f"⏱ انتهت المهلة (2s) على {ip}:{port}", "warning")
    except OSError as exc:
        status = "unreachable"
        flash(f"✗ تعذّر الاتصال: {exc}", "error")
    try:
        nas_repo.record_check(_tid(), nas_id, status=status)
    except Exception:
        pass
    return redirect(url_for("radius.devices_list"))


# ─── O3: enable/disable toggles ──────────────────────────────────


def _flip_enabled(nas_id: int, *, action: str) -> bool:
    """Flip enabled flag for one NAS row. `action` must be
    'enable' or 'disable'. Returns True when a row was updated.
    Goes through the service so the audit trail captures it.
    """
    if action not in ("enable", "disable"):
        return False
    svc = get_nas_devices_service()
    try:
        dev = svc.get(nas_id)
    except RadiusError:
        return False
    if dev is None:
        return False
    from dataclasses import replace as _replace
    new = _replace(dev, enabled=(action == "enable"))
    try:
        svc.update(actor=_actor(), device=new)
    except RadiusError:
        return False
    return True


def devices_toggle(nas_id: int):
    """Per-row enable/disable from Operations Center. Accepts
    `action=enable|disable` from form, otherwise auto-flips."""
    requested = (request.form.get("action") or "").strip().lower()
    if requested not in ("enable", "disable"):
        # No explicit action → flip whichever way the row is now.
        try:
            dev = get_nas_devices_service().get(nas_id)
        except RadiusError:
            abort(404)
        if dev is None:
            abort(404)
        requested = "disable" if dev.enabled else "enable"
    ok = _flip_enabled(nas_id, action=requested)
    if not ok:
        flash("تعذّر تغيير حالة الراوتر — تأكّد من وجوده.", "error")
    else:
        flash(
            "تم تفعيل الراوتر." if requested == "enable"
            else "تم تعطيل الراوتر.",
            "success",
        )
    # If the caller came from Operations Center, send them back
    # there; otherwise the legacy devices list.
    next_url = request.form.get("next") or request.referrer or ""
    if "/mt/operations" in next_url:
        return redirect(url_for("radius.mt_operations"))
    return redirect(url_for("radius.devices_list"))


def devices_bulk_toggle():
    """Bulk enable/disable from Operations Center.

    Form keys:
        ids[]    — list of nas_id ints (HTML multi-value)
        action   — 'enable' or 'disable'

    Rejects unknown actions; silently skips ids that don't exist
    (caller may have stale selections). The audit trail records
    one update per affected NAS via the underlying service.
    """
    action = (request.form.get("action") or "").strip().lower()
    if action not in ("enable", "disable"):
        flash("إجراء غير معروف.", "error")
        return redirect(url_for("radius.mt_operations"))

    raw_ids = request.form.getlist("ids") or request.form.getlist("ids[]")
    nas_ids: list[int] = []
    for raw in raw_ids:
        try:
            nas_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not nas_ids:
        flash("لم تختر أي راوتر.", "error")
        return redirect(url_for("radius.mt_operations"))

    changed = 0
    for nid in nas_ids:
        if _flip_enabled(nid, action=action):
            changed += 1

    if changed:
        verb = "تفعيل" if action == "enable" else "تعطيل"
        flash(f"تم {verb} {changed} راوتر.", "success")
    else:
        flash("لم يتم تغيير أي راوتر — قد تكون الاختيارات قديمة.", "warning")
    return redirect(url_for("radius.mt_operations"))
