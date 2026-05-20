"""Safe RADIUS apply layer for accounting decisions.

Accounting decides entitlement. This module performs the operational account
update through the same RadiusAdapter used by the web/API paths.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from ..core.constants import AUDIT_ACTION_UPDATE, STATUS_ENABLED
from ..core.errors import RadiusValidationError
from ..db.helpers import dt_to_iso
from ..integration.factory import get_radius_adapter
from .audit import get_audit_service


def apply_activation_minutes(
    *,
    username: str,
    minutes: int,
    actor: str,
    source: str,
    dry_run: bool = False,
) -> dict:
    if not username:
        raise RadiusValidationError("username required for RADIUS apply")
    if minutes <= 0:
        raise RadiusValidationError("minutes must be > 0 for RADIUS apply")

    adapter = get_radius_adapter()
    account = adapter.get_account(username)
    now = datetime.utcnow()
    current_expire = account.expire_at
    base = current_expire if current_expire and current_expire > now else now
    new_expire = base + timedelta(minutes=minutes)
    action_id = f"radius-apply-{uuid4().hex[:12]}"
    result = {
        "applied_to_radius": False,
        "dry_run": bool(dry_run),
        "radius_action_id": action_id,
        "source": source,
        "username": username,
        "minutes": minutes,
        "old_expire_at": dt_to_iso(current_expire),
        "new_expire_at": dt_to_iso(new_expire),
        "status": "planned" if dry_run else "applied",
    }
    if dry_run:
        return result

    saved = adapter.upsert_account(replace(
        account,
        expire_at=new_expire,
        status=STATUS_ENABLED,
    ))
    result.update({
        "applied_to_radius": True,
        "saved_status": saved.status,
        "saved_expire_at": dt_to_iso(saved.expire_at),
    })
    try:
        get_audit_service().record(
            actor=actor,
            action=AUDIT_ACTION_UPDATE,
            target_type="subscriber",
            target_id=username,
            payload={
                "radius_action_id": action_id,
                "source": source,
                "minutes": minutes,
                "old_expire_at": dt_to_iso(current_expire),
                "new_expire_at": dt_to_iso(new_expire),
            },
        )
    except Exception:
        # Account update is authoritative; audit failure should not roll it back.
        pass
    return result
