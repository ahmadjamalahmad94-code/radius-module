"""NPC Phase 2 — shared script renderer (pure tests)."""
from __future__ import annotations

import pytest


def test_module_has_no_side_effects_on_import():
    import importlib
    import app.radius.services.npc_script_renderer as m
    importlib.reload(m)
    assert callable(m.render_forward_script)
    assert callable(m.render_rollback_script)


# ─── Hash determinism ────────────────────────────────────────


def test_script_hash_deterministic_and_64_hex():
    from app.radius.services import npc_script_renderer as r
    assert r.script_hash("abc") == r.script_hash("abc")
    assert r.script_hash("abc") != r.script_hash("ABC")
    assert len(r.script_hash("anything")) == 64


# ─── Header rendering ────────────────────────────────────────


def _plan(svc="web_block", pid=7, **kw):
    from app.radius.services import npc_script_renderer as r
    return r.ScriptPlan(
        service=svc,
        policy_id=pid,
        comment_prefix=f"HOBE_NPC_BLOCK:{pid}:",
        **kw,
    )


def test_forward_returns_empty_when_blocked():
    from app.radius.services import npc_script_renderer as r
    p = _plan(blocking_errors=("missing exit_node",))
    assert r.render_forward_script(p) == ""


def test_header_carries_service_label_and_counts():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    header_lines = [ln for ln in r.render_forward_script(p).splitlines()
                    if ln.startswith("#")]
    joined = "\n".join(header_lines)
    assert "Website / App Block" in joined
    assert "policy_id            : 7" in joined
    # No commands → header only, but should still terminate
    # with the doc line.
    assert "Safe to re-run" in joined


# ─── Add-command rendering ───────────────────────────────────


def _add(path, attrs, section="filter"):
    from app.radius.services import npc_script_renderer as r
    return r.PlanCommand(
        section=section, path=path, kind="add",
        attrs=attrs,
    )


def test_render_add_uses_stable_attr_order_with_comment_last():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    cmd = _add(
        "/ip/firewall/filter",
        {
            "comment": "HOBE_NPC_BLOCK:7:rule",
            "action": "drop",
            "chain": "forward",
            "dst-address-list": "HOBE_NPC_BLOCK_7",
        },
    )
    out = r.render_command(cmd, plan=p)
    # Attrs sorted alphabetically; comment last.
    assert out.startswith("/ip/firewall/filter add ")
    assert " action=drop" in out
    # Comment must appear after all other attrs.
    last_attr = out.split(" comment=", 1)[1]
    assert "=" not in last_attr or last_attr.startswith('"')


def test_render_add_quotes_values_with_spaces():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    cmd = _add(
        "/system/scheduler",
        {
            "name": "my sched",
            "on-event": "/log info \"hi\"",
            "comment": "HOBE_NPC_BLOCK:7:scheduler",
        },
    )
    out = r.render_command(cmd, plan=p)
    assert "name=\"my sched\"" in out
    # Internal quotes get backslash-escaped.
    assert "\\\"" in out


def test_render_add_refuses_missing_comment_prefix():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    cmd = _add(
        "/ip/firewall/filter",
        {"chain": "forward", "comment": "unmanaged"},
    )
    with pytest.raises(r.RenderSafetyError):
        r.render_command(cmd, plan=p)


# ─── Remove-command rendering ────────────────────────────────


def test_render_remove_uses_find_pattern_when_anchored():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    cmd = r.PlanCommand(
        section="cleanup",
        path="/ip/firewall/filter",
        kind="remove",
        find_pattern="^HOBE_NPC_BLOCK:7:",
    )
    out = r.render_command(cmd, plan=p)
    assert "[find comment~\"^HOBE_NPC_BLOCK:7:\"]" in out
    assert out.startswith("/ip/firewall/filter remove")


def test_render_remove_refuses_unanchored_pattern():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    cmd = r.PlanCommand(
        section="cleanup",
        path="/ip/firewall/filter",
        kind="remove",
        find_pattern="HOBE_NPC_BLOCK:7:",
    )
    with pytest.raises(r.RenderSafetyError):
        r.render_command(cmd, plan=p)


def test_render_remove_refuses_empty_find_pattern():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    cmd = r.PlanCommand(
        section="cleanup",
        path="/ip/firewall/filter",
        kind="remove",
        find_pattern="",
    )
    with pytest.raises(r.RenderSafetyError):
        r.render_command(cmd, plan=p)


