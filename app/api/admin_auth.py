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

from flask import Blueprint, request

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
    from flask import g
    token_id = getattr(g, "api_token_id", None)
    if not token_id:
        return fail("unauthorized",
                    "هذا الـ endpoint يتطلب admin token (من /api/admin/login)",
                    status=401)
    rec = next(
        (t for t in api_tokens_repo.list_tokens(getattr(g, "tenant_id", 1))
         if t["id"] == token_id),
        None,
    )
    if not rec or not rec.get("created_by"):
        return fail("unauthorized",
                    "الـ token غير مرتبط بأدمن", status=401)
    admin = admins_repo.get_admin(int(rec["created_by"]))
    if not admin:
        return fail("not_found", "admin not found", status=404)
    perms = list(admins_repo.admin_permissions(admin))
    return ok({
        "admin": _serialize_admin(admin),
        "tenant_id": getattr(g, "tenant_id", 1),
        "permissions": perms,
    })


def admin_logout():
    from flask import g
    token_id = getattr(g, "api_token_id", None)
    if token_id:
        api_tokens_repo.revoke_token(getattr(g, "tenant_id", 1), token_id)
    return ok({"logged_out": True})
