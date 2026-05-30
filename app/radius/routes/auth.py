"""routes الـ auth: login/logout."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..auth.session_helpers import clear_current_admin, set_current_admin
from ..core.tenant import DEFAULT_TENANT_ID
from ..services.admins import get_admins_service
from ..stores.tenants_store import TenantsStore


def register_auth_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/login", "auth_login", auth_login, methods=["GET", "POST"])
    bp.add_url_rule("/logout", "auth_logout", auth_logout, methods=["GET", "POST"])
    bp.add_url_rule("/switch-tenant", "auth_switch_tenant",
                    auth_switch_tenant, methods=["POST"])


def auth_login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        _maybe_sync_license_admin_identity()
        svc = get_admins_service()
        admin = svc.authenticate(username, password)
        if not admin:
            flash("بيانات الدخول غير صحيحة.", "error")
            return render_template("radius/login.html", username=username), 401
        # اختيار tenant — أولوية: tenants_for_admin → default
        store = TenantsStore.instance()
        if getattr(admin, "is_super_admin", False):
            tenants = store.list()
        else:
            tenants = store.tenants_for_admin(admin.id)
        if not tenants:
            # bootstrap: نعطيه عضوية في tenant الافتراضي
            from ..core.tenant import TenantMembership
            store.add_membership(TenantMembership(
                id=None, tenant_id=DEFAULT_TENANT_ID, admin_id=admin.id,
                role_id=admin.role_id, status="active",
            ))
            tenants = [store.get(DEFAULT_TENANT_ID)]
        set_current_admin(admin, tenant_id=tenants[0].id)
        flash(f"أهلًا {admin.full_name or admin.username}", "success")
        nxt = request.args.get("next") or url_for("radius.dashboard")
        return redirect(nxt)

    if session.get("admin_id"):
        return redirect(url_for("radius.dashboard"))
    return render_template("radius/login.html")


def _maybe_sync_license_admin_identity() -> None:
    from ..services.admin_panel_client import bridge_flag

    if not bridge_flag("HOBERADIUS_ADMIN_IDENTITY_SYNC_ON_LOGIN", "license_admin_bridge.identity_sync_on_login"):
        return
    try:
        from ..services.license_admin_identity_sync import LicenseAdminIdentitySyncService

        LicenseAdminIdentitySyncService().sync_once(tenant_id=DEFAULT_TENANT_ID)
    except Exception:
        # Login should keep using the last synced local hash if the panel is
        # temporarily unavailable.
        return


def auth_logout():
    clear_current_admin()
    flash("تم تسجيل الخروج.", "info")
    return redirect(url_for("radius.auth_login"))


def auth_switch_tenant():
    """تبديل الـ tenant الحالي (POST من dropdown الـ topbar)."""
    from ..auth.session_helpers import admin_tenants
    new_tid = request.form.get("tenant_id")
    if not new_tid:
        return redirect(request.referrer or url_for("radius.dashboard"))
    try:
        new_tid = int(new_tid)
    except ValueError:
        return redirect(request.referrer or url_for("radius.dashboard"))
    allowed = {t.id for t in admin_tenants()}
    if new_tid not in allowed:
        flash("لا تملك صلاحية على هذا الـ Tenant.", "error")
        return redirect(request.referrer or url_for("radius.dashboard"))
    session["tenant_id"] = new_tid
    t = TenantsStore.instance().get(new_tid)
    flash(f"تم التبديل إلى: {t.display_name or t.name}", "success")
    return redirect(request.referrer or url_for("radius.dashboard"))
