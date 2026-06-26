# -*- coding: utf-8 -*-
"""HobeRadius one-paste onboarding script generator.

The two hard requirements:
  1. ORIGINALITY — HobeRadius-native naming/voice, unique per-router secrets,
     never a shared constant.
  2. FIREWALL ORDERING — allow (established + mgmt iface + RADIUS + DNS +
     walled-garden) BEFORE any reject/redirect; the mgmt path can never be
     dropped; re-paste is idempotent.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import re

import pytest

from app.radius.services.router_onboarding_script import (
    OnboardingParams, OnboardingScriptError, build_onboarding_script,
    firewall_rule_order, FW_TAG, WALLED_GARDEN_LIST, EXPIRED_LIST,
)


def _params(**over):
    base = dict(
        router_name="مقهى النور", router_id=7,
        accel_host="187.77.70.18", sstp_port=443,
        tunnel_user="rtr-cafe-noor", tunnel_password="Uniq-Pw-abc123XYZ",
        tunnel_ip="10.50.0.5", radius_ip="10.50.0.1",
        radius_secret="per-nas-secret-9931", api_user="hobe-api",
        api_password="api-uniq-77team", walled_garden=["renew.hoberadius.com", "1.2.3.4"],
        block_page_url="http://renew.hoberadius.com", hotspot_pool="10.5.50.0/24",
        pppoe_pool="10.5.60.0/24",
    )
    base.update(over)
    return OnboardingParams(**base)


def _fw_add_lines(script):
    return [ln for ln in script.splitlines()
            if "/ip firewall filter add" in ln]


# ════════════ build + parameterization ════════════
def test_builds_and_is_parameterized():
    s = build_onboarding_script(_params())
    # real per-router data is woven in (no placeholders/constants)
    assert "rtr-cafe-noor" in s
    assert "Uniq-Pw-abc123XYZ" in s            # unique tunnel password
    assert "per-nas-secret-9931" in s          # unique NAS RADIUS secret
    assert "187.77.70.18" in s and "10.50.0.1" in s
    assert "renew.hoberadius.com" in s         # walled-garden entry
    # all nine numbered sections present (pools added as 3)
    for marker in ("١) نفق", "٢) RADIUS", "٣) مجمّعات", "٤) مستخدم API",
                   "٥) الجدار", "٦) إعادة توجيه", "٧) تقليص", "٨) الإصلاح",
                   "٩) نسخة"):
        assert marker in s, f"section missing: {marker}"


def test_originality_markers():
    s = build_onboarding_script(_params())
    # our naming everywhere
    assert "hr-sstp-mgmt" in s and "hr-walled-garden" in s and "hr-fw:" in s
    assert "hobe-api" in s
    # NOT a shared constant secret like the competitor's 868868
    assert "868868" not in s
    # bilingual AR/EN comment voice present
    assert "الجدار الناريّ" in s and "Firewall" in s


# ════════════ FIREWALL ORDERING (the priority) ════════════
def test_allow_rules_before_any_reject_or_redirect():
    order = firewall_rule_order(build_onboarding_script(_params()))
    # locate the first reject/redirect-ish rule (expiry handling)
    reject_idx = next(i for i, c in enumerate(order) if "expired" in c)
    allow_keys = ["established", "mgmt SSTP iface", "from RADIUS",
                  "DNS to router", "walled-garden allow", "to RADIUS",
                  "DNS forward"]
    for key in allow_keys:
        idx = next(i for i, c in enumerate(order) if key in c)
        assert idx < reject_idx, f"allow '{key}' must precede expiry reject"


def test_forward_chain_order_allow_then_reject_then_default():
    order = firewall_rule_order(build_onboarding_script(_params()))
    wg = next(i for i, c in enumerate(order) if "walled-garden allow" in c)
    exp = next(i for i, c in enumerate(order) if "expired pool reject" in c)
    dflt = next(i for i, c in enumerate(order) if "default active accept" in c)
    assert wg < exp < dflt   # allow → reject expired → default accept


def test_established_and_mgmt_are_the_first_two_rules():
    order = firewall_rule_order(build_onboarding_script(_params()))
    assert "established" in order[0]
    assert "mgmt SSTP iface" in order[1]   # the mgmt path is rule #2, before all else


def test_no_rule_drops_or_rejects_the_mgmt_interface():
    for ln in _fw_add_lines(build_onboarding_script(_params())):
        if 'in-interface="hr-sstp-mgmt"' in ln or 'out-interface="hr-sstp-mgmt"' in ln:
            assert "action=accept" in ln
            assert "action=drop" not in ln and "action=reject" not in ln


def test_mgmt_and_radius_have_explicit_accept():
    lines = _fw_add_lines(build_onboarding_script(_params()))
    assert any('in-interface="hr-sstp-mgmt"' in l and "action=accept" in l for l in lines)
    assert any("src-address=10.50.0.1" in l and "action=accept" in l for l in lines)


def test_move_to_top_lifts_whole_block_in_order():
    s = build_onboarding_script(_params())
    # the idempotent "lift our block to the top of each chain" loop
    assert f'/ip firewall filter find comment~"^{FW_TAG}"' in s
    assert "move $r destination=$hrPos" in s


# ════════════ IDEMPOTENCY (re-paste safe) ════════════
def test_managed_block_removed_before_readd():
    s = build_onboarding_script(_params())
    # our filter block is removed before being rebuilt → no duplicates on re-paste
    assert f'/ip firewall filter remove [find comment~"^{FW_TAG}"]' in s
    # the firewall remove appears BEFORE any firewall add
    rm = s.index(f'filter remove [find comment~"^{FW_TAG}"]')
    first_add = s.index("/ip firewall filter add")
    assert rm < first_add


def test_other_objects_are_find_guarded():
    s = build_onboarding_script(_params())
    # tunnel, radius, api user, scheduler, netwatch all remove-by-find first
    assert '/interface sstp-client remove [find name="hr-sstp-mgmt"]' in s
    assert '/radius remove [find comment="hr: HobeRadius RADIUS"]' in s
    assert '/user remove [find name="hobe-api"]' in s
    assert '/system scheduler remove [find name="hr-sstp-watchdog"]' in s


def test_walled_garden_always_includes_radius_and_server():
    s = build_onboarding_script(_params(walled_garden=[]))
    # even with an empty operator list, RADIUS + SSTP server are always allowed
    assert f'address-list add list="{WALLED_GARDEN_LIST}" address=10.50.0.1' in s
    assert f'address-list add list="{WALLED_GARDEN_LIST}" address=187.77.70.18' in s


# ════════════ safety / validation ════════════
def test_injection_in_param_is_rejected():
    with pytest.raises(Exception):
        build_onboarding_script(_params(tunnel_password='pw" ; /system reset'))


def test_weak_secret_rejected():
    with pytest.raises(OnboardingScriptError):
        build_onboarding_script(_params(tunnel_password="short"))
    with pytest.raises(OnboardingScriptError):
        build_onboarding_script(_params(radius_secret="x"))


def test_tunnel_uses_profile_default_not_encryption():
    s = build_onboarding_script(_params())
    cmd = [l for l in s.splitlines() if l.startswith("/interface sstp-client add")][0]
    assert "profile=default " in cmd
    assert "default-encryption" not in cmd
    assert "verify-server-certificate=no" in cmd


def test_pools_section_uses_configured_cidrs():
    s = build_onboarding_script(_params(hotspot_pool="10.9.9.0/24",
                                        pppoe_pool="10.8.8.0/24"))
    assert 'name="hr-hotspot-pool" ranges=10.9.9.2-10.9.9.254' in s
    assert 'name="hr-pppoe-pool" ranges=10.8.8.2-10.8.8.254' in s


# ════════════ SSTP flapping fixes (live ccr5: Link Downs=49) ════════════
def _line(script, needle):
    return next(l for l in script.splitlines() if needle in l)


def test_sstp_client_disables_address_from_cert_verification():
    """Self-signed cert (CN=name) reached by IP → address-from-cert re-checks
    fail periodically and flap the tunnel. Must be explicitly off."""
    s = build_onboarding_script(_params())
    add = _line(s, "/interface sstp-client add")
    assert "verify-server-address-from-certificate=no" in add
    assert "verify-server-certificate=no" in add
    assert "profile=default" in add


def test_keepalive_timeout_is_reasonable():
    add = _line(build_onboarding_script(_params()), "/interface sstp-client add")
    m = re.search(r"keepalive-timeout=(\d+)", add)
    assert m and 20 <= int(m.group(1)) <= 120


def test_watchdog_never_disables_a_running_interface():
    """The 2m watchdog must only ensure exists+enabled — never disable/bounce."""
    sched = _line(build_onboarding_script(_params()), "/system scheduler add")
    assert "sstp-client disable" not in sched          # never bounces
    # it only enables when actually disabled (boolean used directly, not =true)
    assert "disabled]) do={/interface sstp-client enable" in sched


def _script_value(line, key):
    """Extract a RouterOS on-event=/down-script= quoted value from a line."""
    return re.search(key + r'="(.*?)" comment=', line).group(1)


def test_self_heal_values_are_ascii_only():
    """ROS 7.20 bug: a non-ASCII char (the old em-dash in the watchdog log
    message) corrupts the stored scheduler script's re-parse → "missing value
    of argument value" every 2m. The stored on-event/down-script values must be
    pure ASCII."""
    s = build_onboarding_script(_params())
    sched = _line(s, "/system scheduler add")
    net = _line(s, "/tool netwatch add")
    assert _script_value(sched, "on-event").isascii()
    assert _script_value(net, "down-script").isascii()


def test_self_heal_commands_are_guarded_by_len_check():
    """Empty `find` must never reach a get/enable/disable with an empty $id (the
    "missing value of argument value" trap). Every command in BOTH scripts must
    come after a `[:len $id] > 0` guard, and braces must balance."""
    s = build_onboarding_script(_params())
    for line, key in ((_line(s, "/system scheduler add"), "on-event"),
                      (_line(s, "/tool netwatch add"), "down-script")):
        v = _script_value(line, key)
        guard = v.find("[:len $id]")
        assert guard >= 0
        first_cmd = min(i for i in (v.find("get $id"), v.find("enable $id"),
                                    v.find("disable $id")) if i >= 0)
        assert guard < first_cmd                       # guard precedes any command
        assert "> 0" in v[guard:guard + 14]            # it's a non-empty check
        assert v.count("{") == v.count("}")            # balanced blocks


def test_watchdog_missing_interface_only_logs():
    """When find returns empty, the watchdog logs and does NOTHING else — no
    command runs against an empty target."""
    sched = _line(build_onboarding_script(_params()), "/system scheduler add")
    v = _script_value(sched, "on-event")
    # the else (empty-find) branch contains a log and no interface command
    else_branch = v[v.rfind("else={"):]
    assert ":log warning" in else_branch
    assert "sstp-client" not in else_branch


def test_netwatch_downscript_bounce_is_guarded_by_running_state():
    """The netwatch down-script may only disable+enable when the interface is
    genuinely down (running=false) — never blindly, never when running."""
    net = _line(build_onboarding_script(_params()), "/tool netwatch add")
    # no blind enable of the old form
    assert 'down-script="/interface sstp-client enable [find' not in net
    # any disable is inside the running=false guard
    assert "running]=false) do={/interface sstp-client disable" in net
    # and the script is state-guarded (checks the interface first)
    assert ":if ([:len $id]" in net


def test_netwatch_timeout_raised_so_a_blip_is_not_a_down():
    net = _line(build_onboarding_script(_params()), "/tool netwatch add")
    assert "timeout=5s" in net and "timeout=2s" not in net
    assert "interval=60s" in net


def test_self_heal_lines_are_single_console_lines():
    """on-event / down-script carry no embedded newline (paste-safe)."""
    s = build_onboarding_script(_params())
    for needle in ("/system scheduler add", "/tool netwatch add"):
        assert _line(s, needle).count("\n") == 0


# ════════════ authoritative / clean-then-apply (own the managed config) ════════════
def _cmd_lines(script):
    """Non-comment, non-blank command lines."""
    return [ln for ln in script.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_radius_disables_existing_before_adding_ours():
    """ADV-style ownership: disable any pre-existing/competitor RADIUS BEFORE
    adding ours, so the router authenticates against ours only."""
    s = build_onboarding_script(_params())
    disable = ":foreach r in=[/radius find] do={ /radius disable $r }"
    assert disable in s
    # the disable-all runs BEFORE our radius add (and before our remove)
    assert s.index(disable) < s.index("/radius add address=")
    assert s.index(disable) < s.index('/radius remove [find comment="hr: HobeRadius RADIUS"]')


def test_radius_entry_is_remove_then_add_comment_scoped():
    """Our radius entry is remove-then-add keyed on OUR comment (idempotent; a
    re-paste leaves exactly one of ours, and the remove is scoped to our tag)."""
    s = build_onboarding_script(_params())
    rm = '/radius remove [find comment="hr: HobeRadius RADIUS"]'
    add = next(l for l in s.splitlines() if l.startswith("/radius add address="))
    assert rm in s and s.index(rm) < s.index(add)
    assert 'comment="hr: HobeRadius RADIUS"' in add          # tagged so re-paste dedupes


def test_tunnel_disables_other_sstp_clients_to_our_endpoint():
    """Disable any OTHER sstp-client dialing OUR server (so two clients can't
    fight over the same rtr-* account), scoped to our endpoint + excluding our
    managed name — never the customer's unrelated VPNs."""
    p = _params()
    s = build_onboarding_script(p)
    line = next(l for l in s.splitlines()
                if l.startswith(":foreach c in=[/interface sstp-client find connect-to="))
    assert f'connect-to="{p.accel_host}"' in line             # scoped to OUR endpoint
    assert f'!= "{p.mgmt_iface}"' in line                     # excludes OUR managed client
    assert "/interface sstp-client disable $c" in line        # disables, not deletes
    # runs before we (re)create ours
    assert s.index(line) < s.index('/interface sstp-client add name=')


