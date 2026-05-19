"""
Admins + Roles + Permissions endpoints.

All writes go through AdminsService → admins_repo, the same path used by
the web `/admin/radius/admins*` and `/admin/radius/roles*` forms — audit
records identical.

Permissions are not yet enforced on the API surface (tracked in
docs/SECURITY_HARDENING_PLAN.md item #4). This slice only exposes the
catalog + role assignments cleanly so Flutter can render the editor.

Password handling:
  - POST /admins requires `password`.
  - PATCH /admins/<id> accepts optional `password` (rotation).
  - Hash is computed inside admins_repo.update_admin / create_admin —
    plaintext never leaves the route function.
  - `password_hash` is never returned by `_serialize`.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

from ...radius.core.constants import ALL_PERMISSIONS
from ...radius.core.errors import RadiusError, RadiusValidationError
from ...radius.db.repos import admins_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def register(bp: Blueprint) -> None:
    # ── admins ──
    bp.add_url_rule("/admins", "admins_list",
                    require_api_token(admins_list), methods=["GET"])
    bp.add_url_rule("/admins", "admins_create",
                    require_api_token(admins_create), methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_get",
                    require_api_token(admins_get), methods=["GET"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_patch",
                    require_api_token(admins_patch), methods=["PATCH"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_delete",
                    require_api_token(admins_delete), methods=["DELETE"])
    # ── roles ──
    bp.add_url_rule("/roles", "roles_list",
                    require_api_token(roles_list), methods=["GET"])
    bp.add_url_rule("/roles", "roles_create",
                    require_api_token(roles_create), methods=["POST"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_get",
                    require_api_token(roles_get), methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_patch",
                    require_api_token(roles_patch), methods=["PATCH"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_delete",
                    require_api_token(roles_delete), methods=["DELETE"])
    # ── permissions catalog ──
    bp.add_url_rule("/permissions", "permissions_catalog",
                    require_api_token(permissions_catalog), methods=["GET"])


# ─────────────── serializers ───────────────

def _serialize_admin(a) -> dict:
    return {
        "id": a.id,
        "username": a.username,
        "full_name": a.full_name,
        "email": a.email,
        "mobile": a.mobile,
        "phone": a.phone,
        "role_id": a.role_id,
        "is_super_admin": a.is_super_admin,
        "enabled": a.enabled,
        "avatar_url": a.avatar_url,
        "tags": a.tags,
        "last_login_at": a.last_login_at.isoformat() + "Z" if a.last_login_at else None,
        "last_login_ip": a.last_login_ip,
        "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
        "updated_at": a.updated_at.isoformat() + "Z" if a.updated_at else None,
    }


def _serialize_role(r) -> dict:
    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "name": r.name,
        "display_name": r.display_name,
        "description": r.description,
        "permissions": list(r.permissions),
        "is_system": r.is_system,
        "color": r.color,
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
    }


# ─────────────── admins ───────────────

_ADMIN_STR_FIELDS = (
    "full_name", "email", "mobile", "phone",
    "avatar_url", "tags", "profile_notes",
)
_ADMIN_BOOL_FIELDS = ("enabled", "is_super_admin")


def _coerce_int(name: str, v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{name} must be integer")


def admins_list():
    items = admins_repo.list_admins()
    return ok({"items": [_serialize_admin(a) for a in items], "count": len(items)})


def admins_get(admin_id: int):
    a = admins_repo.get_admin(admin_id)
    if not a:
        return fail("not_found", f"admin {admin_id} غير موجود", status=404)
    return ok(_serialize_admin(a))


def admins_create():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username:
        return fail("validation_error", "username مطلوب", status=422)
    if not password:
        return fail("validation_error", "password مطلوب", status=422)
    # optional role_id
    try:
        role_id = _coerce_int("role_id", body.get("role_id"))
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        admin = admins_repo.create_admin(
            username=username,
            password=str(password),
            full_name=str(body.get("full_name") or "").strip(),
            email=str(body.get("email") or "").strip(),
            mobile=str(body.get("mobile") or "").strip(),
            role_id=role_id,
            is_super_admin=bool(body.get("is_super_admin")),
            enabled=bool(body.get("enabled", True)),
            phone=str(body.get("phone") or "").strip(),
            profile_notes=str(body.get("profile_notes") or ""),
            avatar_url=str(body.get("avatar_url") or "").strip(),
            tags=str(body.get("tags") or "").strip(),
        )
    except ValueError as e:
        return fail("conflict", str(e), status=409)
    # audit (same shape as web)
    _audit("create", "admin", str(admin.id), {"username": admin.username})
    return ok(_serialize_admin(admin), status=201)


def admins_patch(admin_id: int):
    existing = admins_repo.get_admin(admin_id)
    if not existing:
        return fail("not_found", f"admin {admin_id} غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    changes: dict = {}
    for k in _ADMIN_STR_FIELDS:
        if k in body:
            v = body[k]
            changes[k] = "" if v is None else str(v)
    for k in _ADMIN_BOOL_FIELDS:
        if k in body:
            changes[k] = bool(body[k])
    if "role_id" in body:
        try:
            changes["role_id"] = _coerce_int("role_id", body["role_id"])
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
    if "password" in body and (body["password"] or "").strip():
        changes["password"] = str(body["password"])
    admin = admins_repo.update_admin(admin_id, **changes)
    if not admin:
        return fail("not_found", "admin not found", status=404)
    _audit("update", "admin", str(admin_id), {"fields": list(changes.keys())})
    return ok(_serialize_admin(admin))


def admins_delete(admin_id: int):
    existing = admins_repo.get_admin(admin_id)
    if not existing:
        return fail("not_found", f"admin {admin_id} غير موجود", status=404)
    if existing.is_super_admin:
        return fail("forbidden",
                    "لا يمكن حذف super_admin عبر الـ API", status=403)
    admins_repo.delete_admin(admin_id)
    _audit("delete", "admin", str(admin_id), {"username": existing.username})
    return ok({"deleted": admin_id})


# ─────────────── roles ───────────────

def roles_list():
    items = admins_repo.list_roles()
    return ok({"items": [_serialize_role(r) for r in items], "count": len(items)})


def roles_get(role_id: int):
    r = admins_repo.get_role(role_id)
    if not r:
        return fail("not_found", f"role {role_id} غير موجود", status=404)
    return ok(_serialize_role(r))


def _validate_permissions(perms: Any) -> tuple[str, ...]:
    if perms is None:
        return ()
    if not isinstance(perms, (list, tuple)):
        raise RadiusValidationError("permissions must be a list of strings")
    out: list[str] = []
    valid = set(ALL_PERMISSIONS)
    bad: list[str] = []
    for p in perms:
        s = str(p)
        if s not in valid:
            bad.append(s)
        else:
            out.append(s)
    if bad:
        raise RadiusValidationError(
            f"unknown permission(s): {bad}. "
            f"See GET /api/v1/permissions for the catalog."
        )
    return tuple(dict.fromkeys(out))  # de-dup, preserve order


def roles_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return fail("validation_error", "name مطلوب", status=422)
    try:
        perms = _validate_permissions(body.get("permissions"))
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    try:
        role = admins_repo.create_role(
            name=name,
            display_name=str(body.get("display_name") or name).strip(),
            description=str(body.get("description") or "").strip(),
            permissions=perms,
            color=str(body.get("color") or "#2BAACC").strip(),
        )
    except ValueError as e:
        return fail("conflict", str(e), status=409)
    _audit("create", "role", str(role.id),
           {"name": role.name, "perms_count": len(perms)})
    return ok(_serialize_role(role), status=201)


def roles_patch(role_id: int):
    existing = admins_repo.get_role(role_id)
    if not existing:
        return fail("not_found", f"role {role_id} غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    changes: dict = {}
    for k in ("display_name", "description", "color"):
        if k in body:
            v = body[k]
            changes[k] = "" if v is None else str(v)
    if "permissions" in body:
        try:
            changes["permissions"] = _validate_permissions(body["permissions"])
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
    role = admins_repo.update_role(role_id, **changes)
    if not role:
        return fail("not_found", "role not found", status=404)
    _audit("update", "role", str(role_id), {"fields": list(changes.keys())})
    return ok(_serialize_role(role))


def roles_delete(role_id: int):
    existing = admins_repo.get_role(role_id)
    if not existing:
        return fail("not_found", f"role {role_id} غير موجود", status=404)
    if existing.is_system:
        return fail("forbidden",
                    "لا يمكن حذف دور نظامي", status=403)
    admins_repo.delete_role(role_id)
    _audit("delete", "role", str(role_id), {"name": existing.name})
    return ok({"deleted": role_id})


# ─────────────── permissions catalog ───────────────

# Group permissions by their dotted-prefix so the Flutter editor can render
# them in logical sections.
_PERM_GROUPS_AR = {
    "dashboard": "اللوحة",
    "users": "المشتركون",
    "cards": "الكروت",
    "plans": "الباقات",
    "nas": "أجهزة الشبكة",
    "sessions": "الجلسات",
    "admins": "المدراء",
    "settings": "الإعدادات",
    "audit": "سجل التدقيق",
    "api": "الـ API",
}


def permissions_catalog():
    groups: dict[str, list[str]] = {}
    for p in ALL_PERMISSIONS:
        prefix = p.split(".", 1)[0]
        groups.setdefault(prefix, []).append(p)
    return ok({
        "items": list(ALL_PERMISSIONS),
        "groups": [
            {
                "key": k,
                "label": _PERM_GROUPS_AR.get(k, k),
                "permissions": v,
            }
            for k, v in groups.items()
        ],
        "count": len(ALL_PERMISSIONS),
    })


# ─────────────── audit helper ───────────────

def _audit(action: str, target_type: str, target_id: str, payload: dict) -> None:
    """Write to audit log via the same repo the services use, so admin-edit
    actions taken over the API are auditable identically to web actions."""
    try:
        from ...radius.db.repos import audit_repo
        audit_repo.record(
            tenant_id=_tid(),
            actor=_actor(),
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )
    except Exception:  # noqa: BLE001
        pass  # audit is best-effort, never block the request
