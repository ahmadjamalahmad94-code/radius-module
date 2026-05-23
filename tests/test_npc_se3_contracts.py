"""NPC Safe-Execution Phase 3 — execution contracts engine."""
from __future__ import annotations

import pytest

from app.radius.services import npc_execution_contracts as ec


# ─── Builders ───────────────────────────────────────────────


def _good_inputs(**overrides) -> ec.ContractInputs:
    """A baseline `ContractInputs` that passes every contract.
    Tests override single fields to exercise specific
    branches."""
    base = dict(
        policy_id=42,
        policy_type="web_block",
        target_router_ids=(1,),
        all_routers_targeted=False,
        offline_router_ids=(),
        has_preview=True,
        preview_hash="abc",
        expected_preview_hash="abc",
        preview_at="2026-05-23T10:00:00Z",
        policy_updated_at="2026-05-23T09:00:00Z",
        forward_script=(
            "# header\n"
            "/ip/firewall/address-list add "
            "list=HOBE_NPC_BLOCK_1 address=tiktok.com "
            "comment=\"HOBE_NPC_BLOCK:1:target\"\n"
            "/ip/firewall/filter add chain=forward "
            "action=accept dst-address-list=HOBE_NPC_BLOCK_1 "
            "comment=\"HOBE_NPC_BLOCK:1:rule\"\n"
            "/ip/firewall/filter remove "
            "[find comment~\"^HOBE_NPC_BLOCK:1:\"]\n"
        ),
        rollback_script=(
            "/ip/firewall/filter remove "
            "[find comment~\"^HOBE_NPC_BLOCK:1:\"]\n"
        ),
        render_error="",
        impact_risk_level="low",
        health_grade="good",
        blast_radius="small",
        blast_estimated_users=None,
        conflict_high_count=0,
        dependency_any_uncertain=False,
        rollback_available=True,
        canary_strategy="full",
        snapshot_id=99,
        actor_has_apply_perm=True,
        confirmations_provided=(),
        canary_opt_in=False,
    )
    base.update(overrides)
    return ec.ContractInputs(**base)


def _block_codes(decision: ec.ContractDecision) -> set[str]:
    return {b.code for b in decision.blockers}


def _warn_codes(decision: ec.ContractDecision) -> set[str]:
    return {w.code for w in decision.warnings}


# ─── Best case ──────────────────────────────────────────────


def test_baseline_inputs_are_ready():
    d = ec.evaluate(_good_inputs())
    assert d.ready
    assert d.blockers == ()
    assert d.reason_ar
    assert d.recommended_mode == ec.MODE_FULL


# ─── Every blocker ──────────────────────────────────────────


def test_missing_apply_permission_blocks():
    d = ec.evaluate(_good_inputs(actor_has_apply_perm=False))
    assert not d.ready
    assert ec.BLOCK_MISSING_APPLY_PERM in _block_codes(d)


def test_no_preview_blocks():
    d = ec.evaluate(_good_inputs(
        has_preview=False, forward_script="",
    ))
    assert ec.BLOCK_NO_VALID_PREVIEW in _block_codes(d)


def test_stale_preview_blocks():
    d = ec.evaluate(_good_inputs(
        preview_at="2026-05-23T09:00:00Z",
        policy_updated_at="2026-05-23T11:00:00Z",
    ))
    assert ec.BLOCK_PREVIEW_STALE in _block_codes(d)


def test_preview_hash_mismatch_blocks():
    d = ec.evaluate(_good_inputs(
        preview_hash="abc", expected_preview_hash="xyz",
    ))
    assert ec.BLOCK_PREVIEW_HASH_MISMATCH in _block_codes(d)


def test_no_rollback_blocks():
    d = ec.evaluate(_good_inputs(
        rollback_available=False, rollback_script="",
    ))
    assert ec.BLOCK_NO_ROLLBACK in _block_codes(d)


def test_no_snapshot_blocks():
    d = ec.evaluate(_good_inputs(snapshot_id=None))
    assert ec.BLOCK_NO_SNAPSHOT in _block_codes(d)


