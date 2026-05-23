"""NPC Phase F — policy health score."""
from __future__ import annotations

from app.radius.services import npc_policy_health as ph
from app.radius.services.npc_blast_radius import BlastRadius
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
    import app.radius.services.npc_policy_health as m
    importlib.reload(m)
    assert m.GRADE_EXCELLENT == "excellent"
    assert callable(m.compute)


# ─── Builders ────────────────────────────────────────────────


def _impact(risk_level="low", rollback=True):
    return ImpactAnalysis(
        summary_ar="s", beginner_explanation_ar="b",
        technical_summary_ar="t",
        affected_services=("web_block",),
        affected_router_count=1,
        change_count=2,
        changes_summary={"cleanup": 1, "filter": 1},
        warnings_ar=(),
        rollback_available=rollback,
        rollback_explanation_ar="r",
        risk_level=risk_level,
        risk_reasons_ar=(),
    )


def _conflicts(*, sev="low", n=0):
    if n == 0:
        return ConflictAnalysis(
            has_conflicts=False, severity="low", conflicts=(),
        )
    return ConflictAnalysis(
        has_conflicts=True, severity=sev,
        conflicts=tuple(
            Conflict(kind="overlapping_router",
                     policy_id=i+1, policy_name=f"p{i+1}",
                     service="web_block",
                     reason_ar="r", severity=sev,
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
            ) for i in range(n)
        ),
        warnings_ar=("note",),
    )


def _blast(bucket="small", routers=1):
    return BlastRadius(
        affected_router_count=routers,
        estimated_user_count=None,
        estimated_profile_count=None,
        blast_radius=bucket,
        recommendation_ar="rec",
        heuristic_note_ar="note",
    )


# ─── Best case ──────────────────────────────────────────────


def test_best_case_excellent():
    out = ph.compute(
        impact=_impact(risk_level="low", rollback=True),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    )
    assert out.grade == ph.GRADE_EXCELLENT
    assert out.score >= 90
    assert out.is_advisory is True
    assert out.positives_ar  # at least one positive


# ─── Each deduction lowers grade as expected ────────────────


def test_high_impact_drops_to_caution_or_below():
    out = ph.compute(
        impact=_impact(risk_level="high", rollback=True),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    )
    assert out.grade in (
        ph.GRADE_CAUTION, ph.GRADE_GOOD, ph.GRADE_RISKY,
    )


def test_critical_impact_drops_to_dangerous_or_risky():
    out = ph.compute(
        impact=_impact(risk_level="critical", rollback=True),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    )
    assert out.grade in (ph.GRADE_DANGEROUS, ph.GRADE_RISKY)


def test_no_rollback_lowers_score():
    yes = ph.compute(
        impact=_impact(risk_level="low", rollback=True),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    ).score
    no = ph.compute(
        impact=_impact(risk_level="low", rollback=False),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    ).score
    assert no < yes
    assert (yes - no) >= 20


def test_high_conflict_lowers_more_than_medium():
    high = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(sev="high", n=1),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    ).score
    medium = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(sev="medium", n=1),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    ).score
    assert high < medium


def test_dependency_certainty_partially_offsets():
    """All-certain dependencies should score higher than
    mixed-confidence dependencies.

    Use a non-`small` blast so the single-router bonus
    doesn't clamp both to 100 and hide the offset."""
    certain = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=1, all_certain=True),
        blast=_blast(bucket="medium", routers=2),
    ).score
    likely = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=1, all_certain=False),
        blast=_blast(bucket="medium", routers=2),
    ).score
    assert certain > likely


# ─── Blast escalation ───────────────────────────────────────


def test_blast_critical_lowers_score_dramatically():
    """Critical blast on top of low impact still drops by 30
    points compared to the small-bucket baseline."""
    baseline = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="small", routers=1),
    ).score
    out = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(bucket="critical", routers=10),
    )
    # 30-point gap from CRITICAL minus the lost 5-point
    # single-router bonus = at least 30.
    assert baseline - out.score >= 30
    assert out.score <= 75


# ─── Clamping ───────────────────────────────────────────────


def test_score_clamped_at_zero():
    out = ph.compute(
        impact=_impact(risk_level="critical", rollback=False),
        conflicts=_conflicts(sev="high", n=3),
        dependencies=_deps(n=2),
        blast=_blast(bucket="critical", routers=10),
    )
    assert 0 <= out.score <= 100
    # The worst-case stack should be very low.
    assert out.grade == ph.GRADE_DANGEROUS


# ─── Reasoning + advisory flag ───────────────────────────────


def test_reasoning_string_includes_score_and_grade():
    out = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(),
    )
    assert str(out.score) in out.reasoning_ar
    assert "استشارية" in out.reasoning_ar
    assert out.is_advisory is True


def test_canary_recommendation_adds_positive():
    out = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(),
        canary_recommended=True,
    )
    assert any("canary" in p for p in out.positives_ar)


def test_as_dict_is_json_friendly():
    out = ph.compute(
        impact=_impact(),
        conflicts=_conflicts(n=0),
        dependencies=_deps(n=0),
        blast=_blast(),
    )
    d = out.as_dict()
    assert isinstance(d["score"], int)
    assert isinstance(d["positives_ar"], list)
    assert isinstance(d["negatives_ar"], list)
    assert d["is_advisory"] is True
