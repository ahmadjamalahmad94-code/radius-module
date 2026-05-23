"""NPC Phase 2 — remote-access planner (pure tests)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


_NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)


def _expiry_iso(hours: int = 2) -> str:
    return (_NOW + timedelta(hours=hours)).isoformat().replace(
        "+00:00", "Z"
    )


def _policy(**overrides) -> dict:
    base = {
        "id": 42, "tenant_id": 1, "router_id": 9,
        "name": "Emergency", "slug": "emergency",
        "allow_winbox": 1, "allow_ssh": 0, "allow_api": 0,
        "allow_api_ssl": 0,
        "allow_webfig_http": 0, "allow_webfig_https": 1,
        "source_address_list": "ops-bastion",
        "expires_at": _expiry_iso(2),
        "reason": "investigating", "enabled": 1,
    }
    base.update(overrides)
    return base


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_remote_access_planner as m
    importlib.reload(m)
    assert m.SERVICE == "remote_access"


def test_empty_policy_returns_blocked_plan():
    from app.radius.services import (
        npc_remote_access_planner as p,
    )
    out = p.plan({}, now=_NOW)
    assert not out.can_apply
    assert out.blocking_errors


def test_plan_includes_cleanup_first():
    from app.radius.services import (
        npc_remote_access_planner as p,
    )
    out = p.plan(_policy(), now=_NOW)
    assert out.can_apply
    assert out.cleanup_ops
    # Cleanup removes filter rules + scheduler entries by
    # the same anchored prefix.
    paths = {c.path for c in out.cleanup_ops}
    assert "/ip/firewall/filter" in paths
    assert "/system/scheduler" in paths
    for c in out.cleanup_ops:
        assert c.kind == "remove"
        assert c.find_pattern.startswith("^HOBE_NPC_REMOTE:42:")


def test_plan_emits_one_filter_rule_per_enabled_service():
    from app.radius.services import (
        npc_remote_access_planner as p,
    )
    out = p.plan(_policy(
        allow_winbox=1, allow_ssh=1, allow_webfig_https=1,
    ), now=_NOW)
    ports = sorted(
        int(c.attrs["dst-port"]) for c in out.filter_ops
    )
    assert ports == [22, 443, 8291]
    # Source allowlist propagated.
    for c in out.filter_ops:
        assert c.attrs["src-address-list"] == "ops-bastion"
        assert c.attrs["chain"] == "input"
        assert c.attrs["action"] == "accept"
        assert c.attrs["place-before"] == "0"
        assert c.attrs["comment"].startswith(
            "HOBE_NPC_REMOTE:42:service:"
        )


def test_plan_without_source_list_omits_src_attr():
    from app.radius.services import (
        npc_remote_access_planner as p,
    )
    out = p.plan(_policy(
        source_address_list="",
        expires_at=_expiry_iso(2),
    ), now=_NOW)
    assert out.can_apply
    for c in out.filter_ops:
        assert "src-address-list" not in c.attrs


def test_plan_with_expires_at_emits_scheduler():
    from app.radius.services import (
        npc_remote_access_planner as p,
    )
    out = p.plan(_policy(), now=_NOW)
    assert len(out.scheduler_ops) == 1
    sch = out.scheduler_ops[0]
    assert sch.path == "/system/scheduler"
    assert sch.attrs["name"].startswith("hobe-npc-remote-42-")
    # start-date / start-time present and ISO-derived.
    assert sch.attrs.get("start-time")
    assert sch.attrs.get("start-date")
    assert "/ip firewall filter remove" in sch.attrs["on-event"]
    assert sch.attrs["comment"].endswith(":scheduler")


def test_plan_without_expires_no_scheduler():
    from app.radius.services import (
        npc_remote_access_planner as p,
    )
    out = p.plan(_policy(
        expires_at="", source_address_list="ops-bastion",
    ), now=_NOW)
    assert out.can_apply
    assert out.scheduler_ops == ()


def test_assessment_blockers_make_plan_unapplyable():
    """No source list + no expiry = blocked."""
    from app.radius.services import (
        npc_remote_access_planner as p,
    )
    out = p.plan(_policy(
        source_address_list="", expires_at="",
    ), now=_NOW)
    assert not out.can_apply
    # Cleanup ops still get emitted so a rollback path is
    # discoverable even when the policy can't be applied.
    assert out.cleanup_ops
    assert out.rollback_ops


def test_renderer_consumes_plan_cleanly():
    from app.radius.services import (
        npc_remote_access_planner as p,
        npc_script_renderer as r,
    )
    plan = p.plan(_policy(), now=_NOW)
    fwd = r.render_forward_script(plan)
    rb = r.render_rollback_script(plan)
    assert "HOBE_NPC_REMOTE:42:" in fwd
    assert "HOBE_NPC_REMOTE:42:" in rb
    # Forward references /ip/firewall/filter add for winbox
    # (default-on) + webfig_https (default-on).
    assert "/ip/firewall/filter add " in fwd
    assert "dst-port=8291" in fwd
    assert "dst-port=443" in fwd
    # Rollback removes scheduler + filter.
    assert "/system/scheduler remove" in rb
    assert "/ip/firewall/filter remove" in rb
