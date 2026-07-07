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


def test_service_lockdown_uses_combined_mgmt_acl():
    """The SSTP onboarding script must bind WinBox/API/web to a COMBINED
    allow-list — the SSTP/RADIUS gateway /32 AND the WireGuard management subnet
    — because `/ip service set address=` REPLACES. Without the WG subnet,
    pasting this SSTP script would clobber WinBox-over-WireGuard. SSTP gateway
    leads (this script's own path). Strictly tunnel-only, no WAN."""
    s = build_onboarding_script(_params(radius_ip="10.50.0.1"))
    for svc in ("winbox", "api", "www"):
        assert f"/ip service set {svc} address=10.50.0.1/32,10.10.0.0/24" in s
    # no bare SSTP-only line survives (would clobber the WG path)
    assert "/ip service set winbox address=10.50.0.1/32\n" not in s
    assert "0.0.0.0/0" not in s                 # never the WAN


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


def test_forward_chain_order_allow_then_reject_no_broad_accept():
    """allow (walled-garden) → reject expired, and NO broad forward accept at
    all. A general `chain=forward action=accept` in our block would be lifted
    above the router's Hotspot dynamic rules and break the captive portal."""
    s = build_onboarding_script(_params())
    order = firewall_rule_order(s)
    wg = next(i for i, c in enumerate(order) if "walled-garden allow" in c)
    exp = next(i for i, c in enumerate(order) if "expired pool reject" in c)
    assert wg < exp                              # allow → reject expired
    assert not any("default active accept" in c for c in order)


def test_no_unconditional_forward_accept_in_managed_block():
    """Regression (iPhone captive.apple.com «server cannot be found»): the
    generated script must NOT add an unconditional forward accept — it would
    sit above the Hotspot hs-unauth/hs-auth rules after the move-to-top."""
    s = build_onboarding_script(_params())
    assert 'chain=forward action=accept comment="hr-fw: 99 default active accept"' not in s
    # no hr-fw forward rule is a bare `action=accept` with no match condition
    for ln in _fw_add_lines(s):
        if "chain=forward" in ln and "action=accept" in ln:
            assert any(tok in ln for tok in (
                "connection-state=", "dst-address", "out-interface=",
                "dst-port=", "src-address")), \
                f"broad forward accept leaked: {ln}"


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
    assert '/system scheduler remove [find name=hr-sstp-watchdog]' in s


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


def test_tunnel_uses_profile_default_encryption():
    """Owner decision (2026): the SSTP mgmt tunnel uses profile=default-encryption
    (PPP/MPPE enabled at profile level) — NOT the bare `default`."""
    s = build_onboarding_script(_params())
    cmd = [l for l in s.splitlines() if l.startswith("/interface sstp-client add")][0]
    assert "profile=default-encryption" in cmd
    assert "profile=default " not in cmd            # not the bare default profile
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
    assert "profile=default-encryption" in add


def test_keepalive_timeout_is_reasonable():
    add = _line(build_onboarding_script(_params()), "/interface sstp-client add")
    m = re.search(r"keepalive-timeout=(\d+)", add)
    assert m and 20 <= int(m.group(1)) <= 120


# ════════════ RouterOS 6 vs 7 SSTP compatibility ════════════
def test_v7_sstp_command_is_full():
    """RouterOS 7 (default) keeps the full command — including the props that
    v6 rejects but v7 needs (verify-server-address-from-certificate=no is
    required on v7 or the name-CN cert re-verification flaps the tunnel)."""
    add = _line(build_onboarding_script(_params(ros_version="7")),
                "/interface sstp-client add")
    assert "verify-server-address-from-certificate=no" in add
    assert "port=443" in add
    assert "keepalive-timeout=30" in add
    assert "verify-server-certificate=no" in add