def test_empty_target_routers_blocks():
    d = ec.evaluate(_good_inputs(target_router_ids=()))
    assert ec.BLOCK_NO_TARGET_ROUTERS in _block_codes(d)


def test_critical_risk_blocks():
    d = ec.evaluate(_good_inputs(impact_risk_level="critical"))
    assert ec.BLOCK_CRITICAL_RISK in _block_codes(d)


def test_dangerous_health_blocks():
    d = ec.evaluate(_good_inputs(health_grade="dangerous"))
    assert ec.BLOCK_DANGEROUS_HEALTH in _block_codes(d)


def test_high_conflict_blocks():
    d = ec.evaluate(_good_inputs(conflict_high_count=2))
    assert ec.BLOCK_CRITICAL_CONFLICT in _block_codes(d)


def test_render_error_blocks_as_unsafe_script():
    d = ec.evaluate(_good_inputs(
        render_error="tripwire `password=` detected",
    ))
    assert ec.BLOCK_UNSAFE_SCRIPT in _block_codes(d)


def test_secret_like_content_blocks():
    d = ec.evaluate(_good_inputs(
        forward_script=(
            "/user set admin password=hunter2\n"
        ),
    ))
    assert ec.BLOCK_SECRET_LIKE_CONTENT in _block_codes(d)


def test_unmanaged_deletion_blocks():
    d = ec.evaluate(_good_inputs(
        forward_script=(
            "/ip/firewall/filter remove "
            "[find comment~\"tikok\"]\n"
        ),
    ))
    assert ec.BLOCK_UNMANAGED_DELETION in _block_codes(d)


def test_offline_router_blocks():
    d = ec.evaluate(_good_inputs(
        target_router_ids=(1, 2),
        offline_router_ids=(2,),
    ))
    assert ec.BLOCK_TARGET_ROUTER_OFFLINE in _block_codes(d)


def test_all_routers_without_canary_opt_in_blocks():
    d = ec.evaluate(_good_inputs(
        all_routers_targeted=True,
        canary_opt_in=False,
    ))
    assert ec.BLOCK_ALL_ROUTERS_WITHOUT_CANARY in _block_codes(d)


def test_missing_required_confirmation_blocks():
    """A large-blast plan requires CONFIRM_LARGE_BLAST.
    Without it, contracts produce BLOCK_MISSING_CONFIRMATION
    until the operator ticks the box."""
    d = ec.evaluate(_good_inputs(
        blast_radius="large",
        confirmations_provided=(),
    ))
    assert ec.BLOCK_MISSING_CONFIRMATION in _block_codes(d)
    assert ec.CONFIRM_LARGE_BLAST in d.required_confirmations


# ─── Warnings ───────────────────────────────────────────────


def test_high_risk_warns_only():
    d = ec.evaluate(_good_inputs(impact_risk_level="high"))
    assert ec.WARN_HIGH_RISK in _warn_codes(d)
    # No critical-risk blocker.
    assert ec.BLOCK_CRITICAL_RISK not in _block_codes(d)


def test_medium_risk_warns_only():
    d = ec.evaluate(_good_inputs(impact_risk_level="medium"))
    assert ec.WARN_MEDIUM_RISK in _warn_codes(d)


def test_dependency_uncertainty_warns_and_requires_confirm():
    d = ec.evaluate(_good_inputs(
        dependency_any_uncertain=True,
        confirmations_provided=(ec.CONFIRM_DEPENDENCY_IMPACT,),
    ))
    assert ec.WARN_DEPENDENCY_UNCERTAINTY in _warn_codes(d)
    assert d.ready  # confirmation provided


def test_canary_recommended_warns_only():
    d = ec.evaluate(_good_inputs(canary_strategy="canary"))
    assert ec.WARN_CANARY_RECOMMENDED in _warn_codes(d)


def test_estimated_users_warns():
    d = ec.evaluate(_good_inputs(blast_estimated_users=120))
    assert ec.WARN_ESTIMATED_USERS_HEURISTIC in _warn_codes(d)


