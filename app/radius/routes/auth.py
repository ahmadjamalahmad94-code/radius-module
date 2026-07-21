"""routes الـ auth: login/logout."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..auth.session_helpers import clear_current_admin, set_current_admin
from ..core.tenant import DEFAULT_TENANT_ID
from ..services.admins import get_admins_service
from ..services.login_events import record_login_event
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
            record_login_event(actor_type="admin", username=username, success=False,
                               reason="bad_password", tenant_id=DEFAULT_TENANT_ID,
                               attempted_password=password)
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
        # MT36 — الدخول من الجذر لمالك المنصّة وحده. مديرُ شبكةٍ يُصادَق
        # بنجاح ثمّ يُمنع من فتح جلسة هنا، ويُدلّ على باب شبكته. الفحص بعد
        # المصادقة لا قبلها لأنّنا لا نعرف هويّته إلّا بعدها؛ ولا تُفتح
        # الجلسة إطلاقًا في هذه الحالة (لا set_current_admin).
        from ..core.hosting_mode import open_hosting
        if (open_hosting()
                and not request.environ.get("hoberadius.tenant_slug")
                and not getattr(admin, "is_super_admin", False)):
            slug = next((t.slug for t in tenants if getattr(t, "slug", "")), "")
            record_login_event(actor_type="admin", username=admin.username,
                               success=False, reason="root_login_not_platform_owner",
                               tenant_id=tenants[0].id)
            if slug:
                flash("هذا الرابط لإدارة المنصّة فقط. ادخل من رابط شبكتك: "
                      f"{request.host}/{slug}", "error")
            else:
                flash("هذا الرابط لإدارة المنصّة فقط. استخدم رابط شبكتك "
                      "الخاصّ للدخول.", "error")
            return render_template("radius/login.html", username=username), 403

        set_current_admin(admin, tenant_id=tenants[0].id)
        record_login_event(actor_type="admin", username=admin.username, success=True,
                           actor_id=admin.id, tenant_id=tenants[0].id)
        flash(f"أهلًا {admin.full_name or admin.username}", "success")
        # MT18 — المزوّد/المالك يهبط على لوحة إدارة الاستضافة؛ مشغّل الجهة على
        # لوحة الريديوس التشغيلية لجهته.
        nxt = request.args.get("next") or url_for("radius.dashboard")
        if not request.args.get("next") and session.get("is_super_admin"):
            nxt = url_for("radius.provider_home")
        return redirect(nxt)

    if session.get("admin_id"):
        # MT36 — على الجذر في وضع الاستضافة: صاحب جلسةٍ ليس مالك المنصّة
        # لا يُرمى في لوحة شبكته. زرّ «دخول» في صفحة الهبوط بابُ المالك،
        # فنُبقيه على صفحة الدخول ونُخبره بأنّ عليه الخروج أوّلًا — وإلّا
        # لَما استطاع أحدٌ تبديل حسابه من الجذر أبدًا.
        from ..core.hosting_mode import open_hosting
        if (open_hosting()
                and not request.environ.get("hoberadius.tenant_slug")
                and not session.get("is_super_admin")):
            slug = _slug_of_current_admin()
            here = f" شبكتك: {request.host}/{slug}" if slug else ""
            flash("أنت مسجَّل الدخول بحساب "
                  f"«{session.get('admin_user') or session.get('admin_name') or ''}» "
                  "وهو ليس حساب مالك المنصّة. اخرج أوّلًا للدخول بحساب آخر."
                  + here, "info")
            return render_template("radius/login.html"), 200
        return redirect(url_for(
            "radius.provider_home" if session.get("is_super_admin") else "radius.dashboard"))
    return render_template("radius/login.html")


def _slug_of_current_admin() -> str:
    """اسم شبكة صاحب الجلسة (فارغ إن تعذّر) — لا يرفع أبدًا."""
    try:
        rows = TenantsStore.instance().tenants_for_admin(int(session.get("admin_id") or 0))
        return next((t.slug for t in rows if getattr(t, "slug", "")), "")
    except Exception:  # noqa: BLE001 — نصٌّ إرشاديّ لا يجوز أن يُسقط الصفحة
        return ""


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
