"""
AdminsStore — facade فوق admins_repo. الواجهة كما كانت.
"""
from __future__ import annotations

from threading import Lock
from typing import Optional

from ..core.types import Admin, Role
from ..db.repos import admins_repo

# Re-export password helpers (للـ tests)
hash_password = admins_repo.hash_password
verify_password = admins_repo.verify_password


class AdminsStore:
    _inst: Optional["AdminsStore"] = None
    _inst_lock = Lock()

    @classmethod
    def instance(cls) -> "AdminsStore":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = cls()
        return cls._inst

    # Roles
    def list_roles(self) -> list[Role]:
        return admins_repo.list_roles()

    def get_role(self, role_id: int) -> Optional[Role]:
        return admins_repo.get_role(role_id)

    def get_role_by_name(self, name: str) -> Optional[Role]:
        return admins_repo.get_role_by_name(name)

    def update_role_permissions(self, role_id: int, perms: tuple[str, ...]) -> Optional[Role]:
        return admins_repo.update_role_permissions(role_id, perms)

    # Admins
    def list_admins(self) -> list[Admin]:
        return admins_repo.list_admins()

    def get_admin(self, admin_id: int) -> Optional[Admin]:
        return admins_repo.get_admin(admin_id)

    def get_by_username(self, username: str) -> Optional[Admin]:
        return admins_repo.get_by_username(username)

    def create_admin(self, *, username: str, password: str, full_name: str = "",
                     email: str = "", mobile: str = "", role_id: Optional[int] = None,
                     is_super_admin: bool = False, enabled: bool = True) -> Admin:
        return admins_repo.create_admin(
            username=username, password=password, full_name=full_name,
            email=email, mobile=mobile, role_id=role_id,
            is_super_admin=is_super_admin, enabled=enabled,
        )

    def update_admin(self, admin_id: int, **changes) -> Optional[Admin]:
        return admins_repo.update_admin(admin_id, **changes)

    def delete_admin(self, admin_id: int) -> None:
        admins_repo.delete_admin(admin_id)

    def authenticate(self, username: str, password: str) -> Optional[Admin]:
        return admins_repo.authenticate(username, password)

    def admin_permissions(self, admin: Admin) -> tuple[str, ...]:
        return admins_repo.admin_permissions(admin)
