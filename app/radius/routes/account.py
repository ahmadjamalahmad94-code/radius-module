"""Current admin account surface."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..auth.session_helpers import current_admin
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
    if not admins_repo.verify_password(current_password, admin.password_hash):
        flash("كلمة المرور الحالية غير صحيحة.", "error")
        return redirect(url_for("radius.account"))
    if len(new_password) < 8:
        flash("كلمة المرور الجديدة يجب أن تكون 8 أحرف على الأقل.", "error")
        return redirect(url_for("radius.account"))
    if new_password != confirm_password:
        flash("تأكيد كلمة المرور غير مطابق.", "error")
        return redirect(url_for("radius.account"))

    if admin.managed_by_license_admin:
        result = LicenseAdminIdentitySyncService().change_password_from_runtime(
            admin=admin,
            new_password=new_password,
            tenant_id=int(session.get("tenant_id") or 1),
        )
        if result.get("ok"):
            flash("تم تحديث كلمة المرور من لوحة التراخيص", "success")
        else:
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            flash(error.get("message") or "تعذر تحديث كلمة المرور عبر لوحة التراخيص.", "error")
        return redirect(url_for("radius.account"))

    admins_repo.update_admin(int(admin.id or 0), password=new_password)
    flash("تم تحديث كلمة المرور المحلية.", "success")
    return redirect(url_for("radius.account"))
