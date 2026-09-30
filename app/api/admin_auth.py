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

from ..radius.auth import login_throttle
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


def _is_super_role(a) -> bool:
    try:
        return bool(a.role_id and admins_repo.role_is_super(admins_repo.get_role(int(a.role_id))))
    except Exception:  # noqa: BLE001
        return False


def _effective_permissions(admin) -> list:
    """RBAC keys the app should honour: ALL for owner/co-owner (they bypass RBAC),
    else the role's keys (the «مدير عام» role = all non-owner keys)."""
    from ..radius.core.constants import ALL_PERMISSIONS
    if admins_repo.is_primary_owner(admin.id):
        return list(ALL_PERMISSIONS)
    return list(admins_repo.admin_permissions(admin))


def _grants_summary(admin, tenant_id: int) -> dict:
    """App contract (permmodel): effective fine-grained grants so the app hides
    what the server would refuse — actions (ACTION_REGISTRY key → bool),
    section states (open/locked/hidden) and «عرض كل المشتركين»."""
    owner = bool(admins_repo.is_primary_owner(admin.id))
    out = {"actions": {}, "sections": {}, "view_all_subscribers": owner}
    try:
        from ..radius.services import manager_grants as _mg
        from ..radius.services.subscriber_scope import can_view_all_subscribers
        for key in _mg.ACTION_REGISTRY:
            out["actions"][key] = True if owner else bool(
                _mg.action_permitted(admin.id, key, tenant_id=tenant_id))
        perms = _effective_permissions(admin)
        for sec in _mg.MANAGER_SECTION_REGISTRY:
            if owner:
                out["sections"][sec] = "open"
            elif _mg.effective_section_hidden(admin.id, sec, tenant_id=tenant_id, perms=perms):
                out["sections"][sec] = "hidden"
            else:
                out["sections"][sec] = _mg.section_state(admin.id, sec, tenant_id=tenant_id)
        out["view_all_subscribers"] = owner or bool(
            can_view_all_subscribers(admin.id, tenant_id=tenant_id))
    except Exception:  # noqa: BLE001 — never break login/me over the summary
        pass
    # fix3 (F02 L1): per-field grants so the app renders locked inputs read-only
    # and never posts them. entity → {controlled, editable[], locked[],
    # locked_attrs[]}; ``controlled: false`` = every field editable.
    fields: dict = {}
    try:
        from ..radius.services import manager_grants as _mg
        for entity, defs in _mg.FIELD_REGISTRY.items():
            granted = None if owner else _mg.field_grants(admin.id, entity, tenant_id=tenant_id)
            keys = [d["key"] for d in defs]
            if granted is None:
                fields[entity] = {"controlled": False, "editable": keys, "locked": [],
                                  "locked_attrs": []}
            else:
                locked = [k for k in keys if k not in granted]
                fields[entity] = {
                    "controlled": True,
                    "editable": [k for k in keys if k in granted],
                    "locked": locked,
                    "locked_attrs": sorted({a for d in defs if d["key"] in locked
                                            for a in d["attrs"]}),
                }
    except Exception:  # noqa: BLE001
        fields = {}
    out["fields"] = fields
    # «الأدوات»: tool key → may this admin run it (the API guard's own decision;
    # set-speeds / test-auth / maintenance / general adjustments are owner-only).
    try:
        from .permission_guard import tool_permissions
        out["tools"] = tool_permissions(admin, tenant_id=tenant_id, owner=owner)
    except Exception:  # noqa: BLE001
        out["tools"] = {}
    return out


def _distributor_of(a) -> Optional[dict]:
    """The distributor this admin account IS (``distributors.login_admin_id``),
    or None. Owner / co-owner accounts are never treated as a distributor login
    (same rule as ``access_control.current_distributor``)."""
    try:
        if admins_repo.is_primary_owner(a.id):
            return None
        from ..radius.db.repos import operations_repo
        tid = int(getattr(g, "tenant_id", None) or 1)
        return operations_repo.get_distributor_by_admin(tid, int(a.id))
    except Exception:  # noqa: BLE001 — never break login/me over this
        return None


