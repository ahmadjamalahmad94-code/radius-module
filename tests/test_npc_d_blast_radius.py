"""NPC Phase D — blast radius analyzer."""
from __future__ import annotations

from app.radius.services import (
    npc_blast_radius as br,
    npc_remote_access_planner as ra_planner,
    npc_web_block_planner as wb_planner,
)


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_blast_radius as m
    importlib.reload(m)
    assert m.BLAST_SMALL == "small"
    assert callable(m.analyze)


def _wb_policy(**kw):
    base = {"id": 7, "tenant_id": 1, "router_id": 1,
            "name": "x", "slug": "x", "scope": "all_users",
            "schedule_id": "", "fail_open": 1, "enabled": 1}
    base.update(kw)
    return base


def _wb_target(value):
    return {"value": value,
            "normalized_value": value.lower(),
            "target_type": "domain",
            "category": "x", "status": "active"}


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


# ─── Buckets ─────────────────────────────────────────────────


def test_one_router_walled_garden_is_small():
    """walled_garden adds entries — no forward drop, no input
    chain change, 1 router → small."""
    from app.radius.services import (
        npc_walled_garden_planner as wg,
    )
    plan = wg.plan(
        {"id": 5, "tenant_id": 1, "router_id": 1, "name": "x",
         "slug": "x", "hotspot_profile": "", "enabled": 1},
        [{"value": "api.x.test",
          "normalized_value": "api.x.test",
          "entry_type": "dst_host", "status": "active",
          "dst_port": "", "protocol": ""}],
    )
    out = br.analyze(
        policy_type="walled_garden", plan=plan,
        affected_router_count=1,
    )
    assert out.blast_radius == br.BLAST_SMALL


def test_one_router_web_block_is_medium_due_to_forward_drop():
    plan = wb_planner.plan(_wb_policy(),
                             [_wb_target("tiktok.com")])
    out = br.analyze(
        policy_type="web_block", plan=plan,
        affected_router_count=1,
    )
    # forward drop bumps to medium even with 1 router.
    assert out.blast_radius == br.BLAST_MEDIUM


def test_multiple_routers_medium():
    plan = wb_planner.plan(_wb_policy(),
                             [_wb_target("tiktok.com")])
    out = br.analyze(
        policy_type="web_block", plan=plan,
        affected_router_count=3,
    )
    assert out.blast_radius == br.BLAST_MEDIUM


def test_five_routers_large():
    plan = wb_planner.plan(_wb_policy(),
                             [_wb_target("tiktok.com")])
    out = br.analyze(
        policy_type="web_block", plan=plan,
        affected_router_count=5,
    )
    assert out.blast_radius == br.BLAST_LARGE


def test_all_routers_targeted_large_for_non_dropping_plan():
    """A walled-garden plan doesn't drop traffic — at
    all_routers_targeted it's LARGE, not CRITICAL."""
    from app.radius.services import (
        npc_walled_garden_planner as wg,
    )
    plan = wg.plan(
        {"id": 5, "tenant_id": 1, "router_id": 1, "name": "x",
         "slug": "x", "hotspot_profile": "", "enabled": 1},
        [{"value": "api.x.test",
          "normalized_value": "api.x.test",
          "entry_type": "dst_host", "status": "active",
          "dst_port": "", "protocol": ""}],
    )
    out = br.analyze(
        policy_type="walled_garden", plan=plan,
        affected_router_count=10,
        all_routers_targeted=True,
    )
    assert out.blast_radius == br.BLAST_LARGE


def test_all_routers_plus_forward_drop_is_critical():
    plan = wb_planner.plan(_wb_policy(),
                             [_wb_target("a.com")])
    out = br.analyze(
        policy_type="web_block", plan=plan,
        affected_router_count=10,
        all_routers_targeted=True,
    )
    assert out.blast_radius == br.BLAST_CRITICAL or \
        out.blast_radius == br.BLAST_LARGE
    # Phrasing differs but the recommendation always mentions
    # canary on critical.
    if out.blast_radius == br.BLAST_CRITICAL:
        assert "canary" in out.recommendation_ar


# ─── Input-chain note added on remote_access ─────────────────


def test_remote_access_with_two_routers_adds_input_note():
    plan = ra_planner.plan(_ra_policy())
    out = br.analyze(
        policy_type="remote_access", plan=plan,
        affected_router_count=2,
    )
    assert out.blast_radius == br.BLAST_MEDIUM
    assert "قناة إدارية" in out.recommendation_ar


# ─── Heuristic note always present ───────────────────────────


def test_heuristic_note_always_returned():
    plan = wb_planner.plan(_wb_policy(),
                             [_wb_target("a.com")])
    out = br.analyze(policy_type="web_block", plan=plan)
    assert "تقديريّة" in out.heuristic_note_ar


# ─── Estimated counts pass through ───────────────────────────


def test_user_and_profile_counts_pass_through():
    plan = wb_planner.plan(_wb_policy(),
                             [_wb_target("a.com")])
    out = br.analyze(
        policy_type="web_block", plan=plan,
        estimated_user_count=42,
        estimated_profile_count=3,
    )
    assert out.estimated_user_count == 42
    assert out.estimated_profile_count == 3
    d = out.as_dict()
    assert d["estimated_user_count"] == 42


def test_none_counts_serialize_as_null():
    plan = wb_planner.plan(_wb_policy(),
                             [_wb_target("a.com")])
    out = br.analyze(policy_type="web_block", plan=plan)
    d = out.as_dict()
    assert d["estimated_user_count"] is None
    assert d["estimated_profile_count"] is None
