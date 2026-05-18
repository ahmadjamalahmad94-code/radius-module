"""
NasDevicesService — منطق إدارة الـ NAS.

- يستلم RadiusAdapter + RadiusAuditService.
- لا يلامس Flask request/session — الـ route يمرّر `actor`.
- كل عملية كتابة → audit.record(...).
"""
from __future__ import annotations

from typing import Optional, Sequence

from ..core.constants import (
    AUDIT_ACTION_CREATE,
    AUDIT_ACTION_DELETE,
    AUDIT_ACTION_UPDATE,
    NAS_VENDORS,
)
from ..core.errors import RadiusValidationError
from ..core.types import NasDevice
from ..integration.adapter import RadiusAdapter
from .audit import RadiusAuditService


class NasDevicesService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[NasDevice]:
        return self._adapter.list_nas(limit=limit, offset=offset)

    def get(self, nas_id: int) -> NasDevice:
        return self._adapter.get_nas(nas_id)

    def create(self, *, actor: str, device: NasDevice) -> NasDevice:
        _validate(device)
        saved = self._adapter.upsert_nas(device)
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_CREATE,
            target_type="nas",
            target_id=str(saved.id),
            payload={"name": saved.name, "address": saved.address, "vendor": saved.vendor},
        )
        return saved

    def update(self, *, actor: str, device: NasDevice) -> NasDevice:
        if device.id is None:
            raise RadiusValidationError("update requires id")
        _validate(device)
        saved = self._adapter.upsert_nas(device)
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_UPDATE,
            target_type="nas",
            target_id=str(saved.id),
            payload={"name": saved.name, "address": saved.address, "vendor": saved.vendor},
        )
        return saved

    def delete(self, *, actor: str, nas_id: int) -> None:
        self._adapter.delete_nas(nas_id)
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_DELETE,
            target_type="nas",
            target_id=str(nas_id),
        )


def _validate(device: NasDevice) -> None:
    if device.vendor not in NAS_VENDORS:
        raise RadiusValidationError(f"unknown vendor: {device.vendor!r}")


# Helper للـ routes في M2 (يستخدم الـ defaults)
def get_nas_devices_service() -> NasDevicesService:
    from ..integration.factory import get_radius_adapter

    return NasDevicesService(get_radius_adapter(), audit=_audit_singleton())


def _audit_singleton() -> RadiusAuditService:
    from .audit import get_audit_service

    return get_audit_service()