def test_v6_sstp_command_omits_unsupported_props():
    """RouterOS 6 legacy: the props that make `add` FAIL (so hr-sstp-mgmt is
    never created) are stripped; the supported ones are kept."""
    add = _line(build_onboarding_script(_params(ros_version="6")),
                "/interface sstp-client add")
    assert "verify-server-address-from-certificate" not in add
    assert "port=" not in add
    assert "keepalive-timeout" not in add
    # kept — supported on v6
    assert "verify-server-certificate=no" in add
    assert "profile=default-encryption" in add     # owner decision — kept on v6 too
    assert 'name="hr-sstp-mgmt"' in add
    assert "add-default-route=no" in add


def test_v6_variants_all_detected_as_legacy():
    for v in ("6", "6.48.6", "6.4"):
        add = _line(build_onboarding_script(_params(ros_version=v)),
                    "/interface sstp-client add")
        assert "keepalive-timeout" not in add, v
        assert "verify-server-address-from-certificate" not in add, v


def test_unknown_version_defaults_to_v7_full():
    add = _line(build_onboarding_script(_params(ros_version="")),
                "/interface sstp-client add")
    assert "verify-server-address-from-certificate=no" in add


def test_profile_default_encryption_owner_decision_both_versions():
    """Owner decision (2026): hr-sstp-mgmt uses profile=default-encryption on
    BOTH v6 and v7; bare `profile=default ` must never appear. v7 carries the
    address-from-cert flag; v6 must NOT (it breaks v6)."""
    for v in ("6", "7"):
        add = _line(build_onboarding_script(_params(ros_version=v)),
                    "/interface sstp-client add")
        assert "profile=default-encryption" in add, v
        assert "profile=default " not in add, v          # never the bare default
        assert "verify-server-certificate=no" in add, v
        assert "add-default-route=no" in add, v
    v7 = _line(build_onboarding_script(_params(ros_version="7")), "/interface sstp-client add")
    v6 = _line(build_onboarding_script(_params(ros_version="6")), "/interface sstp-client add")
    assert "verify-server-address-from-certificate=no" in v7
    assert "verify-server-address-from-certificate" not in v6
    assert "port=" not in v6 and "keepalive-timeout" not in v6


def test_route_to_radius_added_only_after_interface_exists():
    """The RADIUS route (gateway = hr-sstp-mgmt) is added AFTER the sstp-client
    add, and guarded so it never creates an orphan route if the interface is
    missing (e.g. a v7 command pasted on a v6 router)."""
    for v in ("6", "7", ""):
        s = build_onboarding_script(_params(ros_version=v))
        add_idx = s.index('/interface sstp-client add name="hr-sstp-mgmt"')
        route_ln = _line(s, "/ip route add dst-address=")
        assert s.index(route_ln) > add_idx, v          # route after the add
        assert ':if ([:len [/interface sstp-client find name="hr-sstp-mgmt"]] > 0)' in s, v


def _script_value(line, key):
    """Extract a RouterOS on-event=/down-script= quoted value. In the run-by-name
    form both are `/system script run NAME` (no inner quotes) followed by
    ` comment=`."""
    return re.search(key + r'="(.*?)" comment=', line).group(1)


def test_self_heal_values_are_ascii_only():
    """Every stored on-event/down-script value must be pure ASCII (a non-ASCII
    char once corrupted the scheduler re-parse)."""
    s = build_onboarding_script(_params())
    sched = _line(s, "/system scheduler add")
    net = _line(s, "/tool netwatch add")
    assert _script_value(sched, "on-event").isascii()
    assert _script_value(net, "down-script").isascii()


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


# ════════════ self-heal: FIELD-VERIFIED run-by-name form (owner adopted) ════════════
def _selfheal_lines(script):
    return [l for l in script.splitlines()
            if l.startswith(("/system script add", "/system scheduler add",
                             "/system scheduler remove", "/system script remove",
                             "/tool netwatch add", "/tool netwatch remove"))]


def _stored(line, key):
    m = re.search(key + r'="(.*?)"', line)
    return m.group(1) if m else None


