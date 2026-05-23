"""npc_execution_readiness — thin orchestrator that turns the
upstream intelligence + policy state into `ContractInputs`,
calls the contracts engine, and returns the decision plus a
template-friendly projection.

Pure module: no DB, no Flask. The route layer is responsible
for loading the policy, children, latest preview, and actor
permissions, then calling `evaluate_for_preview(...)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from . import npc_execution_contracts as ec
from .npc_blast_radius import BlastRadius
from .npc_canary_planner import CanaryPlan
from .npc_conflict_detector import ConflictAnalysis
from .npc_dependency_detector import DependencyAnalysis
from .npc_impact_analyzer import ImpactAnalysis
from .npc_policy_health import HealthScore


@dataclass(frozen=True)
class Readiness:
    decision: ec.ContractDecision
    # Pre-rendered view-model the template can iterate:
    blockers_ar: tuple[str, ...]
    warnings_ar: tuple[str, ...]
    checklist_ar: tuple[dict, ...]
    apply_perm: str
    apply_perm_label_ar: str
    caveat_ar: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision":             self.decision.as_dict(),
            "ready_for_future_apply": self.decision.ready,
            "blockers_ar":          list(self.blockers_ar),
            "warnings_ar":          list(self.warnings_ar),
            "checklist_ar":         [dict(c) for c in self.checklist_ar],
            "apply_perm":           self.apply_perm,
            "apply_perm_label_ar":  self.apply_perm_label_ar,
            "caveat_ar":            self.caveat_ar,
        }


def evaluate_for_preview(
    *,
    policy: dict,
    policy_type: str,
    impact: ImpactAnalysis,
    conflicts: ConflictAnalysis,
    dependencies: DependencyAnalysis,
    blast: BlastRadius,
    health: HealthScore,
    canary: CanaryPlan,
    forward_script: str,
    rollback_script: str,
    render_error: str,
    apply_perm: str,
    actor_has_apply_perm: bool,
    # Optional — populated by the apply route:
    snapshot_id: Optional[int] = None,
    expected_preview_hash: str = "",
    preview_hash: str = "",
    preview_at: str = "",
    confirmations_provided: tuple[str, ...] = (),
    canary_opt_in: bool = False,
    target_router_ids: Optional[tuple[int, ...]] = None,
    offline_router_ids: tuple[int, ...] = (),
    all_routers_targeted: bool = False,
) -> Readiness:
    """Run the contracts engine over the supplied intelligence.

    For the *preview-time* readiness card the caller leaves
    `snapshot_id=None`, `actor_has_apply_perm=False` (we don't
    know yet), `target_router_ids=None`. The card therefore
    surfaces the future blockers an operator would face but
    doesn't pretend apply is currently allowed.
    """
    if target_router_ids is None:
        rid = int(policy.get("router_id") or 0)
        target_router_ids = (rid,) if rid else ()

    inputs = ec.ContractInputs(
        policy_id=int(policy.get("id") or 0),
        policy_type=policy_type,
        target_router_ids=tuple(target_router_ids),
        all_routers_targeted=all_routers_targeted,
        offline_router_ids=tuple(offline_router_ids),
        has_preview=bool(forward_script.strip())
                     and not render_error,
        preview_hash=preview_hash,
        expected_preview_hash=expected_preview_hash,
        preview_at=preview_at,
        policy_updated_at=str(policy.get("updated_at") or ""),
        forward_script=forward_script,
        rollback_script=rollback_script,
        render_error=render_error,
        impact_risk_level=impact.risk_level,
        health_grade=health.grade,
        blast_radius=blast.blast_radius,
        blast_estimated_users=blast.estimated_user_count,
        conflict_high_count=sum(
            1 for c in conflicts.conflicts
            if c.severity == "high"
        ),
        dependency_any_uncertain=bool(
            dependencies.dependencies
            and any(d.confidence != "certain"
                    for d in dependencies.dependencies)
        ),
        rollback_available=bool(impact.rollback_available),
        canary_strategy=canary.recommended_strategy,
        snapshot_id=snapshot_id,
        actor_has_apply_perm=actor_has_apply_perm,
        confirmations_provided=tuple(confirmations_provided),
        canary_opt_in=canary_opt_in,
    )
    decision = ec.evaluate(inputs)

    # Project to a UI-friendly view-model.
    blockers_ar = tuple(b.message_ar for b in decision.blockers)
    warnings_ar = tuple(w.message_ar for w in decision.warnings)

    # Checklist surfaces the 5 quick-glance gates the
    # operator should be able to read at a glance.
    high_conflicts_ok = inputs.conflict_high_count == 0
    checklist = (
        {"label": "السكربت forward موجود ومُولَّد بنجاح.",
         "status_ok": inputs.has_preview},
        {"label": "سكربت rollback متاح.",
         "status_ok": bool(inputs.rollback_available)},
        {"label": "تحليل الأثر ليس في خانة critical.",
         "status_ok": inputs.impact_risk_level != "critical"},
        {"label": "درجة السلامة فوق خط الخطر.",
         "status_ok": inputs.health_grade != "dangerous"},
        {"label": "لا تعارضات عالية الخطورة مع سياسات أخرى.",
         "status_ok": high_conflicts_ok},
    )

    return Readiness(
        decision=decision,
        blockers_ar=blockers_ar,
        warnings_ar=warnings_ar,
        checklist_ar=checklist,
        apply_perm=apply_perm,
        apply_perm_label_ar=apply_perm,
        caveat_ar=(
            "التنفيذ مرحلة لاحقة — هذه شاشة معاينة وتحليل فقط."
        ),
    )


__all__ = ["Readiness", "evaluate_for_preview"]
