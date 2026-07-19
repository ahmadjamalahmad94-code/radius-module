"""
NasDevicesService — منطق إدارة الـ NAS.

- يستلم RadiusAdapter + RadiusAuditService.
- لا يلامس Flask request/session — الـ route يمرّر `actor`.
- كل عملية كتابة → audit.record(...).
"""
from __future__ import annotations

from typing import Optional, Sequence

from ..core.constants import (
    AUDIT_ACTION_ARCHIVE,
    AUDIT_ACTION_CREATE,
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
        # MT12 — سقف أجهزة الجهة (استضافة دائمة متعددة الجهات).
        from .tenants import tenant_capacity_block_reason
        _cap_msg = tenant_capacity_block_reason(device.tenant_id, "nas")
        if _cap_msg:
            raise RadiusValidationError(_cap_msg)
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
        # Capture the router's mgmt-tunnel IP BEFORE archiving so we can release
        # the WireGuard peer bound to it on the VPS. Best-effort — a lookup miss
        # never blocks the delete.
        mgmt_ip = _mgmt_ip_for_nas(nas_id)
        self._adapter.delete_nas(nas_id)
        # Release the 10.10.0.x mgmt peer on the VPS so the IP is actually freed
        # everywhere (not just soft-deleted in the DB). The allocator already
        # stops counting the archived row (deleted_at filter); removing the peer
        # file prevents a stale peer/route lingering when the IP is reused.
        if mgmt_ip:
            _release_mgmt_peer(mgmt_ip)
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_ARCHIVE,
            target_type="nas",
            target_id=str(nas_id),
            payload={"mode": "soft_delete", "released_mgmt_ip": mgmt_ip or ""},
        )


def _mgmt_ip_for_nas(nas_id: int) -> str:
    """The router's mgmt-tunnel IP (``vpn_peer_address``, e.g. 10.10.0.7) for a
    NAS, read raw since the ``NasDevice`` dataclass omits the VPN columns.

    Best-effort: returns "" on any error (missing column/table in some deploys,
    row already gone). Never raises — releasing the peer is a cleanup step, not a
    precondition for the delete."""
    try:
        from ..db.connection import db
        try:
            from ..integration.sqlite_adapter import _tid
            tenant_id = _tid()
        except Exception:  # noqa: BLE001 — no request context → default tenant
            tenant_id = 1
        row = db().execute(
            "SELECT vpn_peer_address FROM nas_devices WHERE id=? AND tenant_id=?",
            (int(nas_id), int(tenant_id)),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return ""
    if not row:
        return ""
    return str((row["vpn_peer_address"] if not isinstance(row, dict) else row.get("vpn_peer_address")) or "").strip()


def _release_mgmt_peer(mgmt_ip: str) -> None:
    """Remove the mgmt-WG peer bound to ``mgmt_ip`` on the VPS. Best-effort."""
    try:
        from .wg_peer_manager import release_peer_by_ip
        release_peer_by_ip(mgmt_ip)
    except Exception:  # noqa: BLE001 — peer cleanup must never break a delete
        import logging
        logging.getLogger(__name__).warning(
            "wg mgmt peer release failed for ip=%s", mgmt_ip, exc_info=True,
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
