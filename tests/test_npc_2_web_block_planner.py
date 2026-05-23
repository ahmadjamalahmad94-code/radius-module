"""NPC Phase 2 — web-block planner (pure tests)."""
from __future__ import annotations


def _policy(**overrides) -> dict:
    base = {
        "id": 7, "tenant_id": 1, "router_id": 1,
        "name": "TikTok block", "slug": "tiktok-block",
        "scope": "all_users", "schedule_id": "",
        "fail_open": 1, "enabled": 1,
    }
    base.update(overrides)
    return base


def _target(value, status="active", target_type="domain",
            category="tiktok"):
    return {
        "value": value, "normalized_value": value,
        "target_type": target_type,
        "category": category, "status": status,
    }


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_web_block_planner as m
    importlib.reload(m)
    assert m.SERVICE == "web_block"
    assert m.address_list_name(7) == "HOBE_NPC_BLOCK_7"


def test_empty_policy_returns_blocked_plan():
    from app.radius.services import npc_web_block_planner as p
    out = p.plan({}, [])
    assert not out.can_apply


def test_emits_one_address_list_add_per_active_target():
    from app.radius.services import npc_web_block_planner as p
    out = p.plan(_policy(), [
        _target("tiktok.com"),
        _target("facebook.com", category="facebook"),
        _target("disabled.example", status="disabled"),
    ])
    al_adds = list(out.address_list_ops)
    assert len(al_adds) == 2
    for c in al_adds:
        assert c.path == "/ip/firewall/address-list"
        assert c.attrs["list"] == "HOBE_NPC_BLOCK_7"
        assert c.attrs["comment"].startswith(
            "HOBE_NPC_BLOCK:7:target:"
        )


def test_emits_one_drop_filter_rule_when_targets_exist():
    from app.radius.services import npc_web_block_planner as p
    out = p.plan(_policy(), [_target("tiktok.com")])
    assert len(out.filter_ops) == 1
    rule = out.filter_ops[0]
    assert rule.attrs["chain"] == "forward"
    assert rule.attrs["action"] == "drop"
    assert rule.attrs["dst-address-list"] == "HOBE_NPC_BLOCK_7"
    assert rule.attrs["comment"] == "HOBE_NPC_BLOCK:7:rule:block"


def test_empty_eligible_targets_emits_no_filter_rule():
    """Defence-in-depth: fail_open OR fail_closed, empty
    list never invents a default-drop catch-all."""
    from app.radius.services import npc_web_block_planner as p
    fail_open = p.plan(
        _policy(fail_open=1),
        [_target("x", status="disabled")],
    )
    fail_closed = p.plan(
        _policy(fail_open=0),
        [_target("x", status="disabled")],
    )
    assert fail_open.filter_ops == ()
    assert fail_closed.filter_ops == ()
    # Both still emit cleanup ops so a rollback wipes any
    # previously-applied rule cleanly.
    assert fail_open.cleanup_ops
    assert fail_closed.cleanup_ops
    # Operator-visible warning explains the no-op.
    assert any("بدون أثر" in w for w in fail_open.warnings)


def test_schedule_time_value_propagates_to_filter_rule():
    from app.radius.services import npc_web_block_planner as p
    out = p.plan(
        _policy(), [_target("a.com")],
        schedule_time_value="0-24h,sun,mon,tue,wed,thu",
    )
    rule = out.filter_ops[0]
    assert rule.attrs.get("time") == "0-24h,sun,mon,tue,wed,thu"


def test_cleanup_anchored_to_policy_prefix():
    from app.radius.services import npc_web_block_planner as p
    out = p.plan(_policy(), [_target("a.com")])
    for c in out.cleanup_ops:
        assert c.find_pattern.startswith("^HOBE_NPC_BLOCK:7:")
    # Cleanup hits BOTH filter (rule) and address-list (entries).
    paths = {c.path for c in out.cleanup_ops}
    assert "/ip/firewall/filter" in paths
    assert "/ip/firewall/address-list" in paths


def test_over_cap_target_count_is_blocked():
    from app.radius.services import (
        npc_web_block_planner as p, npc_policy as pol,
    )
    too_many = [
        _target(f"site-{i}.com")
        for i in range(pol.MAX_TARGETS_PER_POLICY + 1)
    ]
    out = p.plan(_policy(), too_many)
    assert not out.can_apply
    assert any("exceeds" in e for e in out.blocking_errors)


def test_renderer_consumes_plan_cleanly():
    from app.radius.services import (
        npc_web_block_planner as p,
        npc_script_renderer as r,
    )
    plan = p.plan(
        _policy(), [
            _target("tiktok.com"),
            _target("FACEBOOK.COM"),
        ],
    )
    fwd = r.render_forward_script(plan)
    rb = r.render_rollback_script(plan)
    assert "address=tiktok.com" in fwd
    assert "address=FACEBOOK.COM" in fwd
    assert "dst-address-list=HOBE_NPC_BLOCK_7" in fwd
    assert "/ip/firewall/address-list remove" in rb
    assert "/ip/firewall/filter remove" in rb
