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
