"""VX2.3 — Site exit script planner + renderer (pure tests).

Like VX2.2, these services touch no DB and no Flask context.
Plain pytest, no app fixture, no migration overhead.
"""
from __future__ import annotations

import pytest


# ─── Helpers ─────────────────────────────────────────────────


def _node(**overrides) -> dict:
    base = {
        "id": 1,
        "name": "vps-main",
        "public_ip": "203.0.113.10",
        "wireguard_interface_name": "wg-vps",
        "wireguard_gateway_ip": "10.10.0.1",
        "tunnel_cidr": "10.10.0.0/24",
        "enabled": 1,
    }
    base.update(overrides)
    return base


def _policy(**overrides) -> dict:
    base = {
        "id": 42,
        "router_id": 7,
        "exit_node_id": 1,
        "name": "speedtest only",
        "slug": "speedtest-only",
        "fail_mode": "block_when_vps_down",
        "include_subdomains": 0,
        "include_router_output": 0,
        "enabled": 1,
    }
    base.update(overrides)
    return base


def _target(
    *, id_=1, value="speedtest.net", group="speedtest_measurement",
    target_type="domain", include_www=True,
    include_subdomains=False, status="active",
) -> dict:
    return {
        "id": id_,
        "value": value,
        "normalized_value": value.lower(),
        "target_type": target_type,
        "group_name": group,
        "include_www": 1 if include_www else 0,
        "include_subdomains": 1 if include_subdomains else 0,
        "status": status,
    }


# ─── Planner — basic shape ───────────────────────────────────


def test_planner_returns_plan_with_managed_names():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
    )
    assert plan.can_apply
    assert plan.address_list  == "HOBE_VX2_DST_42"
    assert plan.routing_table == "HOBE_VX2_42"
    assert plan.routing_mark  == "HOBE_VX2_42"
    assert plan.comment_prefix == "HOBE_VX2_SITE_EXIT:42:"


def test_planner_emits_all_required_sections_for_minimal_input():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
        wan_interface_list="WAN",
    )
    assert plan.cleanup_ops          # idempotency
    assert plan.routing_table_ops    # the FIB table
    assert plan.route_ops            # the route inside the table
    assert plan.address_list_ops     # destination address-list
    assert plan.mangle_ops           # mark-routing rule
    assert plan.firewall_filter_ops  # failsafe block
    assert plan.rollback_ops         # symmetric rollback


# ─── Planner — safety contracts ──────────────────────────────


def test_route_goes_only_into_custom_routing_table_never_main():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    route = plan.route_ops[0]
    assert route.attrs["dst-address"] == "0.0.0.0/0"
    # Critical safety invariant.
    assert route.attrs["routing-table"] == "HOBE_VX2_42"
    assert route.attrs["routing-table"] != "main"
    # VX2.6c — gateway is the WireGuard interface name, NOT an
    # IP. The interface bypass avoids a recursive-gateway lookup
    # failure inside the sparse custom routing table.
    assert route.attrs["gateway"] == "wg-vps"
    # Make sure no IP gateway sneaks in.
    assert "." not in route.attrs["gateway"], (
        f"gateway should be an interface name, not an IP: "
        f"{route.attrs['gateway']!r}"
    )


def test_mangle_emits_both_prerouting_and_output_chains_always():
    """VX2.6c — output chain mangle MUST be emitted even when
    include_router_output is False, so that router-originated
    `/tool fetch` works out of the box."""
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(include_router_output=0),
        exit_node=_node(),
        targets=[_target()],
    )
    chains = sorted(c.attrs["chain"] for c in plan.mangle_ops)
    assert chains == ["output", "prerouting"]


