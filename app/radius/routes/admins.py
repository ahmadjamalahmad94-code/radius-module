"""Admins + Roles routes."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.admins import get_admins_service


def register_admins_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/admins", "admins_list", admins_list, methods=["GET"])
    bp.add_url_rule("/admins/new", "admins_new", admins_new, methods=["GET"])
    bp.add_url_rule("/admins", "admins_create", admins_create, methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>/edit", "admins_edit", admins_edit, methods=["GET"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_update", admins_update, methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>/delete", "admins_delete", admins_delete, methods=["POST"])
    bp.add_url_rule("/roles", "roles_list", roles_list, methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_update", roles_update, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def admins_list():
    svc = get_admins_service()
    admins = svc.list_admins()
    roles = {r.id: r for r in svc.list_roles()}
    return render_template("radius/admins_list.html", admins=admins, roles=roles)


def admins_new():
    roles = get_admins_service().list_roles()
    return render_template("radius/admins_form.html", admin=None, roles=roles, is_new=True)


def admins_create():
    svc = get_admins_service()
    try:
        a = svc.create_admin(
            actor=_actor(),
            username=(request.form.get("username") or "").strip(),
            password=(request.form.get("password") or "").strip(),
            full_name=(request.form.get("full_name") or "").strip(),
            email=(request.form.get("email") or "").strip(),
            mobile=(request.form.get("mobile") or "").strip(),
            role_id=int(request.form.get("role_id") or 0) or None,
            enabled=bool(request.form.get("enabled")),
        )
    except (ValueError, RadiusError) as e:
        flash(str(e), "error")
        return render_template("radius/admins_form.html",
            admin=None, roles=svc.list_roles(), is_new=True), 400
    flash(f"تم إنشاء المدير «{a.username}».", "success")
    return redirect(url_for("radius.admins_list"))


def admins_edit(admin_id: int):
    svc = get_admins_service()
    a = svc.get_admin(admin_id)
    if not a: abort(404)
    return render_template("radius/admins_form.html", admin=a, roles=svc.list_roles(), is_new=False)


def admins_update(admin_id: int):
    svc = get_admins_service()
    changes = {}
    for k in ("full_name","email","mobile"):
        v = request.form.get(k)
        if v is not None: changes[k] = v.strip()
    if request.form.get("role_id"):
        try: changes["role_id"] = int(request.form["role_id"])
        except: pass
    changes["enabled"] = bool(request.form.get("enabled"))
    password = (request.form.get("password") or "").strip()
    try:
        svc.update_admin(actor=_actor(), admin_id=admin_id,
                         password=password or None, **changes)
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error"); return redirect(url_for("radius.admins_list"))
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.admins_list"))


def admins_delete(admin_id: int):
    try:
        get_admins_service().delete_admin(actor=_actor(), admin_id=admin_id)
        flash("تم حذف المدير.", "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.admins_list"))


# ─────────────── roles ───────────────

def roles_list():
    svc = get_admins_service()
    roles = svc.list_roles()
    perms = svc.all_permissions()
    return render_template("radius/roles_list.html", roles=roles, perms=perms)


def roles_update(role_id: int):
    svc = get_admins_service()
    chosen = tuple(request.form.getlist("permissions"))
    try:
        svc.update_role_permissions(actor=_actor(), role_id=role_id, perms=chosen)
        flash("تم تحديث الصلاحيات.", "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.roles_list"))