def _serialize_admin(a) -> dict:
    return {
        "id": a.id,
        "username": a.username,
        "full_name": a.full_name,
        "email": a.email,
        "mobile": a.mobile,
        "role_id": a.role_id,
        # «مدير عام / سوبر يوزر» = دور super_admin (كل الصلاحيات غير المقصورة
        # على المالك) — من الدور الفعليّ.
        "is_super_admin": bool(_is_super_role(a)),
        # «شريك/مالك»: كل صلاحيات المالك.
        "is_co_owner": bool(getattr(a, "is_co_owner", False)),
        # مالكٌ (أصليّ أو شريك) = يتجاوز كل الصلاحيات.
        "is_owner": bool(admins_repo.is_primary_owner(a.id)),
        "is_original_owner": bool(admins_repo.is_original_owner(a.id)),
        # A distributor's own app login (D11 ``login_admin_id``): the app shows
        # the distributor screens and scopes to its assigned batches.
        **_distributor_fields(a),
        "enabled": a.enabled,
        "last_login_at": a.last_login_at.isoformat() + "Z" if a.last_login_at else None,
        "last_login_ip": a.last_login_ip,
        "phone": a.phone,
        "avatar_url": a.avatar_url,
    }


def _distributor_fields(a) -> dict:
    dist = _distributor_of(a)
    return {
        "is_distributor": dist is not None,
        "distributor_id": int(dist["id"]) if dist else None,
        "distributor_name": (dist.get("name") or None) if dist else None,
    }


def _pick_tenant(admin) -> Optional[int]:
    """Same precedence as the web login: super_admin → all; else memberships;
    else default tenant bootstrap on first login."""
    store = TenantsStore.instance()
    if admin.is_super_admin or admins_repo.is_primary_owner(admin.id):
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
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    username = str(body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        return fail("validation_error",
                    "username + password مطلوبان", status=422)

    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    # brute-force brake: N failures / window for this username from this
    # address → 429 (even with the right password) until the window passes.
    wait = login_throttle.retry_after("admin_login", username)
    if wait:
        return fail("too_many_attempts", login_throttle.locked_message(wait),
                    status=429, details={"retry_after_seconds": wait})
    admin = admins_repo.authenticate(username, password, ip=ip)
    if not admin:
        login_throttle.register_failure("admin_login", username)
        return fail("unauthorized",
                    "بيانات الدخول غير صحيحة", status=401)
    login_throttle.register_success("admin_login", username)

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

    perms = _effective_permissions(admin)

    return ok({
        "token": plain,
        "token_id": record["id"],
        "admin": _serialize_admin(admin),
        "tenant_id": tenant_id,
        "permissions": perms,
        "grants": _grants_summary(admin, tenant_id),
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
    perms = _effective_permissions(admin)
    from ..radius.core.system_config import effective_system_settings
    try:
        system = effective_system_settings()
    except Exception:  # noqa: BLE001 — never break the session restore
        system = None
    return ok({
        "admin": _serialize_admin(admin),
        "tenant_id": getattr(g, "tenant_id", 1),
        "permissions": perms,
        "grants": _grants_summary(admin, int(getattr(g, "tenant_id", 1) or 1)),
        # عملة النظام الفعليّة + المنطقة الزمنية (نفس default_currency()).
        "system": system,
    })


def admin_password():
    admin = _current_admin_from_token()
    if admin is None:
        return fail("unauthorized",
                    "هذا المسار يتطلب تسجيل دخول إداري من التطبيق.",
                    status=401)

    body = request.get_json(silent=True)
    if body is not None and not isinstance(body, dict):
        # [1] / "x" كان يُسقط .get() = HTML 500.
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    body = body or {}
    current_password = str(body.get("current_password") or "")
    new_password = str(body.get("new_password") or "")
    confirm_password = str(body.get("confirm_password") or "")

    if not current_password or not new_password or not confirm_password:
        return fail(
            "validation_error",
            "كلمة المرور الحالية والجديدة وتأكيدها مطلوبة.",
            status=422,
        )
    _pw_key = f"id:{int(admin.id or 0)}"
    wait = login_throttle.retry_after("admin_password", _pw_key)
    if wait:
        return fail("too_many_attempts", login_throttle.locked_message(wait),
                    status=429, details={"retry_after_seconds": wait})
    if not admins_repo.verify_password(current_password, admin.password_hash):
        login_throttle.register_failure("admin_password", _pw_key)
        return fail(
            "invalid_current_password",
            "كلمة المرور الحالية غير صحيحة.",
            status=422,
        )
    login_throttle.register_success("admin_password", _pw_key)
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
    if new_password == current_password:
        # re-test R08 NEW-3: a "change" to the same password was accepted
        # (and revoked the other sessions for nothing).
        return fail(
            "validation_error",
            "كلمة المرور الجديدة يجب أن تختلف عن الحالية.",
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
        # update_admin() is bypassed on this path — revoke the other app
        # sessions here (the local path does it inside update_admin).
        try:
            api_tokens_repo.revoke_admin_tokens(
                int(admin.id or 0), except_id=getattr(g, "api_token_id", None))
        except Exception:  # noqa: BLE001 — never fail a done password change
            pass
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
