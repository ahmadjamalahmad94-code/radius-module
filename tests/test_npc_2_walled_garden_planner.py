"""NPC Phase 2 — walled-garden planner (pure tests)."""
from __future__ import annotations


def _policy(**overrides) -> dict:
    base = {
        "id": 5, "tenant_id": 1, "router_id": 1,
        "name": "Payments allowlist",
        "slug": "payments-allowlist",
        "hotspot_profile": "hsprof1",
        "enabled": 1,
    }
    base.update(overrides)
    return base


def _entry(value, *, entry_type="dst_host", status="active",
           dst_port="", protocol=""):
    return {
        "value": value, "normalized_value": value.lower(),
        "entry_type": entry_type, "status": status,
        "dst_port": dst_port, "protocol": protocol,
    }


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_walled_garden_planner as m
    importlib.reload(m)
    assert m.SERVICE == "walled_garden"


def test_empty_policy_returns_blocked_plan():
    from app.radius.services import (
        npc_walled_garden_planner as p,
    )
    out = p.plan({}, [])
    assert not out.can_apply


def test_host_entries_emit_to_walled_garden_table():
    from app.radius.services import (
        npc_walled_garden_planner as p,
    )
    out = p.plan(_policy(), [
        _entry("api.payments.test"),
        _entry("otp.sms.test"),
    ])
    assert out.can_apply
    assert len(out.walled_garden_ops) == 2
    for c in out.walled_garden_ops:
        assert c.path == "/ip/hotspot/walled-garden"
        assert c.attrs["action"] == "allow"
        assert c.attrs["server"] == "hsprof1"
        assert c.attrs["comment"].startswith(
            "HOBE_NPC_WG:5:entry:dst_host"
        )


def test_ip_entries_emit_to_walled_garden_ip_table():
    from app.radius.services import (
        npc_walled_garden_planner as p,
    )
    out = p.plan(_policy(), [
        _entry("8.8.8.8", entry_type="dst_address",
               dst_port="443", protocol="tcp"),
    ])
    cmd = out.walled_garden_ops[0]
    assert cmd.path == "/ip/hotspot/walled-garden/ip"
    assert cmd.attrs["action"] == "accept"
    assert cmd.attrs["dst-address"] == "8.8.8.8"
    assert cmd.attrs["dst-port"] == "443"
    assert cmd.attrs["protocol"] == "tcp"


def test_empty_hotspot_profile_omits_server_attr():
    from app.radius.services import (
        npc_walled_garden_planner as p,
    )
    out = p.plan(_policy(hotspot_profile=""), [
        _entry("api.example.test"),
    ])
    cmd = out.walled_garden_ops[0]
    assert "server" not in cmd.attrs


def test_skipped_entries_appear_in_notes_only():
    from app.radius.services import (
        npc_walled_garden_planner as p,
    )
    out = p.plan(_policy(), [
        _entry("api.example.test"),
        _entry("disabled.example", status="disabled"),
        _entry("unsupported.example",
               entry_type="dst_glob"),
    ])
    assert len(out.walled_garden_ops) == 1
    assert any("disabled" in n for n in out.notes)
    assert any("dst_glob" in n for n in out.notes)


def test_empty_eligible_entries_emits_no_walled_garden_ops():
    from app.radius.services import (
        npc_walled_garden_planner as p,
    )
    out = p.plan(_policy(), [
        _entry("x", status="disabled"),
    ])
    assert out.walled_garden_ops == ()
    assert out.cleanup_ops      # cleanup still emitted
    assert any("بدون أثر" in w for w in out.warnings)


def test_cleanup_anchored_to_prefix_for_both_paths():
    from app.radius.services import (
        npc_walled_garden_planner as p,
    )
    out = p.plan(_policy(), [_entry("api.example.test")])
    paths = {c.path for c in out.cleanup_ops}
    assert "/ip/hotspot/walled-garden" in paths
    assert "/ip/hotspot/walled-garden/ip" in paths
    for c in out.cleanup_ops:
        assert c.find_pattern.startswith("^HOBE_NPC_WG:5:")


def test_renderer_consumes_plan_cleanly():
    from app.radius.services import (
        npc_walled_garden_planner as p,
        npc_script_renderer as r,
    )
    plan = p.plan(_policy(), [
        _entry("api.payments.test"),
        _entry("8.8.8.8", entry_type="dst_address"),
    ])
    fwd = r.render_forward_script(plan)
    rb = r.render_rollback_script(plan)
    assert "/ip/hotspot/walled-garden add" in fwd
    assert "dst-host=api.payments.test" in fwd
    assert "/ip/hotspot/walled-garden/ip add" in fwd
    assert "dst-address=8.8.8.8" in fwd
    assert "/ip/hotspot/walled-garden remove" in rb
    assert "/ip/hotspot/walled-garden/ip remove" in rb
