"""
Admin JSON login — used by mobile/desktop Flutter clients.

POST /api/admin/login
    body: {"username": "...", "password": "..."}
    →  { ok: true, data: { token, admin, tenant_id, permissions, expires_at } }

The endpoint authenticates against the same AdminsService.authenticate() used
by the web /admin/radius/login form, then mints a fresh hashed API token via
api_tokens_repo and returns the plaintext exactly once. The token is then
accepted by all /api/v1/* endpoints via the existing Bearer-auth middleware.

Subsequent calls /api/admin/me + /api/admin/logout consume the issued token.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

from flask import Blueprint, g, request

from ..radius.db.repos import admins_repo, api_tokens_repo
from ..radius.stores.tenants_store import TenantsStore
from .auth import require_api_token
from .responses import fail, ok


# Default login-token lifetime. Override per-deploy with
# HOBERADIUS_TOKEN_TTL_HOURS. 0 or negative ⇒ no expiry (legacy behaviour).
_DEFAULT_TTL_HOURS = 24 * 7  # 7 days


def _token_ttl_hours() -> int:
    raw = (os.environ.get("HOBERADIUS_TOKEN_TTL_HOURS") or "").strip()
    if not raw:
        return _DEFAULT_TTL_HOURS
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_TTL_HOURS


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/admin/login", "admin_login",
                    admin_login, methods=["POST"])
    bp.add_url_rule("/admin/me", "admin_me",
                    require_api_token(admin_me), methods=["GET"])
    bp.add_url_rule("/admin/password", "admin_password",
                    require_api_token(admin_password), methods=["POST"])
    bp.add_url_rule("/admin/logout", "admin_logout",
                    require_api_token(admin_logout), methods=["POST"])


def _serialize_admin(a) -> dict:
    return {
        "id": a.id,
        "username": a.username,
        "full_name": a.full_name,
        "email": a.email,
        "mobile": a.mobile,
        "role_id": a.role_id,
        "is_super_admin": a.is_super_admin,
        "enabled": a.enabled,
        "last_login_at": a.last_login_at.isoformat() + "Z" if a.last_login_at else None,
        "last_login_ip": a.last_login_ip,
        "phone": a.phone,
        "avatar_url": a.avatar_url,
    }


def _pick_tenant(admin) -> Optional[int]:
    """Same precedence as the web login: super_admin → all; else memberships;
    else default tenant bootstrap on first login."""
    store = TenantsStore.instance()
    if admin.is_super_admin:
        tenants = store.list()
    else:
        tenants = store.tenants_for_admin(admin.id)
    if tenants:
        return tenants[0].id
    # bootstrap default — same as web flow
    from ..radius.core.tenant import DEFAULT_TENANT_ID, TenantMembership
    store.add_membership(TenantMembership(
        id=None, tenant_id=DEFAULT_TENANT_ID, admin_id=admin.id,
        role_id=admin.role_id, status="active",
    ))
    return DEFAULT_TENANT_ID


def admin_login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return fail("validation_error",
                    "username + password مطلوبان", status=422)

    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    admin = admins_repo.authenticate(username, password, ip=ip)
    if not admin:
        return fail("unauthorized",
                    "بيانات الدخول غير صحيحة", status=401)

    tenant_id = _pick_tenant(admin)
    if tenant_id is None:
        return fail("forbidden",
                    "لا تملك صلاحية على أي tenant", status=403)

    ttl_hours = _token_ttl_hours()
    expires_at = (datetime.utcnow() + timedelta(hours=ttl_hours)) if ttl_hours > 0 else None
    record, plain = api_tokens_repo.create_token(
        tenant_id=tenant_id,
        name=f"login:{admin.username}:{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}",
        scopes=["admin:full"],
        created_by=admin.id,
        expires_at=expires_at,
    )

    perms = list(admins_repo.admin_permissions(admin))

    return ok({
        "token": plain,
        "token_id": record["id"],
        "admin": _serialize_admin(admin),
        "tenant_id": tenant_id,
        "permissions": perms,
        "expires_at": record.get("expires_at"),
    })


def admin_me():
    """Returns the admin associated with the calling token. Identifies the
    admin via api_tokens.created_by (set by /admin/login)."""
    admin = _current_admin_from_token()
    if admin is None:
        return fail("unauthorized",
                    "هذا المسار يتطلب تسجيل دخول إداري من التطبيق.",
                    status=401)
    perms = list(admins_repo.admin_permissions(admin))
    return ok({
        "admin": _serialize_admin(admin),
        "tenant_id": getattr(g, "tenant_id", 1),
        "permissions": perms,
    })


def admin_password():
    admin = _current_admin_from_token()
    if admin is None:
        return fail("unauthorized",
                    "هذا المسار يتطلب تسجيل دخول إداري من التطبيق.",
                    status=401)

    body = request.get_json(silent=True) or {}
    current_password = str(body.get("current_password") or "")
    new_password = str(body.get("new_password") or "")
    confirm_password = str(body.get("confirm_password") or "")

    if not current_password or not new_password or not confirm_password:
        return fail(
            "validation_error",
            "كلمة المرور الحالية والجديدة وتأكيدها مطلوبة.",
            status=422,
        )
    if not admins_repo.verify_password(current_password, admin.password_hash):
        return fail(
            "invalid_current_password",
            "كلمة المرور الحالية غير صحيحة.",
            status=422,
        )
    if len(new_password) < 8:
        return fail(
            "validation_error",
            "كلمة المرور الجديدة يجب أن تكون 8 أحرف على الأقل.",
            status=422,
        )
    if new_password != confirm_password:
        return fail(
            "validation_error",
            "تأكيد كلمة المرور غير مطابق.",
            status=422,
        )

    if admin.managed_by_license_admin:
        from ..radius.services.license_admin_identity_sync import LicenseAdminIdentitySyncService

        result = LicenseAdminIdentitySyncService().change_password_from_runtime(
            admin=admin,
            new_password=new_password,
            tenant_id=int(getattr(g, "tenant_id", 1) or 1),
        )
        if not result.get("ok"):
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            return fail(
                str(error.get("code") or result.get("status") or "license_admin_password_change_failed"),
                str(error.get("message") or "تعذر تحديث كلمة المرور عبر لوحة التراخيص."),
                status=502,
            )
        return ok({
            "updated": True,
            "source": "license_admin",
            "message": "تم تحديث كلمة المرور من لوحة التراخيص.",
        })

    admins_repo.update_admin(int(admin.id or 0), password=new_password)
    return ok({
        "updated": True,
        "source": "local",
        "message": "تم تحديث كلمة المرور المحلية.",
    })


def admin_logout():
    token_id = getattr(g, "api_token_id", None)
    if token_id:
        api_tokens_repo.revoke_token(getattr(g, "tenant_id", 1), token_id)
    return ok({"logged_out": True})


def _current_admin_from_token():
    token_id = getattr(g, "api_token_id", None)
    if not token_id:
        return None
    rec = next(
        (t for t in api_tokens_repo.list_tokens(getattr(g, "tenant_id", 1))
         if t["id"] == token_id),
        None,
    )
    if not rec or not rec.get("created_by"):
        return None
    return admins_repo.get_admin(int(rec["created_by"]))
