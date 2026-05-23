"""NPC Phase I — smart recommendations."""
from __future__ import annotations

from app.radius.services import npc_recommendations as rec
from app.radius.services.npc_blast_radius import BlastRadius
from app.radius.services.npc_canary_planner import (
    CanaryPlan, STRATEGY_CANARY, STRATEGY_FULL,
    STRATEGY_HOLD, STRATEGY_STAGED,
)
from app.radius.services.npc_conflict_detector import (
    Conflict, ConflictAnalysis,
)
from app.radius.services.npc_dependency_detector import (
    Dependency, DependencyAnalysis,
)
from app.radius.services.npc_impact_analyzer import (
    ImpactAnalysis,
)


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_recommendations as m
    importlib.reload(m)
    assert m.ACTION_CANARY_FIRST == "canary_first"
    assert callable(m.build)


# ─── Builders ───────────────────────────────────────────────


def _impact(rollback=True):
    return ImpactAnalysis(
        summary_ar="s", beginner_explanation_ar="b",
        technical_summary_ar="t",
        affected_services=("web_block",),
        affected_router_count=1,
        change_count=2,
        changes_summary={"filter": 1},
        warnings_ar=(),
        rollback_available=rollback,
        rollback_explanation_ar="r",
        risk_level="low",
        risk_reasons_ar=(),
    )


def _conflicts(sev=None, n=0):
    if n == 0:
        return ConflictAnalysis(
            has_conflicts=False, severity="low", conflicts=(),
        )
    return ConflictAnalysis(
        has_conflicts=True, severity=sev or "high",
        conflicts=tuple(
            Conflict(kind="overlapping_router",
                     policy_id=10+i, policy_name=f"peer{i}",
                     service="web_block",
                     reason_ar="r", severity=sev or "high",
                     recommendation_ar="rec")
            for i in range(n)
        ),
    )


def _deps(n=0, all_certain=False):
    if n == 0:
        return DependencyAnalysis()
    conf = "certain" if all_certain else "likely"
    return DependencyAnalysis(
        dependencies=tuple(
            Dependency(
                service_name=f"svc{i}",
                impact_ar="i", confidence=conf,
                reason_ar="r",
                related_domains=("a.com", "b.com"),
            ) for i in range(n)
        ),
    )


def _blast(bucket="small", routers=1):
    return BlastRadius(
        affected_router_count=routers,
        estimated_user_count=None,
        estimated_profile_count=None,
        blast_radius=bucket,
        recommendation_ar="r",
        heuristic_note_ar="n",
    )


def _canary(strategy=STRATEGY_FULL):
    return CanaryPlan(
        recommended_strategy=strategy,
        steps=("backup",),
        wait_time_recommendation_ar="t",
        rollback_checkpoint_required=False,
        recommendation_ar="r",
    )


# ─── Empty / best case ──────────────────────────────────────


def test_best_case_few_recommendations():
    out = rec.build(
        impact=_impact(rollback=True),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small"),
        canary=_canary(STRATEGY_FULL),
    )
    # No critical recommendations; list may be empty.
    assert len(out.recommendations) == 0


# ─── Each rule fires when expected ──────────────────────────


def test_no_rollback_emits_create_rollback():
    out = rec.build(
        impact=_impact(rollback=False),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small"),
        canary=_canary(STRATEGY_FULL),
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_CREATE_ROLLBACK in actions


def test_conflict_emits_resolve_conflict():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(sev="high", n=2),
        dependencies=_deps(n=0),
        blast=_blast(),
        canary=_canary(STRATEGY_FULL),
    )
    actions = [r for r in out.recommendations
                if r.action_type == rec.ACTION_RESOLVE_CONFLICT]
    assert len(actions) == 2
    # Each conflict carries a related_policy_id.
    for r in actions:
        assert r.related_policy_id is not None


def test_dependencies_emit_review_deps():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=1, all_certain=False),
        blast=_blast(),
        canary=_canary(STRATEGY_FULL),
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_REVIEW_DEPS in actions
    # related_domains populated.
    review = next(r for r in out.recommendations
                   if r.action_type == rec.ACTION_REVIEW_DEPS)
    assert "a.com" in review.related_domains
    assert "b.com" in review.related_domains


def test_certain_dependencies_emit_add_related_for_web_block():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=1, all_certain=True),
        blast=_blast(),
        canary=_canary(STRATEGY_FULL),
        policy_type="web_block",
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_ADD_RELATED_DOMS in actions


def test_certain_dependencies_skip_add_related_for_walled_garden():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=1, all_certain=True),
        blast=_blast(),
        canary=_canary(STRATEGY_FULL),
        policy_type="walled_garden",
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_ADD_RELATED_DOMS not in actions


def test_canary_strategy_emits_canary_first():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="large", routers=8),
        canary=_canary(STRATEGY_CANARY),
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_CANARY_FIRST in actions


def test_hold_strategy_emits_hold_and_replan():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="critical", routers=10),
        canary=_canary(STRATEGY_HOLD),
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_HOLD_AND_REPLAN in actions


def test_large_blast_emits_limit_scope():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="large", routers=8),
        canary=_canary(STRATEGY_CANARY),
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_LIMIT_SCOPE in actions


def test_remote_access_without_expiry_emits_add_expiry():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(),
        canary=_canary(STRATEGY_FULL),
        policy_type="remote_access",
        policy={"expires_at": ""},
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_ADD_EXPIRY in actions


def test_remote_access_with_expiry_skips_add_expiry():
    out = rec.build(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(),
        canary=_canary(STRATEGY_FULL),
        policy_type="remote_access",
        policy={"expires_at": "2027-01-01T00:00:00Z"},
    )
    actions = {r.action_type for r in out.recommendations}
    assert rec.ACTION_ADD_EXPIRY not in actions


# ─── Sorting ────────────────────────────────────────────────


def test_recommendations_sorted_by_priority():
    out = rec.build(
        impact=_impact(rollback=False),          # priority 1
        conflicts=_conflicts(sev="high", n=1),    # priority 1
        dependencies=_deps(n=1, all_certain=False),  # priority 3
        blast=_blast(bucket="large", routers=8), # large + canary p2
        canary=_canary(STRATEGY_CANARY),
    )
    priorities = [r.priority for r in out.recommendations]
    assert priorities == sorted(priorities)


# ─── JSON projection ────────────────────────────────────────


def test_as_dict_shape():
    out = rec.build(
        impact=_impact(rollback=False),
        conflicts=_conflicts(sev="medium", n=1),
        dependencies=_deps(n=1, all_certain=True),
        blast=_blast(bucket="medium", routers=2),
        canary=_canary(STRATEGY_STAGED),
        policy_type="web_block",
    )
    d = out.as_dict()
    assert isinstance(d["recommendations"], list)
    item = d["recommendations"][0]
    assert set(item.keys()) >= {
        "title_ar", "explanation_ar", "action_type",
        "confidence", "priority", "related_policy_id",
        "related_domains",
    }
