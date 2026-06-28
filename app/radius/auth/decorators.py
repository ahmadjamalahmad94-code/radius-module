"""decorators للحماية: login_required + require_perm."""
from __future__ import annotations

import functools

from flask import flash, redirect, request, url_for

from ..services.admins import get_admins_service
from .session_helpers import current_admin, current_admin_id


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_admin_id():
            flash("سجّل الدخول للمتابعة.", "warning")
            return redirect(url_for("radius.auth_login", next=request.path))
        a = current_admin()
        if a is None or not a.enabled:
            from .session_helpers import clear_current_admin
            clear_current_admin()
            flash("الحساب غير متاح، أعد تسجيل الدخول.", "error")
            return redirect(url_for("radius.auth_login"))
        return view(*args, **kwargs)
    return wrapped


def require_perm(permission: str):
    """يتطلّب صلاحية محدّدة على الـ admin الحالي."""
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            a = current_admin()
            if a is None:
                return redirect(url_for("radius.auth_login"))
            # المالك الرئيسي وحده يَتجاوز فحص الصلاحية. علم is_super_admin
            # وحده (دور super_admin المُسنَد / تجاوز الترخيص) لم يَعُد كافيًا
            # — يَمرّ صاحبه عبر فحص الصلاحية المُسمّاة كأيّ مدير.
            from ..db.repos import admins_repo
            if admins_repo.is_primary_owner(getattr(a, "id", None)):
                return view(*args, **kwargs)
            perms = get_admins_service().permissions_of(a)
            if permission not in perms:
                flash(f"لا تملك الصلاحية: {permission}", "error")
                return redirect(url_for("radius.dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator
