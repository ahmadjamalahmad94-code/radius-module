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
        # إلزام تغيير كلمة المرور عند أول دخول: الأدمن الذي أنشأته لوحة التراخيص
        # مركزياً بكلمة مرور أوليّة يُحوَّل إلى صفحة الحساب حتى يغيّرها. صفحة الحساب
        # وتسجيل الخروج غير محميَّين بهذا الـ decorator فلا تحدث حلقة إعادة توجيه.
        if getattr(a, "must_change_password", False):
            flash("لأمانك، يجب تغيير كلمة المرور قبل المتابعة.", "warning")
            return redirect(url_for("radius.account"))
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
                # 🔴 SEC r5perms — كان الرفضُ **توجيهًا 302** إلى اللوحةِ لأيّ
                # method. أي أنّ `POST /routers/<id>/link` و
                # `/routers/serial-binding` و`/routers/alert-settings`
                # و`/routers/enroll` تُجيب 302 لمديرٍ لا يملك `routers.manage`
                # (ولا دورَ يستطيع حملَه أصلًا بعد D14/F22) — الرفضُ حقيقيٌّ
                # ولا كتابةَ تقع، لكنّ أيَّ ماسحٍ آليٍّ أو تطبيقِ الجوّال
                # يقرأ 302 **نجاحًا**. نفسُ صنفِ NEW-11. الآن: الكتابةُ 403
                # (وJSON للطلباتِ غيرِ المتصفّحة)، والقراءةُ تَبقى توجيهًا
                # ودودًا إلى اللوحة.
                from flask import g as _g, jsonify
                mutating = request.method not in ("GET", "HEAD", "OPTIONS")
                try:
                    _g._rbac_denial = {"permission": permission,
                                       "reason": "permission"}
                except Exception:  # noqa: BLE001
                    pass
                if mutating:
                    try:
                        from ..routes.blueprint import _wants_json_response
                        wants_json = _wants_json_response()
                    except Exception:  # noqa: BLE001
                        wants_json = False
                    msg = f"لا تملك الصلاحية: {permission}"
                    if wants_json:
                        return jsonify({"ok": False, "error": msg,
                                        "code": "forbidden",
                                        "permission": permission}), 403
                    from flask import abort
                    abort(403)
                flash(f"لا تملك الصلاحية: {permission}", "error")
                return redirect(url_for("radius.dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator
