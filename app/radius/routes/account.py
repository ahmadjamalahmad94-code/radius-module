"""Current admin account surface."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..auth.session_helpers import clear_current_admin, current_admin
from ..db.repos import admins_repo
from ..services.license_admin_identity_sync import LicenseAdminIdentitySyncService


def register_account_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/account", "account", account, methods=["GET"])
    bp.add_url_rule("/account/password", "account_password", account_password, methods=["POST"])


def account():
    admin = current_admin()
    if not admin:
        return redirect(url_for("radius.auth_login"))
    return render_template("radius/account.html", admin=admin)


def account_password():
    admin = current_admin()
    if not admin:
        return redirect(url_for("radius.auth_login"))
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""
    from ..auth import login_throttle
    _pw_key = f"id:{int(admin.id or 0)}"
    wait = login_throttle.retry_after("admin_password", _pw_key)
    if wait:
        flash(login_throttle.locked_message(wait), "error")
        return redirect(url_for("radius.account"))
    if not admins_repo.verify_password(current_password, admin.password_hash):
        login_throttle.register_failure("admin_password", _pw_key)
        flash("كلمة المرور الحالية غير صحيحة.", "error")
        return redirect(url_for("radius.account"))
    login_throttle.register_success("admin_password", _pw_key)
    if len(new_password) < 8:
        flash("كلمة المرور الجديدة يجب أن تكون 8 أحرف على الأقل.", "error")
        return redirect(url_for("radius.account"))
    if new_password != confirm_password:
        flash("تأكيد كلمة المرور غير مطابق.", "error")
        return redirect(url_for("radius.account"))
    if new_password == current_password:
        # same rule as POST /api/admin/password (re-test R08 NEW-3)
        flash("كلمة المرور الجديدة يجب أن تختلف عن الحالية.", "error")
        return redirect(url_for("radius.account"))

    if admin.managed_by_license_admin:
        result = LicenseAdminIdentitySyncService().change_password_from_runtime(
            admin=admin,
            new_password=new_password,
            tenant_id=int(session.get("tenant_id") or 1),
        )
        if result.get("ok"):
            # تغيير كلمة المرور يطرد كل الأجهزة المفتوحة على الحساب — ومنها
            # جلسات تطبيق الجوال (update_admin لا يُستدعى في هذا المسار).
            try:
                from ..db.repos import api_tokens_repo
                api_tokens_repo.revoke_admin_tokens(int(admin.id or 0))
            except Exception:  # noqa: BLE001
                pass
            clear_current_admin()
            flash("تم تحديث كلمة المرور من لوحة التراخيص، وتم تسجيل الخروج من "
                  "كل الأجهزة. سجّل الدخول بكلمتك الجديدة.", "success")
            return redirect(url_for("radius.auth_login"))
        else:
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            flash(error.get("message") or "تعذر تحديث كلمة المرور عبر لوحة التراخيص.", "error")
        return redirect(url_for("radius.account"))

    admins_repo.update_admin(int(admin.id or 0), password=new_password)
    # رفع إلزام التغيير عند أول دخول (إن كان مضبوطاً) بعد تغييرٍ محلّي ناجح.
    if getattr(admin, "must_change_password", False):
        admins_repo.clear_must_change_password(int(admin.id or 0))
    # قرار المالك: أي تغيير لكلمة المرور يطرد كل الجلسات المفتوحة على الحساب
    # (كل الأجهزة، بما فيها هذه) — update_admin زاد ختم الجلسة فعلًا.
    clear_current_admin()
    flash("تم تحديث كلمة المرور، وتم تسجيل الخروج من كل الأجهزة. "
          "سجّل الدخول بكلمتك الجديدة.", "success")
    return redirect(url_for("radius.auth_login"))
