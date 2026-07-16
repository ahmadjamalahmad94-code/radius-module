"""إدارة الراوترات عن بُعد (TR-069) — مسارات الويب (وحدة تجريبيّة).

كلها محروسة بـ (1) علم `tr069_enabled()`، (2) صلاحية `routers.*`، (3) عزل الملكيّة
داخل الخدمة. لا تختلط بأي قسم آخر — قسم مستقلّ «المعمل (تجريبيّ)» في الشريط.
"""
from __future__ import annotations

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)

from ..auth.decorators import require_perm
from ..auth.session_helpers import current_admin, current_admin_id, is_super_admin
from ..core.tenant import DEFAULT_TENANT_ID
from ..services.tr069 import config as tr069_config
from ..services.tr069.action_service import Tr069ActionError, Tr069ActionService, ACTION_PERM
from ..services.tr069.device_service import Tr069DeviceService
from ..services.tr069.enrollment_service import Tr069EnrollmentService


def _tid() -> int:
    return int(session.get("tenant_id") or DEFAULT_TENANT_ID)


def _can_view_all() -> bool:
    return is_super_admin()


def _can_sensitive() -> bool:
    from ..auth.ui_permissions import can
    return can("routers.view_sensitive_data")


def _guard():
    """يتحقّق من تفعيل الوحدة التجريبيّة؛ يُرجع None إن سمح، أو Response توجيه."""
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    if not tr069_config.tr069_enabled():
        abort(404)
    return None


def register_routers_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/routers", "routers_list", routers_list, methods=["GET"])
    bp.add_url_rule("/routers/setup", "routers_setup", routers_setup, methods=["GET"])
    bp.add_url_rule("/routers/serial-binding", "routers_serial_binding_add",
                    routers_serial_binding_add, methods=["POST"])
    bp.add_url_rule("/routers/serial-binding/<int:binding_id>/delete",
                    "routers_serial_binding_delete",
                    routers_serial_binding_delete, methods=["POST"])
    bp.add_url_rule("/routers/enroll", "routers_enroll",
                    routers_enroll, methods=["GET", "POST"])
    bp.add_url_rule("/routers/enroll/<int:device_id>/cancel", "routers_enroll_cancel",
                    routers_enroll_cancel, methods=["POST"])
    bp.add_url_rule("/routers/<int:device_id>", "routers_detail",
                    routers_detail, methods=["GET"])
    bp.add_url_rule("/routers/<int:device_id>/action", "routers_action",
                    routers_action, methods=["POST"])
    bp.add_url_rule("/routers/<int:device_id>/link", "routers_link",
                    routers_link, methods=["POST"])


@require_perm("routers.view")
def routers_list():
    g = _guard()
    if g:
        return g
    svc = Tr069DeviceService(_tid())
    status = (request.args.get("status") or "").strip()
    q = (request.args.get("q") or "").strip()
    items = svc.list_devices(viewer_admin_id=current_admin_id(),
                             can_view_all=_can_view_all(), status=status, q=q)
    return render_template("radius/routers_list.html",
                           items=items, counts=svc.counts(),
                           status=status, q=q,
                           nbi_ok=_nbi_ok())


@require_perm("routers.view")
def routers_detail(device_id: int):
    g = _guard()
    if g:
        return g
    svc = Tr069DeviceService(_tid())
    dev = svc.get_device(device_id, viewer_admin_id=current_admin_id(),
                         can_view_all=_can_view_all())
    if not dev:
        abort(404)
    # بيانات التسجيل تُعرَض مرّة واحدة فقط (بعد الإنشاء) ثم تُمحى من الجلسة.
    enroll = session.pop(f"_enroll_{device_id}", None)
    return render_template("radius/routers_detail.html", d=dev, enroll=enroll,
                           can_sensitive=_can_sensitive())


@require_perm("routers.view")
def routers_setup():
    """صفحة الإعداد «بلا لمس»: عنوان ACS المشترك + الربط المسبق بالسيريال."""
    g = _guard()
    if g:
        return g
    svc = Tr069DeviceService(_tid())
    bindings = svc.list_serial_bindings(viewer_admin_id=current_admin_id(),
                                        can_view_all=_can_view_all())
    return render_template(
        "radius/routers_setup.html",
        cwmp_url=tr069_config.cwmp_public_url(),
        global_user=tr069_config.cwmp_global_username(),
        global_auth=tr069_config.cwmp_global_auth_enabled(),
        bindings=bindings, nbi_ok=_nbi_ok())