def test_planner_emits_srcnat_on_wg_interface():
    """VX2.6c — without src-nat on the wg interface, packets
    leave the router with source = LAN IP, get dropped by
    WireGuard's allowed-ips check on the VPS, and never reach
    MASQUERADE."""
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    assert plan.nat_ops, "expected at least one nat op (srcnat)"
    srcnat = plan.nat_ops[0]
    assert srcnat.attrs["chain"] == "srcnat"
    assert srcnat.attrs["action"] == "masquerade"
    # MUST target the wg interface specifically — NOT a generic
    # WAN interface or wildcard, which would mask all outbound.
    assert srcnat.attrs["out-interface"] == "wg-vps"
    assert "src-address" not in srcnat.attrs
    # Comment carries the managed prefix.
    assert srcnat.attrs["comment"].startswith(plan.comment_prefix)


def test_cleanup_paths_include_firewall_nat():
    """VX2.6c — cleanup must purge stale srcnat rules too,
    otherwise re-runs accumulate duplicate NAT entries."""
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    cleanup_paths = {c.path for c in plan.cleanup_ops}
    assert "/ip/firewall/nat" in cleanup_paths


def test_rendered_script_contains_srcnat_line():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    body = r.render_forward_script(plan)
    assert "/ip firewall nat add" in body
    assert "out-interface=wg-vps" in body
    assert "action=masquerade" in body


def test_rendered_route_uses_interface_not_ip():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    body = r.render_forward_script(plan)
    # gateway=wg-vps must appear in the rendered route.
    assert "gateway=wg-vps" in body
    # The old IP-gateway form must NOT.
    assert "gateway=10.10.0.1" not in body


def test_mangle_uses_dst_address_list_not_dst_address():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    pre = plan.mangle_ops[0]
    assert "dst-address-list" in pre.attrs
    assert pre.attrs["dst-address-list"] == "HOBE_VX2_DST_42"
    assert "dst-address" not in pre.attrs
    assert pre.attrs["chain"] == "prerouting"
    assert pre.attrs["action"] == "mark-routing"
    assert pre.attrs["new-routing-mark"] == "HOBE_VX2_42"
    assert pre.attrs["passthrough"] == "no"


def test_every_managed_command_has_comment_prefix():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target(), _target(id_=2, value="fast.com")],
        wan_interface_list="WAN",
    )
    prefix = plan.comment_prefix
    # cleanup + add ops both carry the prefix — cleanup via
    # find_pattern, adds via attrs["comment"].
    for cmd in plan.cleanup_ops + plan.rollback_ops:
        # VX2.3a — pattern is anchored with `^` and ends with
        # the full prefix (trailing colon included).
        assert cmd.find_pattern == f"^{prefix}", cmd.find_pattern
    for cmd in (
        plan.routing_table_ops + plan.route_ops
        + plan.address_list_ops + plan.dns_ops
        + plan.mangle_ops + plan.firewall_filter_ops
    ):
        cm = cmd.attrs.get("comment", "")
        assert cm.startswith(prefix), \
            f"missing prefix on {cmd.path}: {cm!r}"


def test_cleanup_pattern_is_anchored_and_colon_terminated():
    """VX2.3a — cleanup must use `^HOBE_VX2_SITE_EXIT:<id>:`.
    Without the `^` anchor an unmanaged comment like
    `# see HOBE_VX2_SITE_EXIT:42: notes` would be deleted.
    Without the trailing colon, policy 1 would also match
    policy 10/11/100. Both bugs are silent and dangerous."""
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
    )
    expected = f"^HOBE_VX2_SITE_EXIT:{plan.policy_id}:"
    for cmd in plan.cleanup_ops:
        assert cmd.find_pattern == expected, (
            f"cleanup {cmd.path}: {cmd.find_pattern!r}")
    for cmd in plan.rollback_ops:
        assert cmd.find_pattern == expected


