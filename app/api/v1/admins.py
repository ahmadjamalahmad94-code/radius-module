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

import functools
from typing import Any

from flask import Blueprint, g, request

from ...radius.core.constants import ALL_PERMISSIONS, DEPRECATED_PERMISSIONS
from ...radius.core.errors import RadiusError, RadiusValidationError
from ...radius.db.repos import admins_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _notify_panel_of_admin_change(*, deleted_admin_id: int | None = None) -> None:
    """Fire an admins-report to the licensing panel right after a REST admin
    write (create/update/delete). Mirrors the web route's post-write hook — the
    REST path mutates admins_repo directly (bypassing AdminsService), so without
    this it would only propagate on the next periodic / request_admin_report
    cycle. Best-effort: swallows every error so a bridge outage never breaks the
    API write. ``deleted_admin_id`` sends a differential tombstone; otherwise a
    full-snapshot report."""
    try:
        from ...radius.services.license_admin_inventory_report import (
            report_admins_best_effort,
        )
        report_admins_best_effort(deleted_admin_id=deleted_admin_id)
    except Exception:  # noqa: BLE001
        pass


# ─────────────── RBAC gate (SEC H1) ───────────────
#
# Managing admin accounts and roles = minting/deleting super admins and
# rewriting permission sets. Before this gate the whole surface was reachable
# by ANY authenticated API principal — including a plain (non-super) admin
# using HTTP Basic, because auth.py grants Basic callers the "admin:full"
# scope regardless of their real super status. So the scope alone is NOT a
# trustworthy signal for a bound principal; we resolve the actual admin and
# require the WEB owner predicate (owner / co-owner — ``is_owner_like``) or,
# for the «مدير عام» role, the RBAC key of the operation (``admins.*``).
# p01/D13: the bare ``is_super_admin`` flag used to be enough here, so a
# flag-holder renamed the owner, reset another admin's password and logged in
# as him. The flag is no longer owner-level.
def _actor_admin():
    aid = int(getattr(g, "admin_id", 0) or 0)
    if aid <= 0:
        return None
    try:
        return admins_repo.get_admin(aid)
    except Exception:  # noqa: BLE001
        return None


def _actor_id() -> int | None:
    """The admin behind the token, or None for an unbound master credential."""
    aid = int(getattr(g, "admin_id", 0) or 0)
    return aid if aid > 0 else None


def _actor_perms() -> frozenset:
    aid = _actor_id()
    if aid is None:
        return frozenset()
    a = admins_repo.get_admin(aid)
    return frozenset(admins_repo.admin_permissions(a)) if a and a.enabled else frozenset()


def _can_manage_admins(perm: str = "admins.view") -> bool:
    """D12/D13 — same predicate as the web panel: owner or co-owner
    (``is_owner_like``), or a manager holding the RBAC key for this operation
    (``admins.view/create/edit/delete`` — the «مدير عام» role has them all).
    The raw ``is_super_admin`` column no longer grants anything by itself."""
    aid = _actor_id()
    if aid is None:
        # Unbound master credential (env HOBERADIUS_API_TOKENS / dev fallback)
        # — owner-level by construction. Mirror access_control.is_full_access.
        scopes = set(getattr(g, "api_token_scopes", []) or [])
        return "admin:full" in scopes or "*" in scopes
    try:
        from ...radius.auth.owner import is_owner_like
        if is_owner_like(aid):
            return True
        return perm in _actor_perms()
    except Exception:  # noqa: BLE001 — never grant on a lookup error
        return False


def _owner_target_protected(target) -> bool:
    """An owner / co-owner account is protected from every non-owner actor,
    and the ORIGINAL owner from everyone but himself (co-owners included):
    no rename, no password reset, no demotion, no disable, no delete.
    Unbound master credentials are owner-level."""
    if int(getattr(g, "admin_id", 0) or 0) <= 0:
        return False
    from ...radius.auth.owner import can_modify_admin
    return not can_modify_admin(int(getattr(g, "admin_id", 0)), target)


_OWNER_PROTECTED_AR = "لا يمكن تعديل أو حذف حساب المالك إلا من قِبل المالك."


def _require_manage(view, perm: str = "admins.view"):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not _can_manage_admins(perm):
            return fail(
                "forbidden",
                f"إدارة حسابات المدراء والأدوار تتطلّب صلاحية ({perm}) أو صلاحيات المالك.",
                status=403, details={"permission": perm},
            )
        return view(*a, **kw)
    return wrapped


