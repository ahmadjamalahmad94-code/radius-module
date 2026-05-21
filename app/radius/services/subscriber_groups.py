"""SubscriberGroupsService — مجموعات المشتركين (CRUD + audit).

Thin wrapper over subscriber_groups_repo. The repo owns SQL; this layer
adds tenant scoping, validation, and audit-trail entries.

Pattern reference: SERVICES_COOKBOOK §15.
"""
from __future__ import annotations

from typing import Optional

from ..core.errors import RadiusValidationError
from ..db.repos import subscriber_groups_repo


class SubscriberGroupsService:
    def __init__(self, audit) -> None:
        self._audit = audit

    def list(self, *, tenant_id: int) -> list[dict]:
        return subscriber_groups_repo.list_groups(tenant_id)

    def get(self, *, tenant_id: int, gid: int) -> Optional[dict]:
        return subscriber_groups_repo.get(tenant_id, gid)

    def create(self, *, actor: str, tenant_id: int, name: str,
               description: str = "",
               bandwidth_schedule_id: Optional[int] = None,
               default_plan_id: Optional[int] = None,
               default_auto_renewal: bool = True,
               working_days: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            raise RadiusValidationError("اسم المجموعة مطلوب")
        if subscriber_groups_repo.get_by_name(tenant_id, name):
            raise RadiusValidationError(
                f"اسم المجموعة «{name}» مستخدم مسبقًا في هذا الـ tenant.")
        gid = subscriber_groups_repo.create(
            tenant_id=tenant_id, name=name, description=description,
            bandwidth_schedule_id=bandwidth_schedule_id,
            default_plan_id=default_plan_id,
            default_auto_renewal=default_auto_renewal,
            working_days=working_days,
        )
        self._audit.record(
            actor=actor, action="subscriber_group.create",
            target_type="subscriber_group", target_id=str(gid),
            payload={"name": name},
        )
        return subscriber_groups_repo.get(tenant_id, gid)

    def update(self, *, actor: str, tenant_id: int, gid: int,
               **changes) -> Optional[dict]:
        current = subscriber_groups_repo.get(tenant_id, gid)
        if not current:
            raise RadiusValidationError("المجموعة غير موجودة")
        new_name = (changes.get("name") or "").strip()
        if new_name and new_name != current["name"]:
            clash = subscriber_groups_repo.get_by_name(tenant_id, new_name)
            if clash and clash["id"] != gid:
                raise RadiusValidationError(
                    f"اسم المجموعة «{new_name}» مستخدم مسبقًا.")
            changes["name"] = new_name
        updated = subscriber_groups_repo.update(tenant_id, gid, **changes)
        self._audit.record(
            actor=actor, action="subscriber_group.update",
            target_type="subscriber_group", target_id=str(gid),
            payload={k: v for k, v in changes.items() if k != "description"},
        )
        return updated

    def delete(self, *, actor: str, tenant_id: int, gid: int) -> None:
        current = subscriber_groups_repo.get(tenant_id, gid)
        if not current:
            return
        subscriber_groups_repo.delete(tenant_id, gid)
        self._audit.record(
            actor=actor, action="subscriber_group.delete",
            target_type="subscriber_group", target_id=str(gid),
            payload={"name": current["name"]},
        )

    def members(self, *, tenant_id: int, gid: int, limit: int = 500) -> list[dict]:
        return subscriber_groups_repo.list_members(tenant_id, gid, limit=limit)


_singleton: Optional[SubscriberGroupsService] = None


def get_subscriber_groups_service() -> SubscriberGroupsService:
    global _singleton
    if _singleton is None:
        from .audit import RadiusAuditService
        _singleton = SubscriberGroupsService(audit=RadiusAuditService())
    return _singleton