def test_rendered_remove_line_uses_anchored_prefix():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
    )
    forward = r.render_forward_script(plan)
    rollback = r.render_rollback_script(plan)
    anchored = '"^HOBE_VX2_SITE_EXIT:42:"'
    # both forward (cleanup section) and rollback emit the
    # anchored regex.
    assert forward.count(anchored) == len(plan.cleanup_ops)
    assert rollback.count(anchored) == len(plan.rollback_ops)
    # The OLD unanchored variant must NOT appear anywhere.
    assert '"HOBE_VX2_SITE_EXIT:42"' not in forward
    assert '"HOBE_VX2_SITE_EXIT:42"' not in rollback


def test_anchored_pattern_does_not_match_unmanaged_lookalike():
    """Simulate RouterOS's POSIX-regex match on the comment
    column to prove the anchored pattern only catches our own
    rules. We use Python's re for the same semantics — `^`
    anchors to start in both engines."""
    import re
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
    )
    pat = re.compile(plan.cleanup_ops[0].find_pattern)
    # Managed comments — MUST match.
    assert pat.match("HOBE_VX2_SITE_EXIT:42:routing-table")
    assert pat.match("HOBE_VX2_SITE_EXIT:42:target:1:speedtest")
    # Unmanaged lookalikes — MUST NOT match.
    assert not pat.match("see HOBE_VX2_SITE_EXIT:42: in notes")
    assert not pat.match("# HOBE_VX2_SITE_EXIT:42: reference")
    assert not pat.match("MYHOBE_VX2_SITE_EXIT:42:bogus")
    # Different policy id — MUST NOT match.
    assert not pat.match("HOBE_VX2_SITE_EXIT:420:routing-table")
    assert not pat.match("HOBE_VX2_SITE_EXIT:4:foo")  # not 42
    # Same prefix without trailing colon — MUST NOT match
    # (defends against accidental policy-id truncation).
    assert not pat.match("HOBE_VX2_SITE_EXIT:42routing-table")


def test_address_list_never_contains_catch_all():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    for cmd in plan.address_list_ops:
        assert cmd.attrs["address"] != "0.0.0.0/0"


def test_planner_refuses_catch_all_target_as_blocking_error():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[
            {"id": 1, "value": "0.0.0.0/0",
             "normalized_value": "0.0.0.0/0",
             "target_type": "cidr",
             "group_name": "raw_ip_targets",
             "status": "active"}
        ],
    )
    assert not plan.can_apply
    assert any("0.0.0.0/0" in e for e in plan.blocking_errors)


# ─── Planner — blocking conditions ───────────────────────────


def test_planner_blocks_when_exit_node_disabled():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(enabled=0),
        targets=[_target()],
    )
    assert not plan.can_apply
    assert any("disabled" in e for e in plan.blocking_errors)


def test_planner_blocks_when_wireguard_interface_missing():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(wireguard_interface_name=""),
        targets=[_target()],
    )
    assert not plan.can_apply
    assert any("wireguard" in e.lower()
               for e in plan.blocking_errors)


def test_planner_blocks_when_zero_active_targets():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[
            _target(status="disabled"),
            _target(id_=2, value="x.com", status="invalid"),
        ],
    )
    assert not plan.can_apply
    assert any("no active targets" in e
               for e in plan.blocking_errors)


# ─── Planner — fail modes ────────────────────────────────────


def test_block_when_vps_down_with_wan_emits_failsafe_drop():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(fail_mode="block_when_vps_down"),
        exit_node=_node(),
        targets=[_target()],
        wan_interface_list="WAN",
    )
    assert len(plan.firewall_filter_ops) == 1
    rule = plan.firewall_filter_ops[0]
    assert rule.attrs["chain"]  == "forward"
    assert rule.attrs["action"] == "drop"
    assert rule.attrs["dst-address-list"] == "HOBE_VX2_DST_42"
    assert rule.attrs["out-interface-list"] == "WAN"
    # No warning about missing WAN config.
    assert not any("wan_interface_list" in w
                    for w in plan.warnings)