def _source(line):
    i = line.find("source=")
    return line[i + len("source="):] if i >= 0 else None


def test_owner_verified_watchdog_lines_are_emitted_verbatim():
    """main MUST equal the exact construct the owner pasted on ccr5 and adopted
    («اعتمد») — the field-verified run-by-name form. These four lines copy-paste
    identical."""
    s = build_onboarding_script(_params())
    for line in (
        "/system scheduler remove [find name=hr-sstp-watchdog]",
        "/system script remove [find name=hr-sstp-watchdog-fn]",
        "/system script add name=hr-sstp-watchdog-fn source={:if ([:len "
        "[/interface sstp-client find name=hr-sstp-mgmt disabled=yes]] > 0) "
        "do={/interface sstp-client enable [/interface sstp-client find "
        "name=hr-sstp-mgmt disabled=yes]}}",
        '/system scheduler add name=hr-sstp-watchdog interval=2m '
        'start-time=startup on-event="/system script run hr-sstp-watchdog-fn" '
        'comment="hr: re-enable mgmt tunnel if disabled"',
    ):
        assert line in s, line


def test_scheduler_and_netwatch_only_run_a_named_script():
    """The stored on-event/down-script must be a bare `/system script run NAME`
    — NO brackets, NO $, NO nested double-quotes (the ROS-7.20 store+reparse
    traps that produced 'missing value of argument value')."""
    s = build_onboarding_script(_params())
    sched = next(l for l in s.splitlines() if l.startswith("/system scheduler add"))
    net = next(l for l in s.splitlines() if l.startswith("/tool netwatch add"))
    for val in (_stored(sched, "on-event"), _stored(net, "down-script")):
        assert val.startswith("/system script run ")
        assert '"' not in val
        assert "$" not in val
        assert "[" not in val and "]" not in val
        assert val.isascii()


def test_selfheal_source_blocks_have_no_quote_dollar_or_get():
    """The logic lives in `source={...}` literal blocks: no double-quote (unquoted
    iface name), no `$` variable, no `get`; balanced braces/brackets; ASCII."""
    s = build_onboarding_script(_params())
    adds = [l for l in s.splitlines() if l.startswith("/system script add")]
    assert len(adds) == 2                              # watchdog-fn + reheal-fn
    for line in adds:
        src = _source(line)
        assert src.startswith("{") and src.endswith("}")
        assert '"' not in src
        assert "$" not in src
        assert "get " not in src
        assert src.isascii()
        assert src.count("{") == src.count("}")
        assert src.count("[") == src.count("]")


def test_selfheal_uses_find_filter_not_get():
    s = build_onboarding_script(_params())
    section = "\n".join(_selfheal_lines(s))
    assert "get " not in section
    assert "find name=hr-sstp-mgmt disabled=yes]] > 0" in section
    assert ("enable [/interface sstp-client find name=hr-sstp-mgmt disabled=yes]"
            in section)


def test_selfheal_is_non_flapping():
    """Watchdog never disables (only enables a disabled client). The reheal
    disables ONLY inside the running=no branch (bounce a genuinely stuck one)."""
    s = build_onboarding_script(_params())
    wd = _source(next(l for l in s.splitlines()
                      if l.startswith("/system script add name=hr-sstp-watchdog-fn")))
    rh = _source(next(l for l in s.splitlines()
                      if l.startswith("/system script add name=hr-sstp-reheal-fn")))
    assert "sstp-client disable [" not in wd
    assert "running=no]] > 0) do={/interface sstp-client disable [" in rh


def test_selfheal_authoritative_cleanup_removes_old_objects():
    """Re-paste removes the OLD scheduler AND both /system script fns first."""
    s = build_onboarding_script(_params())
    assert "/system scheduler remove [find name=hr-sstp-watchdog]" in s
    assert "/system script remove [find name=hr-sstp-watchdog-fn]" in s
    assert "/system script remove [find name=hr-sstp-reheal-fn]" in s
    assert s.index("/system script remove [find name=hr-sstp-watchdog-fn]") < \
        s.index("/system script add name=hr-sstp-watchdog-fn")


