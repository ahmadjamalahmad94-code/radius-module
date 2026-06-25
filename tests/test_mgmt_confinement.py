# -*- coding: utf-8 -*-
"""Management-tunnel confinement rule generator (deploy/mgmt-confinement).

The iptables/tc commands only run on the live VPS, but the rule LOGIC is the
high-stakes part (a wrong order could break RADIUS or leave passthrough open),
so we test it here. Run alone (per-file isolation)."""
from __future__ import annotations

import os
import sys

import pytest

_GEN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "deploy", "mgmt-confinement")
sys.path.insert(0, _GEN_DIR)
import confine_rules_gen as cg  # noqa: E402


def _flat(cmds):
    return [" ".join(c) for c in cmds]


# ── the chain order is the safety contract ──
def test_established_is_matched_first():
    rules = _flat(cg.confine_chain_rules("10.50.0.0/24", "10.10.0.0/24"))
    assert "ESTABLISHED,RELATED" in rules[0] and rules[0].endswith("RETURN")


def test_provider_to_router_is_allowed_before_any_drop():
    rules = _flat(cg.confine_chain_rules("10.50.0.0/24", "10.10.0.0/24"))
    # both tunnel subnets are RETURN'd by DESTINATION (provider→router) ...
    d_accel = next(i for i, r in enumerate(rules) if "-d 10.50.0.0/24" in r)
    d_wg = next(i for i, r in enumerate(rules) if "-d 10.10.0.0/24" in r)
    # ... before either is DROP'd by SOURCE (router→forward-out)
    s_accel = next(i for i, r in enumerate(rules) if "-s 10.50.0.0/24" in r)
    s_wg = next(i for i, r in enumerate(rules) if "-s 10.10.0.0/24" in r)
    assert rules[d_accel].endswith("RETURN") and rules[d_wg].endswith("RETURN")
    assert "DROP" in rules[s_accel] and "DROP" in rules[s_wg]
    assert max(d_accel, d_wg) < min(s_accel, s_wg)


def test_router_initiated_forward_is_dropped_for_both_subnets():
    rules = _flat(cg.confine_chain_rules("10.50.0.0/24", "10.10.0.0/24"))
    assert any("-s 10.50.0.0/24 -j DROP" in r for r in rules)
    assert any("-s 10.10.0.0/24 -j DROP" in r for r in rules)


def test_confinement_never_emits_an_input_rule():
    """RADIUS to the host is INPUT; this confinement must only ever touch the
    FORWARD path, so auth/acct/CoA can't break."""
    cmds = _flat(cg.confine_install_cmds("10.50.0.0/24", "10.10.0.0/24"))
    assert not any(" INPUT " in f" {c} " for c in cmds)
    assert not any("-A INPUT" in c or "-I INPUT" in c for c in cmds)


def test_hooked_into_forward_and_docker_user_idempotently():
    cmds = _flat(cg.confine_install_cmds("10.50.0.0/24", "10.10.0.0/24"))
    for parent in ("FORWARD", "DOCKER-USER"):
        # delete-then-insert == idempotent
        assert f"iptables -D {parent} -j {cg.CHAIN}" in cmds
        assert f"iptables -I {parent} 1 -j {cg.CHAIN}" in cmds


def test_chain_is_flushed_before_refill():
    cmds = _flat(cg.confine_install_cmds("10.50.0.0/24", "10.10.0.0/24"))
    assert f"iptables -F {cg.CHAIN}" in cmds
    assert cmds.index(f"iptables -F {cg.CHAIN}") < \
        cmds.index(f"iptables -A {cg.CHAIN} -m conntrack "
                   "--ctstate ESTABLISHED,RELATED -j RETURN")


# ── WireGuard tc cap ──
def test_wg_tc_caps_both_directions_at_rate():
    cmds = _flat(cg.wg_tc_cmds("wg0", 10))
    assert any("htb" in c and "rate 10mbit" in c for c in cmds)      # egress class
    assert any("ingress" in c for c in cmds)                          # ingress qdisc
    assert any("police rate 10mbit" in c and "drop" in c for c in cmds)


def test_wg_tc_disabled_when_rate_zero():
    assert cg.wg_tc_cmds("wg0", 0) == []


# ── injection guards ──
def test_bad_cidr_rejected():
    with pytest.raises(ValueError):
        cg.confine_chain_rules("10.50.0.0/24; rm -rf /", "10.10.0.0/24")


def test_bad_iface_rejected():
    with pytest.raises(ValueError):
        cg.wg_tc_cmds("wg0; reboot", 10)


def test_rate_clamped():
    assert cg._rate_mbps(-5) == 0
    assert cg._rate_mbps(99999) == 1000
    assert cg._rate_mbps("garbage") == cg.DEFAULT_RATE_MBPS
