# -*- coding: utf-8 -*-
"""«انتهى اشتراكك» enforcement — RADIUS funnel + onboarding redirect.

The expiry path now funnels expired users of ALL three types (cards / PPPoE /
hotspot) into the captive pool (Mikrotik-Address-List = hr-pool-expired) instead
of rejecting them, gated by HOBERADIUS_EXPIRED_CAPTIVE_ENABLED. Builds on the
existing _check_status/_check_expiration logic (one unified gate).

Run this file alone (per-file isolation)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _sub(**over):
    from app.radius.core.types import Subscriber
    base = dict(id=1, username="u", password="p", status="enabled",
                service_type="Hotspot")
    base.update(over)
    return Subscriber(**base)


# ════════════ RADIUS funnel — all 3 types → captive pool ════════════
def test_expired_status_all_types_funnel_to_pool(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_EXPIRED_CAPTIVE_ENABLED", "1")
    from app.radius.services import policy_engine as pe
    for stype, utype in (("Hotspot", "subscriber"), ("PPPoE", "subscriber"),
                         ("Hotspot", "card")):
        d = pe._check_expiry_captive(_sub(status="expired", service_type=stype,
                                          user_type=utype))
        assert d is not None and d.ok and d.reason == "expired_captive"
        assert d.reply_attrs["Mikrotik-Address-List"] == "hr-pool-expired"
        assert d.reply_attrs.get("Session-Timeout")  # short re-auth window


def test_expire_at_in_past_funnels(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_EXPIRED_CAPTIVE_ENABLED", "1")
    from app.radius.services import policy_engine as pe
    d = pe._check_expiry_captive(
        _sub(status="enabled", service_type="PPPoE",
             expire_at=datetime.utcnow() - timedelta(days=1)))
    assert d.ok and d.reply_attrs["Mikrotik-Address-List"] == "hr-pool-expired"


def test_active_user_not_funneled(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_EXPIRED_CAPTIVE_ENABLED", "1")
    from app.radius.services import policy_engine as pe
    assert pe._check_expiry_captive(_sub(status="enabled")) is None
    # future expiry is fine
    assert pe._check_expiry_captive(
        _sub(expire_at=datetime.utcnow() + timedelta(days=5))) is None


def test_disabled_status_always_rejects_no_captive(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_EXPIRED_CAPTIVE_ENABLED", "1")
    from app.radius.services import policy_engine as pe
    d = pe._check_expiry_captive(_sub(status="disabled"))
    assert d is not None and not d.ok and d.reason == "disabled"


def test_captive_disabled_falls_back_to_reject(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_EXPIRED_CAPTIVE_ENABLED", "0")
    from app.radius.services import policy_engine as pe
    d = pe._check_expiry_captive(_sub(status="expired"))
    assert d is not None and not d.ok and d.reason == "expired"


def test_pool_name_is_configurable(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_EXPIRED_CAPTIVE_ENABLED", "1")
    monkeypatch.setenv("HOBERADIUS_EXPIRED_POOL_NAME", "custom-expired")
    from app.radius.services import policy_engine as pe
    d = pe._check_expiry_captive(_sub(status="expired"))
    assert d.reply_attrs["Mikrotik-Address-List"] == "custom-expired"


# ════════════ onboarding redirect — NAT enabled + walled-garden ════════════
def _onb(**over):
    from app.radius.services.router_onboarding_script import (
        OnboardingParams, build_onboarding_script)
    base = dict(
        router_name="cafe", router_id=3, accel_host="187.77.70.18", sstp_port=443,
        tunnel_user="rtr-cafe", tunnel_password="Uniq-Pw-abc123XYZ",
        tunnel_ip="10.50.0.5", radius_ip="10.50.0.1",
        radius_secret="per-nas-secret-9931", api_user="hobe-api",
        api_password="apipw", walled_garden=[],
        block_page_url="http://203.0.113.9/p/expired")
    base.update(over)
    return build_onboarding_script(OnboardingParams(**base))


def test_nat_redirect_enabled_for_ip_host():
    s = _onb()
    nat = [l for l in s.splitlines() if "/ip firewall nat add" in l][0]
    assert "action=dst-nat" in nat and "to-addresses=203.0.113.9" in nat
    assert "to-ports=80" in nat and 'dst-port=80' in nat
    assert "disabled=yes" not in nat   # phase 2: ENABLED


def test_block_host_added_to_walled_garden():
    s = _onb()
    assert 'list="hr-walled-garden" address=203.0.113.9' in s


def test_nat_skipped_for_domain_host_but_walled():
    s = _onb(block_page_url="http://renew.example.com/p/expired")
    assert "/ip firewall nat add" not in s            # can't dst-nat to a domain
    assert 'list="hr-walled-garden" address=renew.example.com' in s


def test_redirect_does_not_break_firewall_ordering():
    from app.radius.services.router_onboarding_script import firewall_rule_order
    order = firewall_rule_order(_onb())
    wg = next(i for i, c in enumerate(order) if "walled-garden allow" in c)
    exp = next(i for i, c in enumerate(order) if "expired pool reject" in c)
    assert wg < exp   # walled-garden (page reachable) still BEFORE the reject