def test_large_blast_warns_and_requires_confirm():
    d = ec.evaluate(_good_inputs(blast_radius="large"))
    assert ec.WARN_LARGE_BLAST in _warn_codes(d)
    # Without the confirmation it must be blocked.
    assert ec.BLOCK_MISSING_CONFIRMATION in _block_codes(d)
    assert ec.CONFIRM_LARGE_BLAST in d.required_confirmations


# ─── Required confirmations cycle ───────────────────────────


def test_firewall_drop_requires_confirm():
    d = ec.evaluate(_good_inputs(
        forward_script=(
            "/ip/firewall/filter add chain=forward "
            "action=drop dst-address-list=X "
            "comment=\"HOBE_NPC_BLOCK:1:rule\"\n"
        ),
    ))
    assert ec.CONFIRM_FIREWALL_DROP in d.required_confirmations
    # Without confirm, blocked.
    assert ec.BLOCK_MISSING_CONFIRMATION in _block_codes(d)


def test_all_router_scope_requires_confirm():
    # Provide canary opt-in to clear BLOCK_ALL_ROUTERS_WITHOUT_CANARY
    d = ec.evaluate(_good_inputs(
        all_routers_targeted=True,
        canary_opt_in=True,
        confirmations_provided=(ec.CONFIRM_CANARY_BYPASS,),
    ))
    # CONFIRM_ALL_ROUTER_SCOPE is required when all-routers.
    assert ec.CONFIRM_ALL_ROUTER_SCOPE in d.required_confirmations
    assert ec.BLOCK_MISSING_CONFIRMATION in _block_codes(d)
    # Provide it → ready.
    d2 = ec.evaluate(_good_inputs(
        all_routers_targeted=True,
        canary_opt_in=True,
        confirmations_provided=(
            ec.CONFIRM_CANARY_BYPASS,
            ec.CONFIRM_ALL_ROUTER_SCOPE,
        ),
    ))
    assert d2.ready, d2.blockers


def test_no_bypass_for_missing_rollback():
    """Required-confirmations cannot 'override' missing
    rollback — confirmations are for soft surfaces only."""
    d = ec.evaluate(_good_inputs(
        rollback_available=False,
        confirmations_provided=(
            ec.CONFIRM_LARGE_BLAST,
            ec.CONFIRM_FIREWALL_DROP,
            ec.CONFIRM_ALL_ROUTER_SCOPE,
            ec.CONFIRM_DEPENDENCY_IMPACT,
            ec.CONFIRM_CANARY_BYPASS,
        ),
    ))
    assert ec.BLOCK_NO_ROLLBACK in _block_codes(d)
    assert not d.ready


def test_no_bypass_for_missing_snapshot():
    d = ec.evaluate(_good_inputs(
        snapshot_id=None,
        confirmations_provided=(
            ec.CONFIRM_LARGE_BLAST,
            ec.CONFIRM_FIREWALL_DROP,
            ec.CONFIRM_ALL_ROUTER_SCOPE,
            ec.CONFIRM_DEPENDENCY_IMPACT,
            ec.CONFIRM_CANARY_BYPASS,
        ),
    ))
    assert ec.BLOCK_NO_SNAPSHOT in _block_codes(d)


def test_no_bypass_for_unsafe_script():
    d = ec.evaluate(_good_inputs(
        forward_script=(
            "/user add password=hunter2\n"
        ),
        confirmations_provided=(
            ec.CONFIRM_LARGE_BLAST,
            ec.CONFIRM_FIREWALL_DROP,
            ec.CONFIRM_ALL_ROUTER_SCOPE,
            ec.CONFIRM_DEPENDENCY_IMPACT,
            ec.CONFIRM_CANARY_BYPASS,
        ),
    ))
    assert ec.BLOCK_SECRET_LIKE_CONTENT in _block_codes(d)


# ─── Modes ──────────────────────────────────────────────────


def test_recommended_mode_canary_for_canary_strategy():
    d = ec.evaluate(_good_inputs(canary_strategy="canary"))
    assert d.recommended_mode == ec.MODE_CANARY