def test_tunnel_removes_stale_our_named_pptp():
    s = build_onboarding_script(_params())
    rm = '/interface pptp-client remove [find name="hr-pptp-mgmt"]'
    assert rm in s
    assert s.index(rm) < s.index('/interface sstp-client add name=')


def test_cleanup_does_not_touch_unscoped_objects():
    """The cleanup must key on our name/comment/endpoint — never a blanket
    `remove [find]` of all interfaces/users/pools (which would hit the customer's
    own config)."""
    s = build_onboarding_script(_params())
    # no unscoped interface/user/pool wipe
    for danger in ("/interface sstp-client remove [find]",
                   "/interface pptp-client remove [find]",
                   "/interface remove [find]",
                   "/user remove [find]",
                   "/ip pool remove [find]",
                   "/radius remove [find]"):                  # radius is DISABLE-all, not remove-all
        assert danger not in s
    # our removes are all scoped by name= or comment=
    for ln in _cmd_lines(s):
        if "remove [find" in ln:
            assert ("name=" in ln or "comment" in ln), f"unscoped remove: {ln}"


def test_cleanup_foreach_lines_are_ascii_guarded_and_balanced():
    """The disable :foreach constructs are paste-safe: ASCII-only, balanced
    braces, and guarded (a :foreach over an empty find is a no-op — never the
    empty-$id 'missing value of argument' trap)."""
    s = build_onboarding_script(_params())
    foreach_lines = [l for l in s.splitlines()
                     if ":foreach" in l and not l.lstrip().startswith("#")]
    # at least: radius-disable + tunnel-cleanup + the firewall move-to-top
    assert len(foreach_lines) >= 3
    for l in foreach_lines:
        assert l.isascii(), f"non-ASCII in stored/console construct: {l!r}"
        assert l.count("{") == l.count("}"), f"unbalanced braces: {l!r}"
        assert l.count("\n") == 0                             # single console line


def test_disable_actions_use_guarded_foreach_not_bare_scalar():
    """We never call enable/disable on a bare scalar that could be empty; the
    disable-others actions iterate a find via :foreach (safe on empty)."""
    s = build_onboarding_script(_params())
    assert ":foreach r in=[/radius find] do={ /radius disable $r }" in s
    assert any(l.startswith(":foreach c in=[/interface sstp-client find connect-to=")
               for l in s.splitlines())