@require_perm("routers.manage")
def routers_serial_binding_add():
    g = _guard()
    if g:
        return g
    serial = (request.form.get("serial_number") or "").strip()
    username = (request.form.get("radius_username") or "").strip()
    sub_id = request.form.get("subscriber_id") or None
    note = (request.form.get("note") or "").strip()
    if not serial:
        flash("أدخل الرقم التسلسليّ للراوتر.", "error")
        return redirect(url_for("radius.routers_setup"))
    Tr069DeviceService(_tid()).add_serial_binding(
        serial_number=serial, radius_username=username,
        subscriber_id=int(sub_id) if sub_id else None,
        owner_admin_id=current_admin_id(), note=note,
        actor=session.get("admin_name") or "admin")
    flash("حُفِظ الربط المسبق — سيُربَط الراوتر تلقائيًّا بمجرّد اتصاله.", "success")
    return redirect(url_for("radius.routers_setup"))


@require_perm("routers.manage")
def routers_serial_binding_delete(binding_id: int):
    g = _guard()
    if g:
        return g
    ok = Tr069DeviceService(_tid()).delete_serial_binding(
        binding_id, viewer_admin_id=current_admin_id(), can_view_all=_can_view_all())
    flash("حُذِف الربط المسبق." if ok else "تعذّر الحذف.", "success" if ok else "error")
    return redirect(url_for("radius.routers_setup"))


@require_perm("routers.manage")
def routers_enroll():
    g = _guard()
    if g:
        return g
    if request.method == "POST":
        sub_id = request.form.get("subscriber_id") or None
        username = (request.form.get("radius_username") or "").strip()
        svc = Tr069EnrollmentService(_tid())
        result = svc.create_enrollment(
            subscriber_id=int(sub_id) if sub_id else None,
            radius_username=username,
            owner_admin_id=current_admin_id(),
            actor=session.get("admin_name") or "admin")
        # بيانات التسجيل تُعرَض مرّة واحدة — تُمرَّر عبر الجلسة لعرضها بعد التوجيه.
        session[f"_enroll_{result['device_id']}"] = result
        return redirect(url_for("radius.routers_detail", device_id=result["device_id"]))
    return render_template("radius/routers_enroll.html",
                           cwmp_url=tr069_config.cwmp_public_url())


@require_perm("routers.manage")
def routers_enroll_cancel(device_id: int):
    g = _guard()
    if g:
        return g
    Tr069EnrollmentService(_tid()).cancel(device_id)
    flash("أُلغي التسجيل المعلّق.", "success")
    return redirect(url_for("radius.routers_list"))


def routers_action(device_id: int):
    g = _guard()
    if g:
        return g
    action_type = (request.form.get("action_type") or "").strip()
    # فرض الصلاحية المناسبة لكل فعل.
    from ..auth.ui_permissions import can
    perm = ACTION_PERM.get(action_type, "routers.manage")
    if not can(perm):
        flash(f"لا تملك الصلاحية: {perm}", "error")
        return redirect(url_for("radius.routers_detail", device_id=device_id))
    params = {k: v for k, v in request.form.items()
              if k not in ("action_type", "_csrf_token", "csrf_token")}
    try:
        svc = Tr069ActionService(_tid())
        res = svc.queue(device_id=device_id, action_type=action_type, params=params,
                        actor=session.get("admin_name") or "admin",
                        owner_admin_id=current_admin_id(),
                        viewer_admin_id=current_admin_id(), can_view_all=_can_view_all())
        flash(f"تمّت جدولة الأمر: {res['summary']} — سيُنفَّذ عند اتصال الراوتر.", "success")
    except Tr069ActionError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.routers_detail", device_id=device_id))


@require_perm("routers.manage")
def routers_link(device_id: int):
    g = _guard()
    if g:
        return g
    sub_id = request.form.get("subscriber_id") or None
    username = (request.form.get("radius_username") or "").strip()
    ok = Tr069DeviceService(_tid()).link_subscriber(
        device_id, int(sub_id) if sub_id else None, username,
        actor=session.get("admin_name") or "admin",
        viewer_admin_id=current_admin_id(), can_view_all=_can_view_all())
    flash("تمّ ربط الجهاز بالمشترك." if ok else "تعذّر الربط.",
          "success" if ok else "error")
    return redirect(url_for("radius.routers_detail", device_id=device_id))


def _nbi_ok() -> bool:
    try:
        from ..services.tr069.genieacs_client import get_client
        return get_client().health()
    except Exception:  # noqa: BLE001
        return False