def test_block_when_vps_down_without_wan_warns_not_blocks():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(fail_mode="block_when_vps_down"),
        exit_node=_node(),
        targets=[_target()],
        # No wan_interface_list — still allowed but with warning.
    )
    assert plan.can_apply
    assert plan.firewall_filter_ops == ()
    assert any("wan_interface_list" in w
               for w in plan.warnings)


def test_fallback_to_wan_emits_strong_warning_no_drop():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(fail_mode="fallback_to_wan"),
        exit_node=_node(),
        targets=[_target()],
        wan_interface_list="WAN",
    )
    assert plan.can_apply
    assert plan.firewall_filter_ops == ()
    text = " ".join(plan.warnings).lower()
    assert "fallback_to_wan" in text
    assert "original public ip" in text


# ─── Planner — target handling ───────────────────────────────


def test_include_www_adds_companion_for_root_domain_only():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[
            _target(id_=1, value="example.com",
                     include_www=True),
            _target(id_=2, value="sub.example.com",
                     include_www=True),
        ],
    )
    addrs = [c.attrs["address"] for c in plan.address_list_ops]
    assert "example.com" in addrs
    assert "www.example.com" in addrs       # added for root
    assert "sub.example.com" in addrs
    assert "www.sub.example.com" not in addrs  # NOT added


def test_include_router_output_flag_kept_for_backwards_compat():
    """VX2.6c — output chain is now ALWAYS emitted, so the
    `include_router_output` flag becomes a no-op as far as
    chain emission goes. The attribute is still accepted and
    persisted so we don't break existing policy rows in the
    DB."""
    from app.radius.services import site_exit_script_planner as p
    plan_on = p.build_plan(
        policy=_policy(include_router_output=1),
        exit_node=_node(),
        targets=[_target()],
    )
    plan_off = p.build_plan(
        policy=_policy(include_router_output=0),
        exit_node=_node(),
        targets=[_target()],
    )
    on_chains = sorted(c.attrs["chain"] for c in plan_on.mangle_ops)
    off_chains = sorted(c.attrs["chain"] for c in plan_off.mangle_ops)
    # Both produce the same shape — output chain is unconditional.
    assert on_chains == off_chains == ["output", "prerouting"]


def test_disabled_targets_are_skipped_not_silently_dropped():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[
            _target(id_=1, value="speedtest.net"),
            _target(id_=2, value="fast.com", status="disabled"),
            _target(id_=3, value="x.com", status="invalid"),
        ],
    )
    addrs = [c.attrs["address"] for c in plan.address_list_ops]
    # active only.
    assert "speedtest.net" in addrs
    assert "fast.com" not in addrs
    assert "x.com" not in addrs
    # skip reasons are surfaced.
    skipped_values = {s.value for s in plan.targets_skipped}
    assert "fast.com" in skipped_values
    assert "x.com" in skipped_values


def test_duplicate_normalized_targets_only_counted_once():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[
            _target(id_=1, value="speedtest.net"),
            _target(id_=2, value="SPEEDTEST.NET"),
        ],
    )
    # only one address-list entry for speedtest.net (plus its
    # www. companion).
    addrs = [c.attrs["address"] for c in plan.address_list_ops]
    assert addrs.count("speedtest.net") == 1


# ─── Planner — DNS helper opt-in ─────────────────────────────


def test_include_subdomains_without_dns_helper_only_warns():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(include_subdomains=1),
        exit_node=_node(),
        targets=[_target(include_subdomains=True)],
    )
    assert plan.dns_ops == ()
    assert any("DNS helper mode is OFF" in w
               for w in plan.warnings)


