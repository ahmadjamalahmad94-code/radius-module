"""AdminsService — مدراء + أدوار + صلاحيات."""
from __future__ import annotations

from typing import Optional

from ..core.constants import (
    ALL_PERMISSIONS, AUDIT_ACTION_ARCHIVE, AUDIT_ACTION_CREATE,
    AUDIT_ACTION_UPDATE,
)
from ..core.errors import RadiusValidationError
from ..core.types import Admin, Role
from ..stores.admins_store import AdminsStore
from .audit import RadiusAuditService


class AdminsService:
    def __init__(self, audit: RadiusAuditService) -> None:
        self._store = AdminsStore.instance()
        self._audit = audit

    # ─── admins ───
    def list_admins(self) -> list[Admin]:
        return self._store.list_admins()

    def get_admin(self, admin_id: int) -> Optional[Admin]:
        return self._store.get_admin(admin_id)

    def create_admin(self, *, actor: str, username: str, password: str, full_name: str = "",
                     email: str = "", mobile: str = "", role_id: Optional[int] = None,
                     enabled: bool = True) -> Admin:
        if not username or not password:
            raise RadiusValidationError("username + password مطلوبان")
        a = self._store.create_admin(
            username=username, password=password, full_name=full_name,
            email=email, mobile=mobile, role_id=role_id, enabled=enabled,
        )
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="admin", target_id=str(a.id),
                           payload={"username": a.username})
        return a

    def update_admin(self, *, actor: str, admin_id: int, **changes) -> Admin:
        a = self._store.update_admin(admin_id, **changes)
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="admin", target_id=str(a.id))
        return a

    def delete_admin(self, *, actor: str, admin_id: int) -> None:
        self._store.delete_admin(admin_id)
        self._audit.record(actor=actor, action=AUDIT_ACTION_ARCHIVE,
                           target_type="admin", target_id=str(admin_id),
                           payload={"mode": "soft_delete"})

    # ─── roles + permissions ───
    def list_roles(self) -> list[Role]:
        return self._store.list_roles()

    def get_role(self, role_id: int) -> Optional[Role]:
        return self._store.get_role(role_id)

    def all_permissions(self) -> tuple[str, ...]:
        return ALL_PERMISSIONS

    def update_role_permissions(self, *, actor: str, role_id: int,
                                 perms: tuple[str, ...]) -> Role:
        valid = tuple(p for p in perms if p in ALL_PERMISSIONS)
        r = self._store.update_role_permissions(role_id, valid)
        self._audit.record(actor=actor, action="role_permissions",
                           target_type="role", target_id=str(role_id),
                           payload={"perms": list(valid)})
        return r

    def authenticate(self, username: str, password: str) -> Optional[Admin]:
        return self._store.authenticate(username, password)

    def permissions_of(self, admin: Admin) -> tuple[str, ...]:
        return self._store.admin_permissions(admin)


def get_admins_service() -> AdminsService:
    from .audit import get_audit_service
    return AdminsService(audit=get_audit_service())
