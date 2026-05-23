"""npc_web_block_planner — pure planner for the Website /
App Blocking sub-service.

Takes a policy dict + an iterable of target dicts and emits a
`ScriptPlan` that:

  1. Builds a per-policy `/ip/firewall/address-list` named
     `HOBE_NPC_BLOCK_<policy_id>` with one entry per ACTIVE
     target (skipping `disabled`, `invalid`, and
     `manual_review` rows).
  2. Adds one `/ip/firewall/filter` rule on the forward chain
     that drops traffic whose `dst-address-list` matches the
     managed list.
  3. Emits a cleanup section that removes every managed object
     for the policy BEFORE the new adds, so re-runs are
     idempotent.

Pure module: no DB, no network, no Flask.

Safety contract:
  * Empty-target policies plan to a no-op when `fail_open=1`
    (no rule emitted, traffic flows). If `fail_open=0`, the
    planner still produces a script — but only the cleanup;
    we never invent a default-drop rule when the target list
    is empty.
  * Anchored cleanup regex per policy; cannot affect unrelated
    rules.
  * Skipped targets get a `note` describing the reason — the
    UI shows them in the preview pane without applying.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .npc_policy import (
    MAX_TARGETS_PER_POLICY, cleanup_regex, comment_prefix,
)
from .npc_script_renderer import PlanCommand, ScriptPlan


SERVICE = "web_block"


# Status values from `npc_web_block_repo` that this planner
# treats as eligible-for-emission. Anything else gets skipped
# with a note.
_EMIT_STATUSES = frozenset({"active"})


def address_list_name(policy_id: int) -> str:
    """The MikroTik address-list this policy manages."""
    return f"HOBE_NPC_BLOCK_{int(policy_id)}"


def plan(
    policy: dict,
    targets: Iterable[dict],
    *,
    schedule_time_value: Optional[str] = None,
) -> ScriptPlan:
    """Build a forward + rollback plan from a policy + targets.

    `targets` is the iterable repo `list_targets()` returns —
    each dict has at minimum `value`, `normalized_value`,
    `target_type`, `category`, `status`.

    `schedule_time_value` is the RouterOS `time=` attribute
    (e.g. `0-24h,sun,mon,tue,wed,thu`) that the renderer will
    pass through verbatim. It's `None` when no schedule
    applies. The schedule lookup itself is the caller's job —
    keeps the planner pure.
    """
    if not policy:
        return ScriptPlan(
            service=SERVICE, policy_id=0,
            comment_prefix="",
            blocking_errors=("policy is empty",),
        )
    pid = int(policy["id"])
    cprefix = comment_prefix(SERVICE, pid)
    al_name = address_list_name(pid)

    cleanup_ops, rollback_ops = _cleanup_pair(pid)

    emitted_targets: list[dict] = []
    skipped_notes: list[str] = []
    for t in targets:
        if t.get("status") not in _EMIT_STATUSES:
            skipped_notes.append(
                f"skipped {t.get('value','?')} "
                f"(status={t.get('status','?')})"
            )
            continue
        emitted_targets.append(t)

    # Soft-cap per the foundation constants.
    if len(emitted_targets) > MAX_TARGETS_PER_POLICY:
        return ScriptPlan(
            service=SERVICE, policy_id=pid,
            comment_prefix=cprefix,
            cleanup_ops=cleanup_ops,
            rollback_ops=rollback_ops,
            blocking_errors=(
                f"web-block target count "
                f"({len(emitted_targets)}) exceeds the "
                f"supported maximum "
                f"({MAX_TARGETS_PER_POLICY}).",
            ),
        )

    address_list_ops: list[PlanCommand] = []
    for t in emitted_targets:
        # Use normalized_value where the analyzer cleaned it
        # up; fall back to raw value for safety. We pass the
        # value verbatim — RouterOS accepts both domain strings
        # and IP/CIDR in `/ip/firewall/address-list`.
        value = (t.get("normalized_value")
                 or t.get("value") or "").strip()
        if not value:
            continue
        category = (t.get("category") or "custom")
        address_list_ops.append(PlanCommand(
            section="address-list",
            path="/ip/firewall/address-list",
            kind="add",
            attrs={
                "list": al_name,
                "address": value,
                "comment": (
                    f"{cprefix}target:{category}:"
                    f"{t.get('target_type','?')}"
                ),
            },
            note=f"{category}: {value}",
        ))

    # The blocking filter rule — only emit it when we actually
    # have targets. Empty-target policies become no-op even at
    # `fail_open=0` (we refuse to invent a default-drop rule).
    filter_ops: list[PlanCommand] = []
    if address_list_ops:
        fattrs: dict[str, str] = {
            "chain": "forward",
            "action": "drop",
            "dst-address-list": al_name,
            "comment": f"{cprefix}rule:block",
        }
        if schedule_time_value:
            fattrs["time"] = schedule_time_value
        filter_ops.append(PlanCommand(
            section="filter",
            path="/ip/firewall/filter",
            kind="add",
            attrs=fattrs,
            note=f"drop forward → {al_name}",
        ))

    warnings: list[str] = []
    fail_open = bool(policy.get("fail_open", 1))
    if not address_list_ops:
        if fail_open:
            warnings.append(
                "لا توجد وجهات نشطة — السياسة بدون أثر "
                "(fail-open)."
            )
        else:
            warnings.append(
                "لا توجد وجهات نشطة — رفضنا توليد قاعدة "
                "drop-default لتفادي قطع كامل الإنترنت."
            )

    notes = tuple(skipped_notes)
    return ScriptPlan(
        service=SERVICE, policy_id=pid,
        comment_prefix=cprefix,
        cleanup_ops=cleanup_ops,
        address_list_ops=tuple(address_list_ops),
        filter_ops=tuple(filter_ops),
        rollback_ops=rollback_ops,
        warnings=tuple(warnings),
        notes=notes,
    )


# ─── Cleanup pair ────────────────────────────────────────────


def _cleanup_pair(pid: int) -> tuple[
    tuple[PlanCommand, ...], tuple[PlanCommand, ...]
]:
    fp = cleanup_regex(SERVICE, pid)
    ops = (
        PlanCommand(
            section="cleanup",
            path="/ip/firewall/filter",
            kind="remove",
            find_pattern=fp,
            note="remove prior filter rule for this policy",
        ),
        PlanCommand(
            section="cleanup",
            path="/ip/firewall/address-list",
            kind="remove",
            find_pattern=fp,
            note="remove prior managed address-list entries",
        ),
    )
    return ops, ops


__all__ = [
    "SERVICE", "address_list_name", "plan",
]
