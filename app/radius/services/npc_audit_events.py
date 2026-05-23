"""npc_audit_events — Network Policy Center audit-event
catalogue.

Pure module. No DB, no Flask, no network. The strings here
become the `action` field on `audit_log` rows once Phase 4
wires the routes; pinning them in their own file keeps the
catalogue self-documenting and prevents drift between caller
and auditor.

Event-action naming convention:
    npc.<service>.<verb>

Stable forever — once shipped, never rename, only add.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# ─── Action strings (stable contract) ────────────────────────


# Per-sub-service per-verb action strings. Read these from
# the route handlers so the catalogue is the single source.

_VERBS = (
    "preview_generated",
    "apply_attempted",
    "applied",
    "apply_failed",
    "rolled_back",
    "policy_created",
    "policy_updated",
    "policy_deleted",
    "target_added",     # web_block / walled_garden only
    "target_removed",   # web_block / walled_garden only
)

_SERVICES = ("remote_access", "web_block", "walled_garden")


# Builds {action: True} entries for every (service, verb)
# combination at import time so the test pin is exhaustive.
ALL_EVENTS: tuple[str, ...] = tuple(
    sorted(f"npc.{svc}.{verb}"
           for svc in _SERVICES for verb in _VERBS)
)


# Convenience constants the route layer uses directly. Anyone
# typing one of these literals as a string elsewhere is asked
# (via review) to import the constant instead.

# remote_access
EVT_RA_PREVIEW_GENERATED = "npc.remote_access.preview_generated"
EVT_RA_APPLY_ATTEMPTED   = "npc.remote_access.apply_attempted"
EVT_RA_APPLIED           = "npc.remote_access.applied"
EVT_RA_APPLY_FAILED      = "npc.remote_access.apply_failed"
EVT_RA_ROLLED_BACK       = "npc.remote_access.rolled_back"
EVT_RA_POLICY_CREATED    = "npc.remote_access.policy_created"
EVT_RA_POLICY_UPDATED    = "npc.remote_access.policy_updated"
EVT_RA_POLICY_DELETED    = "npc.remote_access.policy_deleted"

# web_block
EVT_WB_PREVIEW_GENERATED = "npc.web_block.preview_generated"
EVT_WB_APPLY_ATTEMPTED   = "npc.web_block.apply_attempted"
EVT_WB_APPLIED           = "npc.web_block.applied"
EVT_WB_APPLY_FAILED      = "npc.web_block.apply_failed"
EVT_WB_ROLLED_BACK       = "npc.web_block.rolled_back"
EVT_WB_POLICY_CREATED    = "npc.web_block.policy_created"
EVT_WB_POLICY_UPDATED    = "npc.web_block.policy_updated"
EVT_WB_POLICY_DELETED    = "npc.web_block.policy_deleted"
EVT_WB_TARGET_ADDED      = "npc.web_block.target_added"
EVT_WB_TARGET_REMOVED    = "npc.web_block.target_removed"

# walled_garden
EVT_WG_PREVIEW_GENERATED = "npc.walled_garden.preview_generated"
EVT_WG_APPLY_ATTEMPTED   = "npc.walled_garden.apply_attempted"
EVT_WG_APPLIED           = "npc.walled_garden.applied"
EVT_WG_APPLY_FAILED      = "npc.walled_garden.apply_failed"
EVT_WG_ROLLED_BACK       = "npc.walled_garden.rolled_back"
EVT_WG_POLICY_CREATED    = "npc.walled_garden.policy_created"
EVT_WG_POLICY_UPDATED    = "npc.walled_garden.policy_updated"
EVT_WG_POLICY_DELETED    = "npc.walled_garden.policy_deleted"
EVT_WG_TARGET_ADDED      = "npc.walled_garden.target_added"
EVT_WG_TARGET_REMOVED    = "npc.walled_garden.target_removed"


# ─── target_type strings for audit rows ──────────────────────


# The `target_type` column on audit_log gets set from these so
# the audit timeline can filter by sub-service.
TARGET_REMOTE_ACCESS = "npc_remote_access_policy"
TARGET_WEB_BLOCK     = "npc_web_block_policy"
TARGET_WALLED_GARDEN = "npc_walled_garden_policy"


def target_type_for(service: str) -> str:
    """Map a NPC service discriminator to its audit
    `target_type`. Raises ValueError on unknown service."""
    mapping = {
        "remote_access": TARGET_REMOTE_ACCESS,
        "web_block":     TARGET_WEB_BLOCK,
        "walled_garden": TARGET_WALLED_GARDEN,
    }
    if service not in mapping:
        raise ValueError(
            f"unknown NPC service for audit target_type: "
            f"{service!r}"
        )
    return mapping[service]


# ─── Payload builder ─────────────────────────────────────────


@dataclass(frozen=True)
class AuditPayload:
    """Stable payload shape sent to `RadiusAuditService.record`
    as the `payload=` dict. Frozen + tuple-keyed `extra` so the
    callsites can't mutate it after the fact."""
    service: str
    policy_id: int
    router_id: Optional[int]
    actor_admin_id: Optional[int]
    script_hash: str = ""
    error: str = ""
    extra: tuple[tuple[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "service":        self.service,
            "policy_id":      int(self.policy_id),
            "router_id":      (int(self.router_id)
                               if self.router_id is not None
                               else None),
            "actor_admin_id": (int(self.actor_admin_id)
                               if self.actor_admin_id is not None
                               else None),
        }
        if self.script_hash:
            out["script_hash"] = self.script_hash
        if self.error:
            out["error"] = self.error[:1000]
        for k, v in self.extra:
            out[k] = v
        return out


def build_payload(
    *,
    service: str,
    policy_id: int,
    router_id: Optional[int] = None,
    actor_admin_id: Optional[int] = None,
    script_hash: str = "",
    error: str = "",
    **extra: Any,
) -> AuditPayload:
    """Convenience: build the AuditPayload from keyword args
    that the route layer naturally has on hand. Extra kwargs
    land in the `extra` tuple so route-specific context (e.g.
    `expires_at`, `target_count`) is preserved on the audit
    row without changing the payload dataclass."""
    return AuditPayload(
        service=service,
        policy_id=policy_id,
        router_id=router_id,
        actor_admin_id=actor_admin_id,
        script_hash=script_hash,
        error=error,
        extra=tuple(sorted(extra.items())),
    )


__all__ = [
    "ALL_EVENTS",
    "EVT_RA_PREVIEW_GENERATED", "EVT_RA_APPLY_ATTEMPTED",
    "EVT_RA_APPLIED", "EVT_RA_APPLY_FAILED",
    "EVT_RA_ROLLED_BACK",
    "EVT_RA_POLICY_CREATED", "EVT_RA_POLICY_UPDATED",
    "EVT_RA_POLICY_DELETED",
    "EVT_WB_PREVIEW_GENERATED", "EVT_WB_APPLY_ATTEMPTED",
    "EVT_WB_APPLIED", "EVT_WB_APPLY_FAILED",
    "EVT_WB_ROLLED_BACK",
    "EVT_WB_POLICY_CREATED", "EVT_WB_POLICY_UPDATED",
    "EVT_WB_POLICY_DELETED",
    "EVT_WB_TARGET_ADDED", "EVT_WB_TARGET_REMOVED",
    "EVT_WG_PREVIEW_GENERATED", "EVT_WG_APPLY_ATTEMPTED",
    "EVT_WG_APPLIED", "EVT_WG_APPLY_FAILED",
    "EVT_WG_ROLLED_BACK",
    "EVT_WG_POLICY_CREATED", "EVT_WG_POLICY_UPDATED",
    "EVT_WG_POLICY_DELETED",
    "EVT_WG_TARGET_ADDED", "EVT_WG_TARGET_REMOVED",
    "TARGET_REMOTE_ACCESS", "TARGET_WEB_BLOCK",
    "TARGET_WALLED_GARDEN",
    "target_type_for",
    "AuditPayload", "build_payload",
]
