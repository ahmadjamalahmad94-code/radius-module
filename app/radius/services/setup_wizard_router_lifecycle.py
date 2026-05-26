"""Router provisioning lifecycle state machine for Setup Wizard.

This module is persistence and validation only. It does not execute MikroTik
commands and does not mutate WireGuard server configuration.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import row_to_dict
from .setup_wizard_common import SetupWizardValidationError


ROUTER_LIFECYCLE_STATES = {
    "reserved",
    "script_generated",
    "waiting_router_key",
    "router_key_received",
    "peer_pending",
    "peer_ready",
    "vpn_verified",
    "radius_pending",
    "radius_verified",
    "api_pending",
    "api_verified",
    "fully_onboarded",
    "failed",
    "retired",
}

_FORWARD_TRANSITIONS: dict[str, set[str]] = {
    "reserved": {"script_generated", "failed", "retired"},
    "script_generated": {"waiting_router_key", "failed", "retired"},
    "waiting_router_key": {"router_key_received", "failed", "retired"},
    "router_key_received": {"peer_pending", "failed", "retired"},
    "peer_pending": {"peer_ready", "failed", "retired"},
    "peer_ready": {"vpn_verified", "failed", "retired"},
    "vpn_verified": {"radius_pending", "failed", "retired"},
    "radius_pending": {"radius_verified", "failed", "retired"},
    "radius_verified": {"api_pending", "failed", "retired"},
    "api_pending": {"api_verified", "failed", "retired"},
    "api_verified": {"fully_onboarded", "failed", "retired"},
    "fully_onboarded": {"retired"},
    "failed": {"script_generated", "waiting_router_key", "peer_pending", "peer_ready", "retired"},
    "retired": set(),
}

_REGISTRY_STATUS_BY_LIFECYCLE = {
    "reserved": "reserved",
    "script_generated": "generated",
    "waiting_router_key": "generated",
    "router_key_received": "generated",
    "peer_pending": "generated",
    "peer_ready": "generated",
    "vpn_verified": "generated",
    "radius_pending": "generated",
    "radius_verified": "generated",
    "api_pending": "generated",
    "api_verified": "generated",
    "fully_onboarded": "verified",
    "failed": "failed",
    "retired": "retired",
}


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RouterLifecycleEvent:
    id: int
    tenant_id: int
    registry_id: int
    wizard_run_id: int | None
    from_state: str
    to_state: str
    event_type: str
    actor: str
    reason: str
    metadata: dict[str, Any]
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "RouterLifecycleEvent":
        data = row_to_dict(row)
        return cls(
            id=int(data["id"]),
            tenant_id=int(data["tenant_id"]),
            registry_id=int(data["registry_id"]),
            wizard_run_id=int(data["wizard_run_id"]) if data.get("wizard_run_id") is not None else None,
            from_state=str(data.get("from_state") or ""),
            to_state=str(data.get("to_state") or ""),
            event_type=str(data.get("event_type") or "transition"),
            actor=str(data.get("actor") or "system"),
            reason=str(data.get("reason") or ""),
            metadata=_json_loads(str(data.get("metadata_json") or "{}"), {}),
            created_at=str(data.get("created_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "registry_id": self.registry_id,
            "wizard_run_id": self.wizard_run_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "event_type": self.event_type,
            "actor": self.actor,
            "reason": self.reason,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


class RouterLifecycleService:
    """Validates and persists provisioning state changes."""

    def get_registry(self, *, tenant_id: int, registry_id: int) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT * FROM router_provisioning_registry
            WHERE tenant_id=? AND id=?
            """,
            (int(tenant_id), int(registry_id)),
        ).fetchone()
        if not row:
            raise SetupWizardValidationError("router provisioning registry not found")
        return row_to_dict(row)

    def current_state(self, *, tenant_id: int, registry_id: int) -> str:
        registry = self.get_registry(tenant_id=tenant_id, registry_id=registry_id)
        state = str(registry.get("lifecycle_state") or registry.get("status") or "reserved")
        return state if state in ROUTER_LIFECYCLE_STATES else "reserved"

    def transition(
        self,
        *,
        tenant_id: int,
        registry_id: int,
        to_state: str,
        actor: str = "system",
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = str(to_state or "").strip()
        if target not in ROUTER_LIFECYCLE_STATES:
            raise SetupWizardValidationError("unknown router lifecycle state")
        now = _now()
        with transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM router_provisioning_registry
                WHERE tenant_id=? AND id=?
                """,
                (int(tenant_id), int(registry_id)),
            ).fetchone()
            if not row:
                raise SetupWizardValidationError("router provisioning registry not found")
            current = str(row["lifecycle_state"] or row["status"] or "reserved")
            if current not in ROUTER_LIFECYCLE_STATES:
                current = "reserved"
            if current != target and target not in _FORWARD_TRANSITIONS[current]:
                raise SetupWizardValidationError(
                    f"invalid router lifecycle transition: {current} -> {target}"
                )
            registry_status = _REGISTRY_STATUS_BY_LIFECYCLE[target]
            failure_reason = str(reason or "") if target == "failed" else ""
            conn.execute(
                """
                UPDATE router_provisioning_registry
                SET lifecycle_state=?, status=?, failure_reason=?,
                    lifecycle_updated_at=?, updated_at=?,
                    retired_at=CASE WHEN ?='retired' AND retired_at='' THEN ? ELSE retired_at END
                WHERE tenant_id=? AND id=?
                """,
                (
                    target,
                    registry_status,
                    failure_reason,
                    now,
                    now,
                    target,
                    now,
                    int(tenant_id),
                    int(registry_id),
                ),
            )
            if current != target:
                conn.execute(
                    """
                    INSERT INTO router_lifecycle_events (
                      tenant_id, registry_id, wizard_run_id, from_state, to_state,
                      event_type, actor, reason, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'transition', ?, ?, ?, ?)
                    """,
                    (
                        int(tenant_id),
                        int(registry_id),
                        row["wizard_run_id"],
                        current,
                        target,
                        (actor or "system")[:120],
                        (reason or "")[:500],
                        _json_dumps(metadata or {}),
                        now,
                    ),
                )
            # ── Promote to permanent on success ──────────────
            # Once the router actually reaches a verified state,
            # clear the TTL so the janitor leaves it alone.
            from .setup_wizard_tentative_reclaimer import (
                PERMANENT_STATES as _PERM,
            )
            if target in _PERM:
                conn.execute(
                    """UPDATE router_provisioning_registry
                       SET tentative_expires_at='',
                           tentative_started_at=''
                       WHERE tenant_id=? AND id=?""",
                    (int(tenant_id), int(registry_id)),
                )
            updated = conn.execute(
                """
                SELECT * FROM router_provisioning_registry
                WHERE tenant_id=? AND id=?
                """,
                (int(tenant_id), int(registry_id)),
            ).fetchone()
        return row_to_dict(updated)

    def mark_failed(
        self,
        *,
        tenant_id: int,
        registry_id: int,
        reason: str,
        actor: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.transition(
            tenant_id=tenant_id,
            registry_id=registry_id,
            to_state="failed",
            actor=actor,
            reason=reason,
            metadata=metadata or {},
        )

    def retry(
        self,
        *,
        tenant_id: int,
        registry_id: int,
        target_state: str = "script_generated",
        actor: str = "system",
        reason: str = "retry requested",
    ) -> dict[str, Any]:
        if self.current_state(tenant_id=tenant_id, registry_id=registry_id) != "failed":
            raise SetupWizardValidationError("retry is allowed only from failed lifecycle state")
        if target_state not in _FORWARD_TRANSITIONS["failed"]:
            raise SetupWizardValidationError("invalid retry target state")
        return self.transition(
            tenant_id=tenant_id,
            registry_id=registry_id,
            to_state=target_state,
            actor=actor,
            reason=reason,
        )

    def retire(
        self,
        *,
        tenant_id: int,
        registry_id: int,
        actor: str = "system",
        reason: str = "retired",
    ) -> dict[str, Any]:
        return self.transition(
            tenant_id=tenant_id,
            registry_id=registry_id,
            to_state="retired",
            actor=actor,
            reason=reason,
        )

    def history(self, *, tenant_id: int, registry_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM router_lifecycle_events
            WHERE tenant_id=? AND registry_id=?
            ORDER BY id ASC
            """,
            (int(tenant_id), int(registry_id)),
        ).fetchall()
        return [RouterLifecycleEvent.from_row(row).to_dict() for row in rows]
