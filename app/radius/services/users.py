"""UsersService — المشتركون (Radius Accounts)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

from ..core.constants import (
    AUDIT_ACTION_CREATE, AUDIT_ACTION_DELETE, AUDIT_ACTION_DISABLE,
    AUDIT_ACTION_ENABLE, AUDIT_ACTION_RESET_PASSWORD, AUDIT_ACTION_UPDATE,
    STATUS_DISABLED, STATUS_ENABLED, USER_TYPES,
)
from ..core.errors import RadiusValidationError
from ..core.types import Subscriber
from ..integration.adapter import RadiusAdapter
from .audit import RadiusAuditService


class UsersService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def list(self, *, status: Optional[str] = None, plan_id: Optional[int] = None,
             search: str = "", limit: int = 500, offset: int = 0) -> Sequence[Subscriber]:
        items = list(self._adapter.list_accounts(status=status, limit=limit, offset=offset))
        if plan_id is not None:
            items = [u for u in items if u.plan_id == plan_id]
        if search:
            s = search.lower()
            items = [u for u in items if s in u.username.lower()
                     or s in (u.full_name or "").lower()
                     or s in (u.mobile or "")]
        return items

    def get(self, username: str) -> Subscriber:
        return self._adapter.get_account(username)

    def create(self, *, actor: str, sub: Subscriber) -> Subscriber:
        _validate(sub)
        saved = self._adapter.upsert_account(sub)
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="user", target_id=saved.username,
                           payload={"plan_id": saved.plan_id})
        return saved

    def update(self, *, actor: str, sub: Subscriber) -> Subscriber:
        _validate(sub)
        saved = self._adapter.upsert_account(sub)
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="user", target_id=saved.username)
        return saved

    def disable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        from dataclasses import replace
        self._adapter.upsert_account(replace(u, status=STATUS_DISABLED))
        self._audit.record(actor=actor, action=AUDIT_ACTION_DISABLE,
                           target_type="user", target_id=username)

    def enable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        from dataclasses import replace
        self._adapter.upsert_account(replace(u, status=STATUS_ENABLED))
        self._audit.record(actor=actor, action=AUDIT_ACTION_ENABLE,
                           target_type="user", target_id=username)

    def reset_password(self, *, actor: str, username: str, new_password: str) -> None:
        if not new_password:
            raise RadiusValidationError("new password required")
        self._adapter.reset_password(username, new_password)
        self._audit.record(actor=actor, action=AUDIT_ACTION_RESET_PASSWORD,
                           target_type="user", target_id=username)

    def extend_time(self, *, actor: str, username: str, minutes: int) -> Subscriber:
        if minutes <= 0:
            raise RadiusValidationError("minutes > 0 required")
        u = self._adapter.get_account(username)
        from dataclasses import replace
        new_exp = (u.expire_at or datetime.utcnow()) + timedelta(minutes=minutes)
        saved = self._adapter.upsert_account(replace(u, expire_at=new_exp))
        self._audit.record(actor=actor, action="extend_time",
                           target_type="user", target_id=username,
                           payload={"minutes": minutes, "new_expire_at": new_exp.isoformat()})
        return saved

    def delete(self, *, actor: str, username: str) -> None:
        self._adapter.delete_account(username)
        self._audit.record(actor=actor, action=AUDIT_ACTION_DELETE,
                           target_type="user", target_id=username)


def _validate(sub: Subscriber) -> None:
    if sub.user_type not in USER_TYPES:
        raise RadiusValidationError(f"unknown user_type: {sub.user_type!r}")
    if not sub.username:
        raise RadiusValidationError("username required")


def get_users_service() -> UsersService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return UsersService(get_radius_adapter(), audit=get_audit_service())