def test_recommended_mode_hold_blocks_modes():
    d = ec.evaluate(_good_inputs(canary_strategy="hold"))
    assert d.recommended_mode == ec.MODE_HOLD
    # No allowed modes when hold.
    assert d.execution_modes_allowed == ()


def test_recommended_mode_for_medium_blast():
    d = ec.evaluate(_good_inputs(blast_radius="medium"))
    assert d.recommended_mode == ec.MODE_STAGED


# ─── JSON projection ────────────────────────────────────────


def test_decision_as_dict_shape():
    d = ec.evaluate(_good_inputs(blast_radius="large"))
    out = d.as_dict()
    assert isinstance(out["blockers"], list)
    assert isinstance(out["warnings"], list)
    assert isinstance(out["required_confirmations"], list)
    assert "ready" in out
    assert "reason_ar" in out
    # Each issue is a dict with code/severity/message_ar.
    if out["blockers"]:
        b0 = out["blockers"][0]
        assert set(b0.keys()) == {
            "code", "severity", "message_ar",
        }


# ─── Readiness orchestrator (the route-facing wrapper) ──────


def _stub_intelligence():
    """Minimal stubs for the upstream intelligence dataclasses.
    The orchestrator only reads a handful of attributes."""
    from app.radius.services.npc_blast_radius import BlastRadius
    from app.radius.services.npc_canary_planner import CanaryPlan
    from app.radius.services.npc_conflict_detector import (
        ConflictAnalysis,
    )
    from app.radius.services.npc_dependency_detector import (
        DependencyAnalysis,
    )
    from app.radius.services.npc_impact_analyzer import (
        ImpactAnalysis,
    )
    from app.radius.services.npc_policy_health import HealthScore
    return dict(
        impact=ImpactAnalysis(
            summary_ar="s", beginner_explanation_ar="b",
            technical_summary_ar="t",
            affected_services=("web_block",),
            affected_router_count=1, change_count=2,
            changes_summary={},
            warnings_ar=(),
            rollback_available=True,
            rollback_explanation_ar="r",
            risk_level="low",
            risk_reasons_ar=(),
        ),
        conflicts=ConflictAnalysis(
            has_conflicts=False, severity="low", conflicts=(),
        ),
        dependencies=DependencyAnalysis(),
        blast=BlastRadius(
            affected_router_count=1, estimated_user_count=None,
            estimated_profile_count=None,
            blast_radius="small",
            recommendation_ar="r", heuristic_note_ar="n",
        ),
        health=HealthScore(
            score=90, grade="good",
            positives_ar=(), negatives_ar=(),
            reasoning_ar="r", is_advisory=True,
        ),
        canary=CanaryPlan(
            recommended_strategy="full",
            steps=("backup",),
            wait_time_recommendation_ar="t",
            rollback_checkpoint_required=False,
            recommendation_ar="r",
        ),
    )


def test_readiness_orchestrator_uses_contracts_engine(app=None):
    """The route-facing wrapper just composes ContractInputs
    and delegates. Smoke: same blocker codes surface."""
    from app.radius.services import (
        npc_execution_readiness as rdy,
    )
    intel = _stub_intelligence()
    # Preview-time: no snapshot, no apply perm.
    out = rdy.evaluate_for_preview(
        policy={"id": 5, "router_id": 1,
                 "updated_at": "2026-01-01T00:00:00Z"},
        policy_type="web_block",
        forward_script="# no body\n",
        rollback_script="",
        render_error="",
        apply_perm="npc.web_block.apply",
        actor_has_apply_perm=False,
        **intel,
    )
    codes = {b.code for b in out.decision.blockers}
    # No snapshot + no apply perm → both blockers present.
    assert ec.BLOCK_NO_SNAPSHOT in codes
    assert ec.BLOCK_MISSING_APPLY_PERM in codes
    # The dict projection is JSON-friendly.
    d = out.as_dict()
    assert "decision" in d
    assert "checklist_ar" in d
    assert isinstance(d["checklist_ar"], list)