def _owner_guard_fail(exc):
    return fail("forbidden", str(exc), status=403, details={"reason": "owner_protected"})


def register(bp: Blueprint) -> None:
    # ── admins ── (SEC H1 — every admin-account WRITE needs the owner / a
    # co-owner, or the matching ``admins.*`` key («مدير عام»). Reads
    # follow the web «المدراء» page: the central API guard requires
    # ``admins.view`` (permission_guard.API_PERMISSIONS), owner bypasses.)
    bp.add_url_rule("/admins", "admins_list",
                    require_api_token(admins_list), methods=["GET"])
    bp.add_url_rule("/admins", "admins_create",
                    require_api_token(_require_manage(admins_create, "admins.create")), methods=["POST"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_get",
                    require_api_token(admins_get), methods=["GET"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_patch",
                    require_api_token(_require_manage(admins_patch, "admins.edit")), methods=["PATCH"])
    bp.add_url_rule("/admins/<int:admin_id>", "admins_delete",
                    require_api_token(_require_manage(admins_delete, "admins.delete")), methods=["DELETE"])
    # ── roles ── (mutations grant/rewrite permission sets → super-only;
    # list/get stay readable so the Flutter role editor can render the catalog.)
    bp.add_url_rule("/roles", "roles_list",
                    require_api_token(roles_list), methods=["GET"])
    bp.add_url_rule("/roles", "roles_create",
                    require_api_token(_require_manage(roles_create, "admins.edit")), methods=["POST"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_get",
                    require_api_token(roles_get), methods=["GET"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_patch",
                    require_api_token(_require_manage(roles_patch, "admins.edit")), methods=["PATCH"])
    bp.add_url_rule("/roles/<int:role_id>", "roles_delete",
                    require_api_token(_require_manage(roles_delete, "admins.delete")), methods=["DELETE"])
    # ── permissions catalog ──
    bp.add_url_rule("/permissions", "permissions_catalog",
                    require_api_token(permissions_catalog), methods=["GET"])


# ─────────────── serializers ───────────────

def _is_super_role(a) -> bool:
    try:
        return bool(a.role_id and admins_repo.role_is_super(admins_repo.get_role(int(a.role_id))))
    except Exception:  # noqa: BLE001
        return False


def _serialize_admin(a) -> dict:
    return {
        "id": a.id,
        "username": a.username,
        "full_name": a.full_name,
        "email": a.email,
        "mobile": a.mobile,
        "phone": a.phone,
        "role_id": a.role_id,
        # «مدير عام / سوبر يوزر» = الدور super_admin (كل الصلاحيات غير المقصورة
        # على المالك). يعكس الدور الفعليّ لا عمودًا منفصلًا.
        "is_super_admin": bool(_is_super_role(a)),
        # «شريك/مالك» (co-owner) — كل صلاحيات المالك. يضبطه المالك/الشريك فقط.
        "is_co_owner": bool(getattr(a, "is_co_owner", False)),
        # مالكٌ أصليّ (محميّ) أو شريك.
        "is_owner": bool(admins_repo.is_primary_owner(a.id)),
        "is_original_owner": bool(admins_repo.is_original_owner(a.id)),
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
_ADMIN_BOOL_FIELDS = ("enabled",)


def _super_role_id():
    from ...radius.core.constants import ROLE_SUPER_ADMIN
    r = admins_repo.get_role_by_name(ROLE_SUPER_ADMIN)
    return r.id if r else None


def _coerce_int(name: str, v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"قيمة {name} يجب أن تكون رقمًا صحيحًا.")


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
    # optional role_id — omitted ⇒ the least-privileged role (viewer), never
    # super_admin (create_admin's default); an unknown id is refused.
    try:
        role_id = _coerce_int("role_id", body.get("role_id"))
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    if role_id is not None and admins_repo.get_role(role_id) is None:
        return fail("validation_error", "الدور المحدد غير موجود.", status=422)
    # «مدير عام» = إسناد دور super_admin (المالك/الشريك فقط).
    want_super = bool(body.get("is_super_admin"))
    want_co = bool(body.get("is_co_owner"))
    if want_super:
        role_id = _super_role_id()
    if role_id is None:
        role_id = admins_repo.least_privileged_role_id()
    from ...radius.auth.owner import OwnerGuardError, is_owner_like, assert_role_within_actor
    actor = _actor_id()
    if (want_super or want_co) and actor is not None and not is_owner_like(actor):
        return fail("forbidden",
                    "منح صلاحيات المالك (شريك) أو «مدير عام» مقصورٌ على المالك أو الشريك.",
                    status=403, details={"reason": "owner_only"})
    try:
        r = admins_repo.get_role(int(role_id)) if role_id else None
        assert_role_within_actor(actor, admins_repo.admin_permissions(
            type("A", (), {"role_id": role_id})()) if r else ())
    except OwnerGuardError as exc:
        return _owner_guard_fail(exc)
    try:
        admin = admins_repo.create_admin(
            username=username,
            password=str(password),
            full_name=str(body.get("full_name") or "").strip(),
            email=str(body.get("email") or "").strip(),
            mobile=str(body.get("mobile") or "").strip(),
            role_id=role_id,
            is_super_admin=want_super,
            enabled=bool(body.get("enabled", True)),
            phone=str(body.get("phone") or "").strip(),
            profile_notes=str(body.get("profile_notes") or ""),
            avatar_url=str(body.get("avatar_url") or "").strip(),
            tags=str(body.get("tags") or "").strip(),
        )
    except ValueError as e:
        return fail("conflict", str(e), status=409)
    if want_co:
        admins_repo.set_co_owner(admin.id, True)
        admin = admins_repo.get_admin(admin.id)
    # audit (same shape as web)
    _audit("create", "admin", str(admin.id), {"username": admin.username})
    _notify_panel_of_admin_change()
    return ok(_serialize_admin(admin), status=201)


def admins_patch(admin_id: int):
    existing = admins_repo.get_admin(admin_id)
    if not existing:
        return fail("not_found", f"admin {admin_id} غير موجود", status=404)
    if _owner_target_protected(existing):
        return fail("forbidden", _OWNER_PROTECTED_AR, status=403)
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return fail("validation_error", "جسم الطلب يجب أن يكون كائن JSON.", status=422)
    changes: dict = {}
    for k in _ADMIN_STR_FIELDS:
        if k in body:
            v = body[k]
            changes[k] = "" if v is None else str(v)
    for k in _ADMIN_BOOL_FIELDS:
        if k in body:
            changes[k] = bool(body[k])
    if "role_id" in body:
        # re-test R08 NEW-2: 9999 / 0 hit the FK → 500, and null / "" nulled
        # the role silently (0 permissions). Same rule as create: the role
        # must exist; clearing it is refused (pick a role instead).
        try:
            role_id = _coerce_int("role_id", body["role_id"])
        except RadiusValidationError as e:
            return fail("validation_error", e.message, status=422)
        # «مدير عام» is the super_admin ROLE now (permmodel D12): an empty
        # role is only acceptable when the same request assigns that role.
        _super = bool(body.get("is_super_admin"))
        if role_id is None and not _super:
            return fail("validation_error",
                        "الدور مطلوب — اختر دورًا موجودًا بدل إفراغه.", status=422)
        if role_id is not None and admins_repo.get_role(role_id) is None:
            return fail("validation_error", "الدور المحدد غير موجود.", status=422)
        changes["role_id"] = role_id
    if "password" in body and (body["password"] or "").strip():
        changes["password"] = str(body["password"])
    # «مدير عام» = دور super_admin؛ إطفاؤه يعيد الدور المعطى أو الأقلّ صلاحيةً.
    super_change = "is_super_admin" in body and bool(body["is_super_admin"]) != _is_super_role(existing)
    if super_change:
        if bool(body["is_super_admin"]):
            changes["role_id"] = _super_role_id()
            changes["is_super_admin"] = True
        else:
            if not changes.get("role_id") or changes.get("role_id") == _super_role_id():
                changes["role_id"] = admins_repo.least_privileged_role_id()
            changes["is_super_admin"] = False
    co_change = "is_co_owner" in body and bool(body["is_co_owner"]) != bool(
        getattr(existing, "is_co_owner", False))
    from ...radius.auth.owner import OwnerGuardError, assert_can_modify_admin
    try:
        assert_can_modify_admin(
            _actor_id(), admin_id,
            new_role_id=changes.get("role_id") if "role_id" in changes else None,
            co_owner_change=co_change, super_change=super_change)
    except OwnerGuardError as exc:
        return _owner_guard_fail(exc)
    if co_change and not bool(body["is_co_owner"]) and admins_repo.is_original_owner(admin_id):
        return fail("forbidden", "المالك الأصليّ ليس «شريكًا» يُسحَب — حسابه محميّ.",
                    status=403, details={"reason": "owner_protected"})
    try:
        if co_change:
            admins_repo.set_co_owner(admin_id, bool(body["is_co_owner"]))
        admin = admins_repo.update_admin(admin_id, **changes)
    except ValueError as exc:
        return fail("password_managed_by_license_admin", str(exc), status=409)
    if not admin:
        return fail("not_found", "الحساب الإداري غير موجود.", status=404)
    _audit("update", "admin", str(admin_id), {"fields": list(changes.keys())})
    _notify_panel_of_admin_change()
    return ok(_serialize_admin(admin))


def admins_delete(admin_id: int):
    existing = admins_repo.get_admin(admin_id)
    if not existing:
        return fail("not_found", f"admin {admin_id} غير موجود", status=404)
    from ...radius.auth.owner import OwnerGuardError, assert_can_modify_admin
    try:
        assert_can_modify_admin(_actor_id(), admin_id, deleting=True)
    except OwnerGuardError as exc:
        return _owner_guard_fail(exc)
    if _actor_id() is not None and int(_actor_id()) == int(admin_id):
        return fail("forbidden", "لا يمكنك حذف حسابك أنت.", status=403)
    admins_repo.delete_admin(admin_id)
    _audit("archive", "admin", str(admin_id), {"username": existing.username})
    _notify_panel_of_admin_change(deleted_admin_id=admin_id)
    return ok({"deleted": admin_id, "archived": True})


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
        raise RadiusValidationError("الصلاحيات يجب أن تكون قائمة نصية.")
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
            f"توجد صلاحيات غير معروفة: {bad}. "
            "راجع GET /api/v1/permissions لقائمة الصلاحيات."
        )
    return tuple(dict.fromkeys(out))  # de-dup, preserve order


def roles_create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return fail("validation_error", "اسم الدور مطلوب.", status=422)
    try:
        perms = _validate_permissions(body.get("permissions"))
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    from ...radius.auth.owner import OwnerGuardError, assert_role_within_actor
    try:
        assert_role_within_actor(_actor_id(), perms)
    except OwnerGuardError as exc:
        return _owner_guard_fail(exc)
    try:
        role = admins_repo.create_role(
            name=name,
            display_name=str(body.get("display_name") or name).strip(),
            description=str(body.get("description") or "").strip(),
            permissions=perms,
            color=str(body.get("color") or "#2BAACC").strip(),
        )
    except ValueError:
        # كان يُعرض نصُّ المبرمج الإنجليزيّ («role 'x' already exists») للمشغّل.
        return fail("conflict", f"اسم الدور «{name}» مستخدم مسبقًا.", status=409)
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
        from ...radius.auth.owner import OwnerGuardError, assert_role_within_actor
        try:
            assert_role_within_actor(_actor_id(), changes["permissions"])
        except OwnerGuardError as exc:
            return _owner_guard_fail(exc)
    role = admins_repo.update_role(role_id, **changes)
    if not role:
        return fail("not_found", "الدور غير موجود.", status=404)
    _audit("update", "role", str(role_id), {"fields": list(changes.keys())})
    return ok(_serialize_role(role))


def roles_delete(role_id: int):
    existing = admins_repo.get_role(role_id)
    if not existing:
        return fail("not_found", f"role {role_id} غير موجود", status=404)
    if existing.is_system:
        return fail("forbidden",
                    "لا يمكن حذف دور نظامي", status=403)
    # D22: دورٌ مُسنَد لمدراء لا يُحذف بصمت (كانوا يفقدون كل صلاحياتهم).
    in_use = admins_repo.role_usage_count(role_id)
    if in_use:
        return fail("role_in_use",
                    f"لا يمكن حذف الدور: مُسنَد إلى {in_use} مدير. انقلهم لدورٍ آخر أوّلًا.",
                    status=409, details={"admins_count": in_use})
    admins_repo.delete_role(role_id)
    _audit("archive", "role", str(role_id), {"name": existing.name})
    return ok({"deleted": role_id, "archived": True})


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
        # D14: keys kept only so stored roles stay valid — they gate nothing; the
        # app's role editor should hide them (the web editor does).
        "deprecated": sorted(DEPRECATED_PERMISSIONS),
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
