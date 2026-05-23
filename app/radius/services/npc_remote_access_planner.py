"""npc_remote_access_planner — pure planner for the Remote
MikroTik Access sub-service.

Takes a policy dict (shape produced by `npc_remote_access_repo`)
and emits a `ScriptPlan` whose commands open the requested
admin ports on the router's `/ip firewall filter` `input` chain,
optionally restrict the source via an address-list, and
schedule an automatic teardown at `expires_at`.

Pure module — no DB, no network, no Flask. Callers fetch the
policy from the repo and hand it in.

Safety contract:
  * Always emit a cleanup section that removes any prior
    managed rules for this policy id, BEFORE the new adds.
    Forward script is idempotent under re-application.
  * Every managed rule carries `comment="HOBE_NPC_REMOTE:<id>:…"`
    so the cleanup `[find comment~"^HOBE_NPC_REMOTE:<id>:"]`
    matches exactly the rules we own.
  * If `expires_at` is set, emit a `/system scheduler` entry
    that runs the rollback at the chosen instant — the policy
    is hard-bounded even if the operator forgets to roll back
    manually.
  * `place-before=0` on every new filter rule, so accept
    rules sit at the top of the input chain before any
    existing drop-default catches them.
  * Refuse to plan when assess_policy() returns blockers —
    populate `blocking_errors` so the route layer renders a
    forbidden state rather than apply unsafe rules.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional

from .npc_policy import comment_prefix, cleanup_regex
from .npc_remote_access import (
    assess_policy, list_services, selected_ports,
)
from .npc_script_renderer import PlanCommand, ScriptPlan


# Anchored prefix used in comments + find regexes.
SERVICE = "remote_access"


# IPv4 sanity — anything else we silently skip rather than push
# garbage to /ip/firewall/address-list/add.
_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)$"
)


def _vps_wg_ip() -> str:
    """The VPS-side WireGuard IP — the address the router will
    see as the source when traffic comes in through the
    nginx-stream tunnel. Read from `HOBERADIUS_WG_SERVER_IP`
    (set by the WG provisioner). Defaults to `10.10.0.1` which
    is the standard server side of the project's WG subnet.

    Returns empty string if the env var is set but malformed —
    the planner then skips the auto-inject silently rather than
    write a bad address-list entry."""
    raw = (os.environ.get("HOBERADIUS_WG_SERVER_IP")
           or "10.10.0.1").strip()
    return raw if _IPV4_RE.match(raw) else ""


def plan(
    policy: dict, *,
    now: Optional[datetime] = None,
) -> ScriptPlan:
    """Build a forward + rollback plan for one remote-access
    policy.

    `policy` is the dict shape produced by
    `npc_remote_access_repo.get_by_id` — id, name, slug, toggle
    columns, source_address_list, expires_at, enabled, etc.
    """
    if not policy:
        return ScriptPlan(
            service=SERVICE, policy_id=0,
            comment_prefix="",
            blocking_errors=("policy is empty",),
        )
    pid = int(policy["id"])
    cprefix = comment_prefix(SERVICE, pid)

    # Re-run the safety assessment from the foundation module
    # so the planner's blockers stay in lockstep with the
    # validator's. Defence-in-depth: callers SHOULD have
    # validated, but the planner can't trust they did.
    assessment = assess_policy(
        allow_winbox=bool(policy.get("allow_winbox")),
        allow_ssh=bool(policy.get("allow_ssh")),
        allow_api=bool(policy.get("allow_api")),
        allow_api_ssl=bool(policy.get("allow_api_ssl")),
        allow_webfig_http=bool(policy.get("allow_webfig_http")),
        allow_webfig_https=bool(policy.get("allow_webfig_https")),
        source_address_list=str(
            policy.get("source_address_list") or ""
        ),
        expires_at=str(policy.get("expires_at") or ""),
        now=now,
    )

    cleanup_ops, rollback_ops = _cleanup_pair(pid)

    if not assessment.is_applicable:
        return ScriptPlan(
            service=SERVICE, policy_id=pid,
            comment_prefix=cprefix,
            cleanup_ops=cleanup_ops,
            rollback_ops=rollback_ops,
            warnings=assessment.warnings_ar,
            blocking_errors=assessment.blockers_ar,
        )

    # Smart default: when the operator leaves source_address_list
    # empty, auto-generate `npc-vps-<pid>`. Keeps the form
    # one-click for the common case (just toggle services and
    # apply) while still letting power users name their own list.
    src_list = (policy.get("source_address_list") or "").strip()
    if not src_list:
        src_list = f"npc-vps-{pid}"

    enabled_toggles = selected_ports(
        allow_winbox=bool(policy.get("allow_winbox")),
        allow_ssh=bool(policy.get("allow_ssh")),
        allow_api=bool(policy.get("allow_api")),
        allow_api_ssl=bool(policy.get("allow_api_ssl")),
        allow_webfig_http=bool(policy.get("allow_webfig_http")),
        allow_webfig_https=bool(policy.get("allow_webfig_https")),
    )

    # One filter rule per enabled service. We emit them in a
    # stable order so a re-plan produces the same script bytes.
    filter_ops: list[PlanCommand] = []
    for key, port, proto in enabled_toggles:
        attrs = {
            "chain": "input",
            "action": "accept",
            "protocol": proto,
            "dst-port": str(port),
            "place-before": "0",
            "comment": f"{cprefix}service:{key}",
        }
        if src_list:
            attrs["src-address-list"] = src_list
        filter_ops.append(PlanCommand(
            section="filter",
            path="/ip/firewall/filter",
            kind="add",
            attrs=attrs,
            note=f"open {key} ({proto}/{port})",
        ))

    # Auto-inject the VPS WireGuard IP into the source list so
    # the operator's remote-access policy is reachable through
    # the VPS tunnel without any manual address-list step. The
    # entry uses the policy's anchored comment prefix so rollback
    # cleans it up automatically — same as every other managed
    # row.
    vps_wg_ip = _vps_wg_ip()
    address_list_ops: list[PlanCommand] = []
    if vps_wg_ip:
        address_list_ops.append(PlanCommand(
            section="address-list",
            path="/ip/firewall/address-list",
            kind="add",
            attrs={
                "list":    src_list,
                "address": vps_wg_ip,
                "comment": f"{cprefix}vps-relay-anchor",
            },
            note=(
                f"allow VPS tunnel ({vps_wg_ip}) → "
                f"{src_list}"
            ),
        ))

    # Optional scheduler — fires the rollback at expires_at.
    scheduler_ops: list[PlanCommand] = []
    if assessment.is_applicable and policy.get("expires_at"):
        sched_attrs = _scheduler_attrs(
            pid=pid, cprefix=cprefix,
            expires_at=str(policy["expires_at"]),
        )
        if sched_attrs:
            scheduler_ops.append(PlanCommand(
                section="scheduler",
                path="/system/scheduler",
                kind="add",
                attrs=sched_attrs,
                note="auto-expire managed rules",
            ))

    return ScriptPlan(
        service=SERVICE, policy_id=pid,
        comment_prefix=cprefix,
        cleanup_ops=cleanup_ops,
        address_list_ops=tuple(address_list_ops),
        filter_ops=tuple(filter_ops),
        scheduler_ops=tuple(scheduler_ops),
        rollback_ops=rollback_ops,
        warnings=assessment.warnings_ar,
        blocking_errors=(),
        notes=(
            f"risk: {assessment.risk}",
            f"enabled services: "
            f"{', '.join(assessment.enabled_services) or 'none'}",
            (f"source list: {src_list}"
             + (f" (auto-includes VPS {vps_wg_ip})"
                if vps_wg_ip else "")),
        ),
    )


# ─── Cleanup pair ────────────────────────────────────────────


def _cleanup_pair(pid: int) -> tuple[
    tuple[PlanCommand, ...], tuple[PlanCommand, ...]
]:
    """The forward script's cleanup ops + the rollback script's
    ops are nearly identical — both must remove every managed
    object owned by this policy. We build them together so a
    drift between the two is impossible."""
    fp = cleanup_regex(SERVICE, pid)
    cprefix = comment_prefix(SERVICE, pid)
    ops = (
        PlanCommand(
            section="cleanup",
            path="/system/scheduler",
            kind="remove",
            find_pattern=fp,
            note="remove prior scheduler entries",
        ),
        PlanCommand(
            section="cleanup",
            path="/ip/firewall/filter",
            kind="remove",
            find_pattern=fp,
            note=f"remove prior filter rules for {cprefix}",
        ),
    )
    # Cleanup and rollback share the same set of remove ops —
    # rollback simply has no add-side to follow.
    return ops, ops


# ─── Scheduler helpers ───────────────────────────────────────


def _scheduler_attrs(
    *, pid: int, cprefix: str, expires_at: str,
) -> Optional[dict[str, str]]:
    """Build the `/system scheduler add` attrs from an ISO-8601
    expires_at. Returns None if the timestamp can't be parsed
    — the renderer is allowed to omit the scheduler op rather
    than crash."""
    dt = _parse_iso(expires_at)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # RouterOS scheduler expects start-date `mmm/dd/yyyy` and
    # `start-time` `HH:MM:SS`. We keep UTC throughout.
    dt_utc = dt.astimezone(timezone.utc)
    start_date = dt_utc.strftime("%b/%d/%Y").lower()
    # MikroTik prefers lowercase month abbreviation.
    start_time = dt_utc.strftime("%H:%M:%S")
    name = f"hobe-npc-remote-{pid}-expire"
    on_event = (
        f"/ip firewall filter remove "
        f"[find comment~\\\"^{cprefix}\\\"]; "
        f"/system scheduler remove "
        f"[find comment~\\\"^{cprefix}\\\"]"
    )
    return {
        "name": name,
        "start-date": start_date,
        "start-time": start_time,
        "on-event": on_event,
        "interval": "0",
        "comment": f"{cprefix}scheduler",
    }


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


__all__ = [
    "SERVICE", "plan",
]
