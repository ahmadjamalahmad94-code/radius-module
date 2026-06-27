# -*- coding: utf-8 -*-
"""Shared management-ACL allow-list builder (mgmt_acl).

Both generated scripts (SSTP onboarding + WireGuard block) bind WinBox/API/web
with `/ip service set <svc> address=<list>`, which REPLACES. So each must emit
BOTH management gateways (SSTP/RADIUS gateway + WG subnet) or pasting one locks
out the other. This locks the builder's resolution + ordering + dedupe.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    # default env (no overrides) for deterministic defaults
    for k in ("HOBERADIUS_WG_SUBNET", "HOBERADIUS_MGMT_TUNNEL_POOL",
              "HOBERADIUS_MGMT_TUNNEL_SERVER_IP"):
        monkeypatch.delenv(k, raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    yield


def _acl():
    from app.radius.services import mgmt_acl
    return mgmt_acl


def test_defaults_resolve_both_gateways():
    m = _acl()
    assert m.wg_mgmt_subnet() == "10.10.0.0/24"
    assert m.sstp_mgmt_gateway() == "10.50.0.1"   # first host of 10.50.0.0/24


def test_combined_sstp_first_then_wg():
    """Default ordering (SSTP onboarding's perspective): gateway /32 then WG."""
    m = _acl()
    assert m.combined_acl(sstp_gateway_ip="10.50.0.1") == "10.50.0.1/32,10.10.0.0/24"


def test_combined_wg_first():
    """wg_first (WG block's perspective): WG subnet leads, SSTP gateway appended."""
    m = _acl()
    out = m.combined_acl(wg_subnet="10.10.0.0/24", sstp_gateway_ip="10.50.0.1",
                         wg_first=True)
    assert out == "10.10.0.0/24,10.50.0.1/32"


def test_both_gateways_always_present_either_order():
    m = _acl()
    for kw in ({}, {"wg_first": True}):
        out = m.combined_acl(**kw)
        assert "10.50.0.1/32" in out      # SSTP gateway
        assert "10.10.0.0/24" in out      # WG subnet
        assert "0.0.0.0/0" not in out     # never the WAN


def test_env_overrides_are_honoured(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.77.0.0/24")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_SERVER_IP", "10.88.0.1")
    m = _acl()
    assert m.wg_mgmt_subnet() == "10.77.0.0/24"
    assert m.sstp_mgmt_gateway() == "10.88.0.1"
    assert m.combined_acl(wg_first=True) == "10.77.0.0/24,10.88.0.1/32"


def test_dedupe_when_values_coincide():
    """If a caller passes a WG subnet that already equals the gateway form, the
    list never emits a duplicate token."""
    m = _acl()
    out = m.combined_acl(sstp_gateway_ip="10.50.0.1", wg_subnet="10.50.0.1/32")
    # both pieces canonicalise to the same /32 → single token
    assert out == "10.50.0.1/32"


def test_gateway_from_pool_env(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.99.0.0/24")
    m = _acl()
    assert m.sstp_mgmt_gateway() == "10.99.0.1"
