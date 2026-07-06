"""Admins + Roles routes.

RM-H6: extends admins form with profile fields (phone, notes, avatar,
tags) and adds roles CRUD (create/edit/delete with color picker and
grouped permissions). Also adds /admins/profile-summary read-only view.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..db.repos import admins_repo
from ..services.admins import get_admins_service


def register_admins_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/admins", "admins_list", admins_list, methods=["GET"])
    bp.add_url_rule("/admins/new", "admins_new", admins_new, methods=["GET"])
    bp.add_url_rule("/admins", "admins_create", admins_create, methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>/edit", "admins_edit", admins_edit, methods=["GET"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_update", admins_update, methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>/delete", "admins_delete", admins_delete, methods=["POST"])
    bp.add_url_rule("/admins/profile-summary", "admins_profile_summary",
                    admins_profile_summary, methods=["GET"])
    bp.add_url_rule("/roles", "roles_list", roles_list, methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_update", roles_update, methods=["POST"])
    # RM-H6: roles CRUD
    bp.add_url_rule("/roles/new", "roles_new", roles_new, methods=["GET"])
    bp.add_url_rule("/roles", "roles_create", roles_create, methods=["POST"])
    bp.add_url_rule("/roles/<int:role_id>/edit", "roles_edit", roles_edit, methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>/save", "roles_save", roles_save, methods=["POST"])
    bp.add_url_rule("/roles/<int:role_id>/delete", "roles_delete", roles_delete, methods=["POST"])
    # وراثة الأفعال/الرؤية: محرّر أساس الدور (يَرثه كل مدير من دوره)
    bp.add_url_rule("/roles/<int:role_id>/grants", "roles_grants", roles_grants, methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>/grants", "roles_grants_save", roles_grants_save, methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def admins_list():
    svc = get_admins_service()
    admins = svc.list_admins()
    roles_seq = svc.list_roles()
    roles = {r.id: r for r in roles_seq}
    return render_template(
        "radius/admins_list.html", admins=admins, roles=roles,
        # قائمة الأدوار كما هي (ترتيبًا) لقائمة «الدور» داخل صندوق «إضافة مدير» العائم
        roles_all=roles_seq,
        # ?new=1 يفتح الصندوق العائم «إضافة مدير» تلقائيًا (الرابط القديم /admins/new يبقى حيًّا)
        open_new_modal=(request.args.get("new") == "1"),
    )


def admins_new():
    # نموذج الإنشاء أصبح صندوقًا عائمًا داخل صفحة القائمة —
    # الرابط القديم يبقى حيًّا ويفتح النافذة تلقائيًا عبر ?new=1.
    return redirect(url_for("radius.admins_list", new=1))


def _s(name: str) -> str:
    return (request.form.get(name) or "").strip()


def admins_create():
    svc = get_admins_service()
    try:
        a = svc.create_admin(
            actor=_actor(),
            username=_s("username"),
            password=_s("password"),
            full_name=_s("full_name"),
            email=_s("email"),
            mobile=_s("mobile"),
            role_id=int(request.form.get("role_id") or 0) or None,
            enabled=bool(request.form.get("enabled")),
            # RM-H6: profile fields (passed via repo since service signature may not accept)
        )
        # update profile fields via repo (service.create_admin doesn't take them)
        profile = {
            "phone":         _s("phone"),
            "profile_notes": _s("profile_notes"),
            "avatar_url":    _s("avatar_url"),
            "tags":          _s("tags"),
        }
        if any(profile.values()):
            try: admins_repo.update_admin(a.id, **profile)
            except Exception: pass
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
    for k in ("full_name","email","mobile",
              # RM-H6 profile fields
              "phone","profile_notes","avatar_url","tags"):
        v = request.form.get(k)
        if v is not None: changes[k] = v.strip()
    if request.form.get("role_id"):
        try: changes["role_id"] = int(request.form["role_id"])
        except (TypeError, ValueError): pass
    changes["enabled"] = bool(request.form.get("enabled"))
    password = (request.form.get("password") or "").strip()
    # تطبيق profile fields عبر repo مباشرة لتجنب تقييد الـ service
    profile_keys = ("phone","profile_notes","avatar_url","tags")
    profile_changes = {k: changes.pop(k) for k in list(changes) if k in profile_keys}

    # ── Per-manager monetary credit caps — SUPER-ADMIN ONLY (server-side).
    # The section is hidden for non-supers; if one POSTs the caps anyway → 403.
    from ..auth.session_helpers import is_super_admin
    cap_changes: dict = {}
    if request.form.get("credit_caps_present"):
        if not is_super_admin():
            abort(403)
        from ..services.business_os_finance import money_to_minor
        cap_changes = {
            "debt_cap_enabled": bool(request.form.get("debt_cap_enabled")),
            "debt_cap_minor": money_to_minor(request.form.get("debt_cap_amount") or 0),
            "loan_cap_enabled": bool(request.form.get("loan_cap_enabled")),
            "loan_cap_minor": money_to_minor(request.form.get("loan_cap_amount") or 0),
        }
    try:
        svc.update_admin(actor=_actor(), admin_id=admin_id,
                         password=password or None, **changes)
        if profile_changes:
            try: admins_repo.update_admin(admin_id, **profile_changes)
            except Exception: pass
        if cap_changes:
            admins_repo.update_admin(admin_id, **cap_changes)
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error"); return redirect(url_for("radius.admins_list"))
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.admins_list"))


def admins_delete(admin_id: int):
    try:
        get_admins_service().delete_admin(actor=_actor(), admin_id=admin_id)
        flash("تمت أرشفة المدير. يمكنك استعادته من سلة المحذوفات.", "success")
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
    """legacy: permissions-only update."""
    svc = get_admins_service()
    chosen = tuple(request.form.getlist("permissions"))
    try:
        svc.update_role_permissions(actor=_actor(), role_id=role_id, perms=chosen)
        flash("تم تحديث الصلاحيات.", "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.roles_list"))


# ════════════════════════════════════════════════════════════════
# RM-H6: roles CRUD + admins profile-summary
# ════════════════════════════════════════════════════════════════

def admins_profile_summary():
    """صفحة ملخّص قراءة فقط لكل المدراء."""
    svc = get_admins_service()
    admins = svc.list_admins()
    roles = {r.id: r for r in svc.list_roles()}
    perms = svc.all_permissions()
    total = len(admins)
    active_count = sum(1 for a in admins if a.enabled)
    with_role = sum(1 for a in admins if a.role_id)
    with_login = sum(1 for a in admins if a.last_login_at)
    return render_template("radius/admins_profile_summary.html",
        admins=admins, roles=roles, perms_count=len(perms),
        stats={"total": total, "active": active_count,
                "with_role": with_role, "with_login": with_login})


def _permission_groups(perms):
    """يصنّف permissions حسب prefix (e.g. 'users.*', 'plans.*')."""
    from collections import defaultdict
    groups = defaultdict(list)
    for p in perms:
        prefix = p.split(".", 1)[0] if "." in p else "general"
        groups[prefix].append(p)
    return dict(sorted(groups.items()))


def roles_new():
    perms = get_admins_service().all_permissions()
    return render_template("radius/roles_form.html",
        role=None, perms=perms, is_new=True,
        groups=_permission_groups(perms))


def roles_create():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("اسم الدور مطلوب.", "error")
        return redirect(url_for("radius.roles_new"))
    try:
        admins_repo.create_role(
            name=name,
            display_name=(request.form.get("display_name") or "").strip() or name,
            description=(request.form.get("description") or "").strip(),
            permissions=tuple(request.form.getlist("permissions")),
            color=(request.form.get("color") or "#2BAACC").strip(),
        )
        flash(f"تم إنشاء الدور «{name}» ✓", "success")
        return redirect(url_for("radius.roles_list"))
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
        return redirect(url_for("radius.roles_new"))


def roles_edit(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r: abort(404)
    perms = get_admins_service().all_permissions()
    return render_template("radius/roles_form.html",
        role=r, perms=perms, is_new=False,
        groups=_permission_groups(perms))


def roles_save(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r: abort(404)
    try:
        admins_repo.update_role(
            role_id,
            display_name=(request.form.get("display_name") or "").strip() or r.name,
            description=(request.form.get("description") or "").strip(),
            permissions=tuple(request.form.getlist("permissions")),
            color=(request.form.get("color") or "#2BAACC").strip(),
        )
        flash("تم حفظ التعديلات ✓", "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.roles_list"))


def roles_delete(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r: abort(404)
    if r.is_system:
        flash("لا يمكن أرشفة دور النظام.", "error")
        return redirect(url_for("radius.roles_list"))
    try:
        admins_repo.delete_role(role_id)
        flash(f"تمت أرشفة الدور «{r.name}» ✓", "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.roles_list"))


def roles_grants(role_id: int):
    """محرّر «الأفعال المسموح بها + نطاق الرؤية» على مستوى الدور — يَرثه كلّ
    مدير من دوره تلقائيًّا (بدل ضبط كلّ مدير على حِدة). الحدود الرقميّة تبقى
    فرديّة لكلّ مدير."""
    r = admins_repo.get_role(role_id)
    if not r:
        abort(404)
    from ..services import manager_grants as _mg
    blob = admins_repo.get_role_granular(role_id)
    flags = blob.get("flags") if isinstance(blob.get("flags"), dict) else {}
    scope_flags = [
        {"key": k, "label": lbl, "checked": bool(flags.get(k))}
        for k, lbl in _mg.SCOPE_FLAG_REGISTRY.items()
    ]
    return render_template(
        "radius/roles_grants.html",
        role=r,
        action_catalog=_mg.role_action_catalog(blob),
        scope_flags=scope_flags,
        section_catalog=_mg.role_section_catalog(blob),
    )


def roles_grants_save(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r:
        abort(404)
    from ..services import manager_grants as _mg
    try:
        blob = _mg.parse_grants_form(request.form)
        admins_repo.set_role_granular(role_id, blob)
        flash(f"تم حفظ أساس صلاحيات الدور «{r.display_name or r.name}» — "
              f"يَرثه كلّ مدير بهذا الدور ✓", "success")
    except Exception as e:  # noqa: BLE001
        flash(str(e), "error")
    return redirect(url_for("radius.roles_grants", role_id=role_id))
