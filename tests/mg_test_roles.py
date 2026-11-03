"""Shared helper for the manager-grants tests (fix wave 2).

``admins_repo.create_admin(role_id=None)`` falls back to the system
``super_admin`` role, and since fix wave 2 (p01/D12) that role — «مدير عام» —
means *every non-owner permission and every granular grant* (all flags,
actions and sections). The grant tests need a PLAIN manager whose per-manager
flags and sections start from the defaults, so they use a custom role that
carries the legacy «مدير عام» RBAC key list without being the system role.
"""
from __future__ import annotations


def plain_role_id(perms=None) -> int:
    """Id of a non-system role with ``perms`` (default: the legacy super_admin
    key list). Created once per DB, re-used afterwards."""
    from app.radius.core.constants import DEFAULT_ROLE_PERMISSIONS
    from app.radius.db.repos import admins_repo

    keys = tuple(perms) if perms is not None else tuple(DEFAULT_ROLE_PERMISSIONS["super_admin"])
    import hashlib
    name = "plain_mgr_" + hashlib.md5("|".join(keys).encode()).hexdigest()[:8]
    role = admins_repo.get_role_by_name(name)
    if role is None:
        role = admins_repo.create_role(name=name, permissions=keys)
    return int(role.id)


def add_role_keys(admin_id: int, *keys: str) -> None:
    """Give ``admin_id`` a private role = his current role's keys + ``keys``.
    (p01/D14-D15: grants such as «استيراد الحِزم» now derive from their RBAC
    key — ``cards.import`` — so a test «grants» them through the role.)"""
    import uuid
    from app.radius.db.repos import admins_repo

    admin = admins_repo.get_admin(int(admin_id))
    base = set(admins_repo.admin_permissions(admin)) if admin else set()
    role = admins_repo.create_role(name="k_" + uuid.uuid4().hex[:8],
                                   permissions=tuple(sorted(base | set(keys))))
    admins_repo.update_admin(int(admin_id), role_id=role.id)


def role_keys(admin_id: int) -> list:
    """The admin's current RBAC keys from the DB (what a real login puts in the
    session)."""
    from app.radius.db.repos import admins_repo

    admin = admins_repo.get_admin(int(admin_id))
    return sorted(admins_repo.admin_permissions(admin)) if admin else []


def set_role_grants(admin_id: int, extra: dict) -> None:
    """Owner 03/11: grants live on the job title only. Give ``admin_id`` a
    private copy of his role whose granular blob carries ``extra`` merged in
    (``{"_actions": {...}}`` → action_grants, ``{"field_grants": {...}}`` …)."""
    import uuid
    from app.radius.db.repos import admins_repo
    from app.radius.services import manager_grants as mg

    admin = admins_repo.get_admin(int(admin_id))
    old = admins_repo.get_role(int(admin.role_id)) if admin and admin.role_id else None
    keys = tuple(getattr(old, "permissions", ()) or ())
    blob = dict(admins_repo.get_role_granular(int(old.id)) or {}) if old else {}
    role = admins_repo.create_role(name="g_" + uuid.uuid4().hex[:8], permissions=keys)
    for k, v in extra.items():
        if k == "_actions":
            ag = dict(blob.get("action_grants") or {})
            ag["_actions"] = {**dict(ag.get("_actions") or {}), **v}
            blob["action_grants"] = ag
        else:
            cur = dict(blob.get(k) or {})
            cur.update(v)
            blob[k] = cur
    admins_repo.set_role_granular(role.id, blob)
    admins_repo.update_admin(int(admin_id), role_id=role.id)
    mg._invalidate_cache()
