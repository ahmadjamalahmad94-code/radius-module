"""NPC Phase G — canary planner."""
from __future__ import annotations

from app.radius.services import npc_canary_planner as cp
from app.radius.services.npc_blast_radius import BlastRadius


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_canary_planner as m
    importlib.reload(m)
    assert m.STRATEGY_FULL == "full"
    assert callable(m.plan)


def _blast(bucket="small", routers=1):
    return BlastRadius(
        affected_router_count=routers,
        estimated_user_count=None,
        estimated_profile_count=None,
        blast_radius=bucket,
        recommendation_ar="r",
        heuristic_note_ar="n",
    )


# ─── Strategy mapping ────────────────────────────────────────


def test_small_blast_recommends_full_strategy():
    out = cp.plan(blast=_blast("small"))
    assert out.recommended_strategy == cp.STRATEGY_FULL
    # Single-router policies don't require a canary checkpoint.
    assert out.rollback_checkpoint_required is False
    # Still includes a backup step.
    assert any("احتياط" in s for s in out.steps)


def test_medium_blast_recommends_staged():
    out = cp.plan(blast=_blast("medium", routers=3))
    assert out.recommended_strategy == cp.STRATEGY_STAGED
    assert out.rollback_checkpoint_required is True


def test_large_blast_recommends_canary():
    out = cp.plan(blast=_blast("large", routers=8))
    assert out.recommended_strategy == cp.STRATEGY_CANARY
    # Canary plan must include the apply-one-then-wait pattern.
    joined = "\n".join(out.steps)
    assert "راوتر اختبار" in joined
    assert any("انتظر" in s for s in out.steps)


def test_critical_blast_recommends_hold():
    out = cp.plan(blast=_blast("critical", routers=10))
    assert out.recommended_strategy == cp.STRATEGY_HOLD
    assert "تأجيل" in out.recommendation_ar


# ─── Step ordering ──────────────────────────────────────────


def test_backup_is_always_the_first_step():
    for bucket in ("small", "medium", "large", "critical"):
        out = cp.plan(blast=_blast(bucket))
        assert "نسخة احتياطية" in out.steps[0]


def test_rollback_ready_appears_for_non_small():
    for bucket in ("medium", "large", "critical"):
        out = cp.plan(blast=_blast(bucket))
        joined = "\n".join(out.steps)
        assert "rollback" in joined


# ─── Wait time guidance ─────────────────────────────────────


def test_wait_time_present_for_all_strategies():
    for bucket in ("small", "medium", "large", "critical"):
        out = cp.plan(blast=_blast(bucket))
        assert out.wait_time_recommendation_ar


# ─── JSON projection ────────────────────────────────────────


def test_as_dict_json_friendly():
    out = cp.plan(blast=_blast("large"))
    d = out.as_dict()
    assert d["recommended_strategy"] == cp.STRATEGY_CANARY
    assert isinstance(d["steps"], list)
    assert d["rollback_checkpoint_required"] is True
    assert d["wait_time_recommendation_ar"]
    assert d["recommendation_ar"]


# ─── No execution ────────────────────────────────────────────


def test_module_does_not_touch_mikrotik():
    """Defence-in-depth: the canary planner is a planner only.
    Re-importing it must succeed without any network / DB
    primitives ever being touched."""
    import importlib
    import app.radius.services.npc_canary_planner as m
    importlib.reload(m)
    # No imports of MikrotikClient / sqlite — verified by
    # the absence of these names in the module dict.
    assert "MikrotikClient" not in dir(m)
    assert "sqlite3" not in dir(m)