def test_include_subdomains_with_dns_helper_emits_dns_ops():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(include_subdomains=1),
        exit_node=_node(),
        targets=[
            _target(id_=1, value="example.com",
                     include_subdomains=True),
        ],
        enable_dns_helper=True,
    )
    assert len(plan.dns_ops) == 1
    dns = plan.dns_ops[0]
    assert dns.path == "/ip/dns/static"
    assert dns.attrs["address-list"] == "HOBE_VX2_DST_42"
    # Regex should match subdomains of example.com with
    # literal escapes for the dots (SINGLE backslashes in the
    # Python string — the renderer doubles them at emit time).
    assert dns.attrs["regexp"] == r"^.*\.example\.com$"


# ─── Rollback ────────────────────────────────────────────────


def test_rollback_only_touches_managed_comments():
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
    )
    pattern = plan.comment_prefix.rstrip(":")
    for cmd in plan.rollback_ops:
        assert cmd.kind == "remove"
        assert pattern in cmd.find_pattern


def test_cleanup_and_rollback_have_identical_patterns():
    """Both run the same set of remove ops; the difference is
    intent (cleanup before re-apply vs full rollback). The
    patterns themselves are the same — and that's the whole
    safety story for both flows."""
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
    )
    c_paths = sorted(c.path for c in plan.cleanup_ops)
    r_paths = sorted(c.path for c in plan.rollback_ops)
    assert c_paths == r_paths


# ─── Renderer ────────────────────────────────────────────────


def test_renderer_emits_safe_forward_script():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
        wan_interface_list="WAN",
    )
    body = r.render_forward_script(plan)
    assert "HOBE_VX2_SITE_EXIT:42:" in body
    # Header + key commands present.
    assert "/routing table add" in body
    assert "/ip route add" in body
    assert "dst-address=0.0.0.0/0" in body
    assert "routing-table=HOBE_VX2_42" in body
    assert "/ip firewall address-list add" in body
    assert "/ip firewall mangle add" in body
    assert "chain=prerouting" in body
    assert "dst-address-list=HOBE_VX2_DST_42" in body
    assert "/ip firewall filter add" in body  # failsafe


def test_renderer_returns_empty_string_for_blocked_plan():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(enabled=0),
        targets=[_target()],
    )
    assert not plan.can_apply
    assert r.render_forward_script(plan) == ""


def test_renderer_rollback_only_emits_managed_removes():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target()],
    )
    body = r.render_rollback_script(plan)
    # Every non-comment, non-blank line must be a `remove
    # [find comment~"HOBE_VX2_SITE_EXIT:42"]` form.
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assert "remove" in line
        assert "HOBE_VX2_SITE_EXIT:42" in line
        assert "find comment~" in line


def test_renderer_idempotent_same_plan_same_bytes():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()], wan_interface_list="WAN",
    )
    a = r.render_forward_script(plan)
    b = r.render_forward_script(plan)
    assert a == b
    assert r.script_hash(a) == r.script_hash(b)


def test_renderer_refuses_to_emit_private_key_tripwire():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    # Construct a plan with a sabotaged target value containing
    # a tripwire — this should never happen via the validator,
    # but the renderer is the last line of defence.
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[_target(value="private-key=LEAKED")],
    )
    # The validator should have rejected this upstream so the
    # plan can fail blocking OR if it slipped through, the
    # renderer must refuse loudly. Either is acceptable.
    if plan.can_apply:
        with pytest.raises(r.RenderSafetyError):
            r.render_forward_script(plan)
    else:
        assert r.render_forward_script(plan) == ""


def test_renderer_refuses_route_with_main_table_default():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    # Build a legit plan, then mutate a route command to look
    # like an attempted main-table hijack.
    legit = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    # Replace the route op with a tampered version.
    from dataclasses import replace
    tampered = replace(
        legit.route_ops[0],
        attrs={
            **legit.route_ops[0].attrs,
            "routing-table": "main",
        },
    )
    with pytest.raises(r.RenderSafetyError):
        r.render_command(tampered, plan=legit)


