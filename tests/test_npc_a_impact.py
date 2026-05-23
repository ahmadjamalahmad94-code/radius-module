"""NPC Phase A — impact analyzer."""
from __future__ import annotations

from app.radius.services import (
    npc_impact_analyzer as ia,
    npc_remote_access_planner as ra_planner,
    npc_script_renderer as renderer,
    npc_web_block_planner as wb_planner,
    npc_walled_garden_planner as wg_planner,
)


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_impact_analyzer as m
    importlib.reload(m)
    assert m.RISK_LOW == "low"
    assert callable(m.analyze)


# ─── Helpers ────────────────────────────────────────────────


def _wb_policy(**kw):
    base = {"id": 7, "tenant_id": 1, "router_id": 1,
            "name": "x", "slug": "x", "scope": "all_users",
            "schedule_id": "", "fail_open": 1, "enabled": 1}
    base.update(kw)
    return base


def _wb_target(value, status="active"):
    return {"value": value, "normalized_value": value.lower(),
            "target_type": "domain", "category": "tiktok",
            "status": status}


def _ra_policy(**kw):
    base = {"id": 9, "tenant_id": 1, "router_id": 2,
            "name": "r", "slug": "r",
            "allow_winbox": 1, "allow_ssh": 0, "allow_api": 0,
            "allow_api_ssl": 0,
            "allow_webfig_http": 0, "allow_webfig_https": 1,
            "source_address_list": "ops",
            "expires_at": "2027-01-01T00:00:00Z",
            "reason": "", "enabled": 1}
    base.update(kw)
    return base


def _wg_policy(**kw):
    base = {"id": 5, "tenant_id": 1, "router_id": 1,
            "name": "g", "slug": "g",
            "hotspot_profile": "hsprof1", "enabled": 1}
    base.update(kw)
    return base


def _wg_entry(value, entry_type="dst_host", status="active"):
    return {"value": value, "normalized_value": value.lower(),
            "entry_type": entry_type, "status": status,
            "dst_port": "", "protocol": ""}


# ─── web_block risk ladder ──────────────────────────────────


def test_web_block_low_risk_short_targets():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("tiktok.com")])
    fwd = renderer.render_forward_script(plan)
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[_wb_target("tiktok.com")],
        rendered_forward=fwd, rendered_rollback=rb,
    )
    # web_block emits forward-chain drop ⇒ at least medium.
    assert out.risk_level in (ia.RISK_LOW, ia.RISK_MEDIUM)
    assert out.rollback_available is True
    assert out.beginner_explanation_ar
    assert "حظر" in out.summary_ar
    assert out.change_count >= 2


def test_web_block_medium_risk_for_forward_drop():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("tiktok.com")])
    fwd = renderer.render_forward_script(plan)
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[_wb_target("tiktok.com")],
        rendered_forward=fwd, rendered_rollback=rb,
    )
    assert out.risk_level == ia.RISK_MEDIUM
    assert any("forward" in r for r in out.risk_reasons_ar)


def test_web_block_high_risk_when_all_routers():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("tiktok.com")])
    fwd = renderer.render_forward_script(plan)
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[_wb_target("tiktok.com")],
        rendered_forward=fwd, rendered_rollback=rb,
        all_routers_targeted=True,
    )
    assert out.risk_level == ia.RISK_HIGH
    assert any("كل الراوترات" in r for r in out.risk_reasons_ar)


# ─── walled_garden low risk ─────────────────────────────────


def test_walled_garden_low_risk():
    policy = _wg_policy()
    entries = [_wg_entry("api.payments.test")]
    plan = wg_planner.plan(policy, entries)
    fwd = renderer.render_forward_script(plan)
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="walled_garden", policy=policy,
        plan=plan, targets=entries,
        rendered_forward=fwd, rendered_rollback=rb,
    )
    assert out.risk_level == ia.RISK_LOW
    assert out.rollback_available is True
    assert out.affected_services == ("walled_garden",)


# ─── remote_access risks ────────────────────────────────────


def test_remote_access_medium_for_input_chain():
    policy = _ra_policy()
    plan = ra_planner.plan(policy)
    fwd = renderer.render_forward_script(plan)
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="remote_access", policy=policy,
        plan=plan, rendered_forward=fwd,
        rendered_rollback=rb,
    )
    # Touching input chain bumps to medium.
    assert out.risk_level == ia.RISK_MEDIUM
    assert any("input" in r for r in out.risk_reasons_ar)


