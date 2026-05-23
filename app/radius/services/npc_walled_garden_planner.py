"""npc_walled_garden_planner — pure planner for the Hotspot
Walled-Garden Allowlist sub-service.

Takes a policy dict + an iterable of entry dicts (shape from
`npc_walled_garden_repo.list_entries`) and emits a `ScriptPlan`
that adds entries to:

  * `/ip hotspot walled-garden`     — for `dst_host` entries
                                       (the captive portal lets
                                       these through before
                                       auth).
  * `/ip hotspot walled-garden ip`  — for `dst_address` and
                                       `dst_address_list`
                                       entries (L3 allowlist
                                       with optional dst-port
                                       + protocol).

Pure module: no DB, no network, no Flask.

Safety contract:
  * Cleanup-first ordering. The forward script removes every
    managed walled-garden entry for the policy id BEFORE
    inserting the new set, so a re-apply is idempotent.
  * Anchored regex on every `remove [find ...]` — substring
    matches elsewhere in an operator's free-form walled-garden
    comment cannot collide.
  * Empty-policy plans (no eligible entries) are a no-op —
    the planner emits cleanup only. Adding a walled-garden
    `*` allowlist would be catastrophic for an ISP's billing
    model.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .npc_policy import (
    MAX_TARGETS_PER_POLICY, cleanup_regex, comment_prefix,
)
from .npc_script_renderer import PlanCommand, ScriptPlan


SERVICE = "walled_garden"


# Entry-status values eligible for emission. Mirrors the
# web-block planner: only `active` rows make it onto the wire.
_EMIT_STATUSES = frozenset({"active"})


# Entry types that ride the host (regex) walled-garden table
# vs the IP table.
_HOST_TYPES = frozenset({"dst_host"})
_IP_TYPES   = frozenset({"dst_address", "dst_address_list"})


def plan(
    policy: dict,
    entries: Iterable[dict],
) -> ScriptPlan:
    if not policy:
        return ScriptPlan(
            service=SERVICE, policy_id=0,
            comment_prefix="",
            blocking_errors=("policy is empty",),
        )
    pid = int(policy["id"])
    cprefix = comment_prefix(SERVICE, pid)
    hotspot_profile = (
        policy.get("hotspot_profile") or ""
    ).strip()

    cleanup_ops, rollback_ops = _cleanup_pair(pid)

    eligible: list[dict] = []
    skipped_notes: list[str] = []
    for e in entries:
        if e.get("status") not in _EMIT_STATUSES:
            skipped_notes.append(
                f"skipped {e.get('value','?')} "
                f"(status={e.get('status','?')})"
            )
            continue
        et = (e.get("entry_type") or "").strip()
        if et not in _HOST_TYPES and et not in _IP_TYPES:
            skipped_notes.append(
                f"skipped {e.get('value','?')} "
                f"(entry_type={et!r})"
            )
            continue
        eligible.append(e)

    if len(eligible) > MAX_TARGETS_PER_POLICY:
        return ScriptPlan(
            service=SERVICE, policy_id=pid,
            comment_prefix=cprefix,
            cleanup_ops=cleanup_ops,
            rollback_ops=rollback_ops,
            blocking_errors=(
                f"walled-garden entry count "
                f"({len(eligible)}) exceeds the supported "
                f"maximum ({MAX_TARGETS_PER_POLICY}).",
            ),
        )

    walled_garden_ops: list[PlanCommand] = []
    for e in eligible:
        et = e["entry_type"]
        value = (e.get("normalized_value")
                 or e.get("value") or "").strip()
        if not value:
            continue
        if et in _HOST_TYPES:
            attrs: dict[str, str] = {
                "dst-host": value,
                "action": "allow",
                "comment": f"{cprefix}entry:{et}",
            }
            if hotspot_profile:
                attrs["server"] = hotspot_profile
            walled_garden_ops.append(PlanCommand(
                section="walled-garden",
                path="/ip/hotspot/walled-garden",
                kind="add",
                attrs=attrs,
                note=f"host allow: {value}",
            ))
        else:
            # /ip hotspot walled-garden ip — L3 allowlist.
            attrs = {
                "dst-address": value,
                "action": "accept",
                "comment": f"{cprefix}entry:{et}",
            }
            if hotspot_profile:
                attrs["server"] = hotspot_profile
            if e.get("dst_port"):
                attrs["dst-port"] = str(e["dst_port"])
            if e.get("protocol"):
                attrs["protocol"] = str(e["protocol"])
            walled_garden_ops.append(PlanCommand(
                section="walled-garden",
                path="/ip/hotspot/walled-garden/ip",
                kind="add",
                attrs=attrs,
                note=f"ip allow: {value}",
            ))

    warnings: list[str] = []
    if not walled_garden_ops:
        warnings.append(
            "لا توجد إدخالات نشطة — السياسة بدون أثر."
        )

    return ScriptPlan(
        service=SERVICE, policy_id=pid,
        comment_prefix=cprefix,
        cleanup_ops=cleanup_ops,
        walled_garden_ops=tuple(walled_garden_ops),
        rollback_ops=rollback_ops,
        warnings=tuple(warnings),
        notes=tuple(skipped_notes),
    )


# ─── Cleanup pair ────────────────────────────────────────────


def _cleanup_pair(pid: int) -> tuple[
    tuple[PlanCommand, ...], tuple[PlanCommand, ...]
]:
    fp = cleanup_regex(SERVICE, pid)
    ops = (
        PlanCommand(
            section="cleanup",
            path="/ip/hotspot/walled-garden",
            kind="remove",
            find_pattern=fp,
            note="remove prior host-table managed entries",
        ),
        PlanCommand(
            section="cleanup",
            path="/ip/hotspot/walled-garden/ip",
            kind="remove",
            find_pattern=fp,
            note="remove prior IP-table managed entries",
        ),
    )
    return ops, ops


__all__ = [
    "SERVICE", "plan",
]