def test_renderer_refuses_add_without_comment_prefix():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    legit = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    from dataclasses import replace
    tampered = replace(
        legit.address_list_ops[0],
        attrs={
            **legit.address_list_ops[0].attrs,
            "comment": "some-other-tool-prefix",
        },
    )
    with pytest.raises(r.RenderSafetyError):
        r.render_command(tampered, plan=legit)


def test_renderer_quotes_values_with_special_chars():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    # The DNS helper regex contains backslashes — exercises the
    # quoting+escaping path.
    plan = p.build_plan(
        policy=_policy(include_subdomains=1),
        exit_node=_node(),
        targets=[_target(value="example.com",
                          include_subdomains=True)],
        enable_dns_helper=True,
    )
    body = r.render_forward_script(plan)
    # The regex literal should appear with DOUBLED backslashes
    # — that's how RouterOS parses `\\.` back to `\.`.
    assert r'regexp="^.*\\.example\\.com$"' in body


def test_renderer_emits_no_default_route_outside_custom_table():
    """Scan the entire body for any 0.0.0.0/0 that is NOT in
    the same line as routing-table=HOBE_VX2_<id>. Belt-and-
    braces for the planner's assertion."""
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()], wan_interface_list="WAN",
    )
    body = r.render_forward_script(plan)
    for line in body.splitlines():
        if "0.0.0.0/0" in line:
            assert "routing-table=HOBE_VX2_42" in line


def test_script_summary_matches_plan_counts():
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(),
        exit_node=_node(),
        targets=[
            _target(id_=1, value="speedtest.net"),
            _target(id_=2, value="fast.com"),
        ],
        wan_interface_list="WAN",
    )
    s = r.script_summary(plan)
    assert s["policy_id"] == 42
    assert s["address_list"] == "HOBE_VX2_DST_42"
    assert s["routing_table"] == "HOBE_VX2_42"
    assert s["section_counts"]["routing_table"] == 1
    assert s["section_counts"]["route"] == 1
    # VX2.6c — mangle now always emits BOTH prerouting + output.
    assert s["section_counts"]["mangle"] == 2
    # VX2.6c — src-NAT on wg interface emitted unconditionally.
    assert s["section_counts"]["nat"] == 1
    assert s["section_counts"]["firewall_filter"] == 1
    # 2 root domains × (1 base + 1 www companion) = 4 entries.
    assert s["section_counts"]["address_list"] == 4
    assert s["command_count"] == plan.total_commands


# ─── Determinism / purity ────────────────────────────────────


def test_planner_is_pure_no_app_context_needed():
    """Same as VX2.2 — if this raises, the planner broke its
    pure contract."""
    from app.radius.services import site_exit_script_planner as p
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()],
    )
    assert plan.policy_id == 42


def test_every_plan_carries_a_fasttrack_warning():
    """VX2.3a — FastTrack interferes with mangle-based policy
    routing. The advisory must be on every plan that can apply,
    regardless of fail_mode or include_subdomains."""
    from app.radius.services import site_exit_script_planner as p
    plan_a = p.build_plan(
        policy=_policy(fail_mode="block_when_vps_down"),
        exit_node=_node(),
        targets=[_target()],
        wan_interface_list="WAN",
    )
    plan_b = p.build_plan(
        policy=_policy(fail_mode="fallback_to_wan",
                        include_subdomains=1),
        exit_node=_node(),
        targets=[_target(include_subdomains=True)],
    )
    for plan in (plan_a, plan_b):
        joined = " ".join(plan.warnings)
        assert "FastTrack" in joined


def test_renderer_command_count_matches_emitted_lines():
    """The summary's command_count must equal the number of
    non-comment, non-blank lines in the forward script."""
    from app.radius.services import site_exit_script_planner as p
    from app.radius.services import site_exit_script_renderer as r
    plan = p.build_plan(
        policy=_policy(), exit_node=_node(),
        targets=[_target()], wan_interface_list="WAN",
    )
    body = r.render_forward_script(plan)
    effective = [
        ln for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert len(effective) == plan.total_commands