def test_no_inline_global_or_get_id_watchdog_regression():
    """Guard against regressing to either the get-$id inline form OR the
    unverified inline-global mirror."""
    s = build_onboarding_script(_params())
    assert "get $id" not in s
    assert 'on-event=":local id' not in s
    assert "global hrSstpIf" not in s              # not the unverified inline form


# ════════════ full-script idempotency: every managed add is remove-before-add ════════════
def test_onboarding_is_fully_idempotent_remove_before_add():
    """Re-pasting the whole script must converge — every managed object class is
    remove-(or disable)-before-add, scoped to OUR name/comment tag, so nothing we
    own ever piles up duplicates on re-run."""
    s = build_onboarding_script(_params(block_page_url="http://203.0.113.9/p/expired"))

    def before(remove_substr, add_substr):
        assert remove_substr in s, f"missing remove: {remove_substr}"
        assert add_substr in s, f"missing add: {add_substr}"
        assert s.index(remove_substr) < s.index(add_substr), \
            f"add not preceded by remove: {add_substr}"

    before('/ppp profile remove [find name="hr-mgmt-profile"]',
           '/ppp profile add name="hr-mgmt-profile"')
    before('/interface sstp-client remove [find name="hr-sstp-mgmt"]',
           '/interface sstp-client add name="hr-sstp-mgmt"')
    before('/ip route remove [find comment="hr: route to RADIUS"]',
           '/ip route add')
    before('/radius remove [find comment="hr: HobeRadius RADIUS"]',
           '/radius add address=')
    before('/ip pool remove [find name="hr-hotspot-pool"]',
           '/ip pool add name="hr-hotspot-pool"')
    before('/ip pool remove [find name="hr-pppoe-pool"]',
           '/ip pool add name="hr-pppoe-pool"')
    before('/user remove [find name="hobe-api"]', '/user add name="hobe-api"')
    before('/ip firewall address-list remove [find list="hr-walled-garden"',
           '/ip firewall address-list add list="hr-walled-garden"')
    before('/ip firewall filter remove [find comment~"^hr-fw:"]',
           '/ip firewall filter add')
    before('/ip firewall nat remove [find comment~"^hr-nat:"]',
           '/ip firewall nat add')
    before('/system scheduler remove [find name=hr-sstp-watchdog]',
           '/system scheduler add name=hr-sstp-watchdog')
    before('/system script remove [find name=hr-sstp-watchdog-fn]',
           '/system script add name=hr-sstp-watchdog-fn')
    before('/tool netwatch remove [find comment="hr: RADIUS reachability"]',
           '/tool netwatch add')


def test_no_managed_add_lacks_a_remove_or_set_guard():
    """Scan EVERY `add` line: each must have a remove/disable guard for its tag,
    OR be a `set` (authoritative). Catches a future section that forgets cleanup."""
    s = build_onboarding_script(_params(block_page_url="http://203.0.113.9/p/expired"))
    lines = [l for l in s.splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    text = "\n".join(lines)
    for line in lines:
        if not re.search(r"\badd\b", line):
            continue
        # the distinguishing tag of this add: comment="..." / name="..." / name=bare / list="..."
        m = (re.search(r'comment="([^"]*?)(?::|")', line)   # tag up to ':' or closing quote
             or re.search(r'name="([^"]+)"', line)
             or re.search(r'name=([^\s"]+)', line)
             or re.search(r'list="([^"]+)"', line))
        assert m, f"managed add without an identifiable tag: {line}"
        tag = m.group(1)
        # a remove/disable referencing the same tag must exist (anywhere in script)
        assert re.search(r'(remove \[find|disable)[^\n]*' + re.escape(tag), text), \
            f"add tag {tag!r} has no remove/disable guard: {line}"