def test_remote_access_high_when_no_rollback():
    """Construct an artificial plan that has forward content but
    no rollback ops to exercise the missing-rollback branch."""
    policy = _ra_policy()
    real_plan = ra_planner.plan(policy)
    no_rollback_plan = renderer.ScriptPlan(
        service=real_plan.service,
        policy_id=real_plan.policy_id,
        comment_prefix=real_plan.comment_prefix,
        filter_ops=real_plan.filter_ops,
        scheduler_ops=real_plan.scheduler_ops,
        cleanup_ops=real_plan.cleanup_ops,
        # rollback_ops empty on purpose
        rollback_ops=(),
    )
    fwd = renderer.render_forward_script(no_rollback_plan)
    rb = ""  # no rollback rendered
    out = ia.analyze(
        policy_type="remote_access", policy=policy,
        plan=no_rollback_plan, rendered_forward=fwd,
        rendered_rollback=rb,
    )
    assert out.risk_level in (ia.RISK_HIGH, ia.RISK_CRITICAL)
    assert out.rollback_available is False
    assert "التراجع" in out.rollback_explanation_ar


# ─── No-op preview ──────────────────────────────────────────


def test_empty_preview_is_explained_as_no_op():
    """Web-block with zero active targets → planner emits no
    address-list/filter ops; analyzer must surface the calm
    'لن تغيّر شيئاً' message."""
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [])
    fwd = renderer.render_forward_script(plan)
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[], rendered_forward=fwd,
        rendered_rollback=rb,
    )
    assert "لن تغيّر شيئاً" in out.summary_ar
    assert any("فارغة" in w for w in out.warnings_ar)
    assert out.risk_level == ia.RISK_LOW


# ─── 0.0.0.0/0 detection ─────────────────────────────────────


def test_blackhole_cidr_in_rendered_forward_is_high_risk():
    """Synthesise a rendered string that includes 0.0.0.0/0 —
    the analyzer must detect it regardless of plan shape."""
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("a.com")])
    fwd = (
        renderer.render_forward_script(plan)
        + "\n# extra: dst-address=0.0.0.0/0 from foreign source"
    )
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[_wb_target("a.com")],
        rendered_forward=fwd, rendered_rollback=rb,
    )
    assert out.risk_level == ia.RISK_HIGH
    assert any("0.0.0.0/0" in r for r in out.risk_reasons_ar)


# ─── Rollback explanation ───────────────────────────────────


def test_rollback_explanation_uses_comment_prefix():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("tiktok.com")])
    fwd = renderer.render_forward_script(plan)
    rb = renderer.render_rollback_script(plan)
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[_wb_target("tiktok.com")],
        rendered_forward=fwd, rendered_rollback=rb,
    )
    assert out.rollback_available is True
    assert plan.comment_prefix in out.rollback_explanation_ar


# ─── Secret tripwire → CRITICAL ─────────────────────────────


def test_secret_tripwire_render_error_is_critical():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("tiktok.com")])
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[_wb_target("tiktok.com")],
        rendered_forward="", rendered_rollback="",
        render_error="tripwire 'password=' detected",
    )
    assert out.risk_level == ia.RISK_CRITICAL
    assert any("حسّاسة" in r for r in out.risk_reasons_ar)


# ─── Arabic beginner summary always present ─────────────────


def test_beginner_summary_present_for_every_service():
    cases = [
        ("web_block", _wb_policy(),
         wb_planner.plan(_wb_policy(), [_wb_target("a.com")]),
         [_wb_target("a.com")]),
        ("walled_garden", _wg_policy(),
         wg_planner.plan(_wg_policy(),
                          [_wg_entry("api.x.test")]),
         [_wg_entry("api.x.test")]),
        ("remote_access", _ra_policy(),
         ra_planner.plan(_ra_policy()), ()),
    ]
    for svc, policy, plan, tgts in cases:
        fwd = renderer.render_forward_script(plan)
        rb = renderer.render_rollback_script(plan)
        out = ia.analyze(
            policy_type=svc, policy=policy,
            plan=plan, targets=tgts,
            rendered_forward=fwd, rendered_rollback=rb,
        )
        assert out.beginner_explanation_ar
        # Arabic content — at least one char outside ASCII.
        assert any(ord(c) > 127
                   for c in out.beginner_explanation_ar)
        # No raw RouterOS in the beginner explanation.
        assert "/ip/firewall" not in out.beginner_explanation_ar
        assert "place-before" not in out.beginner_explanation_ar


# ─── API projection ─────────────────────────────────────────


def test_as_dict_is_json_friendly():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("a.com")])
    out = ia.analyze(
        policy_type="web_block", policy=policy,
        plan=plan, targets=[_wb_target("a.com")],
        rendered_forward=renderer.render_forward_script(plan),
        rendered_rollback=renderer.render_rollback_script(plan),
    )
    d = out.as_dict()
    # Lists not tuples for JSON.
    assert isinstance(d["affected_services"], list)
    assert isinstance(d["warnings_ar"], list)
    assert isinstance(d["risk_reasons_ar"], list)
    assert isinstance(d["changes_summary"], dict)
    assert d["risk_level"] in (
        ia.RISK_LOW, ia.RISK_MEDIUM,
        ia.RISK_HIGH, ia.RISK_CRITICAL,
    )