# ─── Secret tripwires ────────────────────────────────────────


def test_renderer_refuses_to_emit_passwords():
    from app.radius.services import npc_script_renderer as r
    # An attribute carrying `password=` would slip through
    # alone in attr form, but the assembled body always
    # contains `password=`. Build a malicious add and confirm
    # the assertion catches it.
    cmd = _add(
        "/user",
        {"comment": "HOBE_NPC_BLOCK:7:nope",
         "password": "hunter2"},
    )
    p = _plan(filter_ops=(cmd,))
    with pytest.raises(r.RenderSafetyError):
        r.render_forward_script(p)


def test_renderer_refuses_to_emit_private_key():
    from app.radius.services import npc_script_renderer as r
    cmd = _add(
        "/interface/wireguard",
        {"comment": "HOBE_NPC_BLOCK:7:nope",
         "private-key": "AbCdEf=="},
    )
    p = _plan(filter_ops=(cmd,))
    with pytest.raises(r.RenderSafetyError):
        r.render_forward_script(p)


# ─── End-to-end forward + rollback shape ─────────────────────


def test_full_script_section_ordering_is_stable():
    from app.radius.services import npc_script_renderer as r
    p = _plan(
        cleanup_ops=(r.PlanCommand(
            section="cleanup",
            path="/ip/firewall/filter",
            kind="remove",
            find_pattern="^HOBE_NPC_BLOCK:7:",
        ),),
        address_list_ops=(_add(
            "/ip/firewall/address-list",
            {"list": "HOBE_NPC_BLOCK_7", "address": "tiktok.com",
             "comment": "HOBE_NPC_BLOCK:7:target"},
        ),),
        filter_ops=(_add(
            "/ip/firewall/filter",
            {"chain": "forward", "action": "drop",
             "dst-address-list": "HOBE_NPC_BLOCK_7",
             "comment": "HOBE_NPC_BLOCK:7:rule"},
        ),),
    )
    body = r.render_forward_script(p)
    # cleanup precedes address-list precedes filter.
    cleanup_idx = body.index("# cleanup")
    al_idx = body.index("# address-list")
    filter_idx = body.index("# firewall filter")
    assert cleanup_idx < al_idx < filter_idx


def test_render_forward_is_deterministic_for_same_plan():
    from app.radius.services import npc_script_renderer as r
    p = _plan(
        address_list_ops=(_add(
            "/ip/firewall/address-list",
            {"list": "HOBE_NPC_BLOCK_7", "address": "tiktok.com",
             "comment": "HOBE_NPC_BLOCK:7:target"},
        ),),
    )
    a = r.render_forward_script(p)
    b = r.render_forward_script(p)
    assert a == b
    # Hash matches the body.
    assert r.script_hash(a) == r.script_hash(b)


def test_rollback_lists_only_remove_ops_with_banner():
    from app.radius.services import npc_script_renderer as r
    p = _plan(
        rollback_ops=(r.PlanCommand(
            section="cleanup",
            path="/ip/firewall/filter",
            kind="remove",
            find_pattern="^HOBE_NPC_BLOCK:7:",
        ),),
    )
    body = r.render_rollback_script(p)
    assert "Removes ONLY entries whose comment starts with" in body
    assert "HOBE_NPC_BLOCK:7:" in body
    assert "/ip/firewall/filter remove" in body


def test_rollback_empty_when_no_ops():
    from app.radius.services import npc_script_renderer as r
    p = _plan()
    assert r.render_rollback_script(p) == ""


# ─── Summary ─────────────────────────────────────────────────


def test_script_summary_shapes():
    from app.radius.services import npc_script_renderer as r
    p = _plan(
        warnings=("careful",),
        notes=("note one",),
        cleanup_ops=(r.PlanCommand(
            section="cleanup",
            path="/ip/firewall/filter",
            kind="remove",
            find_pattern="^HOBE_NPC_BLOCK:7:",
        ),),
    )
    s = r.script_summary(p)
    assert s["policy_id"] == 7
    assert s["service"] == "web_block"
    assert s["section_counts"]["cleanup"] == 1
    assert s["warnings"] == ["careful"]
    assert s["notes"] == ["note one"]
