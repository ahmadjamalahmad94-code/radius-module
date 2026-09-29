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
