"""site_exit_safety — VX2.5 pre-apply safety check (pure).

Combines every Phase O foundation that matters to a site-exit
apply into a single dataclass the route can render:

  - admin permission (mt_permissions)
  - router scope + health  (mt_router_overview)
  - backup freshness        (mt_router_overview's backup_status)
  - VPS exit-node sanity    (vps_exit_nodes_repo + planner)
  - plan correctness        (planner.build_plan)
  - FastTrack advisory      (planner.FASTTRACK_WARNING_AR)
  - risky-group permission  (classifier.DEFAULT_DISABLED_GROUPS)

It does NOT contact the live router and never mutates DB state
— it's a read-only "may I proceed?" oracle. The route layer
turns its `allowed=False` into a refusal + flash; `True` lets
the route call the actual mt_programming.apply_commands path
(VX2.6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from . import (
    site_exit_classifier      as classifier,
    site_exit_script_planner  as planner,
    site_exit_script_renderer as renderer,
)
from .mt_permissions import (
    PERM_SITE_EXIT_APPLY,
    PERM_SITE_EXIT_ENABLE_RISKY_GROUPS,
    PERM_SITE_EXIT_OVERRIDE_BACKUP_WARNING,
    PERM_SITE_EXIT_VIEW,
    admin_permissions,
)
from .mt_router_overview import build_overview


# Severity ladder. Keep these strings stable — UI keys on them.
SEV_BLOCKED  = "blocked"
SEV_CRITICAL = "critical"
SEV_WARNING  = "warning"
SEV_INFO     = "info"

_SEV_ORDER = {
    SEV_INFO: 0, SEV_WARNING: 1,
    SEV_CRITICAL: 2, SEV_BLOCKED: 3,
}


@dataclass(frozen=True)
class SiteExitSafetyResult:
    allowed: bool
    severity: str
    blocking_reasons:       tuple[str, ...] = ()
    warnings:               tuple[str, ...] = ()
    required_confirmations: tuple[str, ...] = ()
    recommended_actions:    tuple[str, ...] = ()
    # Surface the key signals the UI cares about — saves the
    # template a second round-trip through the planner.
    backup_status:    str = ""           # fresh|stale|missing|unknown
    router_health:    str = ""           # healthy|attention|risky|offline|unknown
    vps_health:       str = ""           # ok|degraded|down|unknown|""
    fasttrack_warning: str = ""
    script_hash:      str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed":   self.allowed,
            "severity":  self.severity,
            "blocking_reasons":       list(self.blocking_reasons),
            "warnings":               list(self.warnings),
            "required_confirmations": list(self.required_confirmations),
            "recommended_actions":    list(self.recommended_actions),
            "backup_status":   self.backup_status,
            "router_health":   self.router_health,
            "vps_health":      self.vps_health,
            "fasttrack_warning": self.fasttrack_warning,
            "script_hash":     self.script_hash,
        }


# The five confirmations the apply route requires. Stable names
# so the UI can render checkboxes with matching values.
REQUIRED_CONFIRMATIONS = (
    "confirm_preview_seen",
    "confirm_backup_status",
    "confirm_vps_exit_understood",
    "confirm_fail_mode_understood",
    "confirm_selected_sites_only",
)


def _worse(a: str, b: str) -> str:
    return a if _SEV_ORDER[a] >= _SEV_ORDER[b] else b


def _bump(severity: str, level: str) -> str:
    return _worse(severity, level)


def evaluate(
    *,
    tenant_id: int,
    nas_id: int,
    admin: Any,
    policy: dict,
    exit_node: Optional[dict],
    targets: Iterable[dict],
    wan_interface_list: Optional[str] = None,
    enable_dns_helper: bool = False,
    backup_override_acknowledged: bool = False,
) -> SiteExitSafetyResult:
    """Single entry point. Returns SiteExitSafetyResult; never
    raises for operator-fixable conditions."""
    severity = SEV_INFO
    blocking:  list[str] = []
    warnings:  list[str] = []
    recs:      list[str] = []

    # ─── 1. Permission ───────────────────────────────────────
    held = admin_permissions(admin) if admin is not None else frozenset()
    if PERM_SITE_EXIT_VIEW not in held:
        return SiteExitSafetyResult(
            allowed=False, severity=SEV_BLOCKED,
            blocking_reasons=(
                "missing site_exit.view permission — cannot"
                " evaluate site-exit apply.",
            ),
        )
    if PERM_SITE_EXIT_APPLY not in held:
        blocking.append(
            "missing site_exit.apply permission — operator"
            " can preview but cannot apply."
        )
        severity = _bump(severity, SEV_BLOCKED)

    # ─── 2. Router scope + health (Phase O overview) ─────────
    overview = build_overview(tenant_id=int(tenant_id),
                                nas_id=int(nas_id))
    router_health = ""
    backup_status = ""
    if overview is None:
        blocking.append(
            f"router {nas_id} not found in tenant scope.")
        severity = _bump(severity, SEV_BLOCKED)
    else:
        # O2 health score (only import lazily so the safety
        # module stays a thin composer).
        from .mt_health_score import score_health
        hs = score_health(overview)
        router_health = hs.state
        backup_status = overview.backup_status or "unknown"
        if hs.state == "offline":
            blocking.append(
                "router health is OFFLINE — apply is refused;"
                " bring the router back online first."
            )
            severity = _bump(severity, SEV_BLOCKED)
        elif hs.state == "risky":
            warnings.append(
                f"router health is RISKY ({hs.score}/100) —"
                " review the overview page before applying."
            )
            severity = _bump(severity, SEV_WARNING)
            recs.append(
                f"open /admin/radius/mt/{nas_id}/overview")

    # ─── 3. VPS exit node ────────────────────────────────────
    vps_health = "unknown"
    if not exit_node:
        blocking.append(
            "exit_node is missing — create a VPS exit node"
            " for this policy first."
        )
        severity = _bump(severity, SEV_BLOCKED)
    else:
        if not exit_node.get("enabled"):
            blocking.append(
                "VPS exit node is disabled — enable it on"
                " the site-exit page before applying."
            )
            severity = _bump(severity, SEV_BLOCKED)
        if not (exit_node.get("wireguard_interface_name") or "").strip():
            blocking.append(
                "VPS exit node has no wireguard_interface_name"
                " — cannot route to the tunnel."
            )
            severity = _bump(severity, SEV_BLOCKED)
        vps_health = (
            (exit_node.get("last_health_status") or "").strip()
            or "unknown"
        )
        if vps_health == "down":
            warnings.append(
                "VPS exit node last_health_status is DOWN —"
                " applying now will silently fall back to WAN"
                " if fail_mode is fallback_to_wan."
            )
            severity = _bump(severity, SEV_WARNING)

    # ─── 4. Backup freshness ────────────────────────────────
    if backup_status == "fresh":
        # ideal — operator's safety net is recent.
        pass
    elif backup_status in {"missing", "unknown"}:
        if PERM_SITE_EXIT_OVERRIDE_BACKUP_WARNING in held \
                and backup_override_acknowledged:
            warnings.append(
                "no recent backup — operator explicitly"
                " acknowledged the override permission."
            )
            severity = _bump(severity, SEV_WARNING)
        else:
            blocking.append(
                "no recent backup — apply refused. Take a"
                " backup from /admin/radius/mt/{}/backups"
                " before retrying, or use the override"
                " permission if you understand the risk."
                .format(int(nas_id))
            )
            severity = _bump(severity, SEV_BLOCKED)
    elif backup_status == "stale":
        warnings.append(
            "the last backup is older than 7 days — consider"
            " taking a fresh one before applying."
        )
        severity = _bump(severity, SEV_WARNING)
        recs.append(
            f"take a new backup at /admin/radius/mt/{nas_id}/backups")

    # ─── 5. Risky groups need extra permission ──────────────
    risky_targets = [
        t for t in (targets or ())
        if (t.get("group_name") or "")
        in classifier.DEFAULT_DISABLED_GROUPS
        and (t.get("status") or "active") == "active"
    ]
    if risky_targets and \
       PERM_SITE_EXIT_ENABLE_RISKY_GROUPS not in held:
        blocking.append(
            f"{len(risky_targets)} active target(s) belong to"
            " a risky group (vpn_provider_pages /"
            " general_probe_sites / manual_review) — operator"
            " lacks site_exit.enable_risky_groups permission."
        )
        severity = _bump(severity, SEV_BLOCKED)

    # ─── 6. Plan correctness ────────────────────────────────
    plan = planner.build_plan(
        policy=policy,
        exit_node=exit_node or {"enabled": 0},
        targets=targets or (),
        wan_interface_list=wan_interface_list,
        enable_dns_helper=enable_dns_helper,
    )
    for err in plan.blocking_errors:
        blocking.append(f"planner: {err}")
        severity = _bump(severity, SEV_BLOCKED)
    fasttrack = ""
    for w in plan.warnings:
        warnings.append(f"planner: {w}")
        if "FastTrack" in w:
            fasttrack = w
        severity = _bump(severity, SEV_WARNING)

    # Defence-in-depth: scan the rendered script for a main-
    # table default route, even though the planner asserts it.
    script_hash = ""
    if plan.can_apply:
        try:
            body = renderer.render_forward_script(plan)
            script_hash = renderer.script_hash(body)
            for line in body.splitlines():
                if ("0.0.0.0/0" in line
                    and "routing-table=" in line
                    and f"routing-table={plan.routing_table}"
                        not in line):
                    blocking.append(
                        "rendered script contains a 0.0.0.0/0"
                        " route OUTSIDE the policy routing"
                        " table — refusing to apply."
                    )
                    severity = _bump(severity, SEV_BLOCKED)
                    break
        except renderer.RenderSafetyError as exc:
            blocking.append(
                f"render-safety refused the script: {exc}")
            severity = _bump(severity, SEV_BLOCKED)

    allowed = (not blocking) and (PERM_SITE_EXIT_APPLY in held)
    return SiteExitSafetyResult(
        allowed=allowed,
        severity=severity if blocking else _bump(severity, SEV_INFO),
        blocking_reasons=tuple(blocking),
        warnings=tuple(warnings),
        required_confirmations=REQUIRED_CONFIRMATIONS,
        recommended_actions=tuple(recs),
        backup_status=backup_status,
        router_health=router_health,
        vps_health=vps_health,
        fasttrack_warning=fasttrack,
        script_hash=script_hash,
    )


__all__ = [
    "SEV_BLOCKED", "SEV_CRITICAL", "SEV_WARNING", "SEV_INFO",
    "SiteExitSafetyResult",
    "REQUIRED_CONFIRMATIONS",
    "evaluate",
]
