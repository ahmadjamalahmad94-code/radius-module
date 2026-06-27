# -*- coding: utf-8 -*-
"""SSTP / PPTP / WireGuard connection scripts are FULLY IDEMPOTENT at the source.

Every renderer that emits a tunnel client (`data_connection.render_{sstp,pptp,
wireguard}_client` for the data connection, and `mt_provisioner.render_{sstp,
pptp}_mgmt_block` for the /mt/setup management tunnel) must start with a
remove-before-add of OUR OWN interface, so re-pasting converges to exactly ONE
clean client — no duplicates, no conflicts — matching the WireGuard block.

The cleanup is scoped to our interface NAMES, which are distinct between the data
client (`hobe-data-*`) and the management tunnel (`hr-*-mgmt`), so neither script
ever removes the other's client (coherence).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import pytest


def _sstp(**over):
    from app.radius.services import data_connection as dc
    kw = dict(host="1.2.3.4", username="u", password="p", comment="C", version=7)
    kw.update(over)
    return dc.render_sstp_client(**kw)


def _pptp(**over):
    from app.radius.services import data_connection as dc
    kw = dict(host="1.2.3.4", username="u", password="p", comment="C")
    kw.update(over)
    return dc.render_pptp_client(**kw)


def _wg(**over):
    from app.radius.services import data_connection as dc
    kw = dict(host="1.2.3.4", wg_port=51820, client_private_key="K",
              server_public_key="S", assigned_ip="10.60.0.5", comment="C")
    kw.update(over)
    return dc.render_wireguard_client(**kw)


def test_data_sstp_is_cleanup_before_add():
    out = _sstp()
    assert out.splitlines()[0] == (
        '/interface sstp-client remove [find name="hobe-data-sstp"]')
    assert '/interface sstp-client add name="hobe-data-sstp"' in out
    assert out.index("remove [find") < out.index("add name=")
    assert _sstp() == out                       # run-twice identical


def test_data_pptp_is_cleanup_before_add():
    out = _pptp()
    assert out.splitlines()[0] == (
        '/interface pptp-client remove [find name="hobe-data-pptp"]')
    assert '/interface pptp-client add name="hobe-data-pptp"' in out
    assert out.index("remove [find") < out.index("add name=")
    assert _pptp() == out


def test_data_wireguard_wipes_before_add():
    out = _wg()
    lines = out.splitlines()
    assert lines[:3] == [
        '/interface wireguard peers remove [find interface="hobe-data-wg"]',
        '/ip address remove [find interface="hobe-data-wg"]',
        '/interface wireguard remove [find name="hobe-data-wg"]',
    ]
    assert out.count("/interface wireguard add name=") == 1
    assert out.count("/interface wireguard peers add") == 1
    assert _wg() == out                          # run-twice identical


def test_opt_out_yields_bare_add_for_caller_owned_cleanup():
    """idempotent=False = the legacy single add (used by callers that own their
    own cleanup, e.g. ip_change_script)."""
    assert _sstp(idempotent=False) == _sstp(idempotent=False)
    assert "remove [find" not in _sstp(idempotent=False)
    assert "remove [find" not in _pptp(idempotent=False)
    assert "remove [find" not in _wg(idempotent=False)


def test_mgmt_blocks_cleanup_before_add():
    from app.radius.services import mt_provisioner as prov
    sstp = prov.render_sstp_mgmt_block(
        nas_name="ccr", accel_host="1.2.3.4", username="rtr-x", password="pw")
    pptp = prov.render_pptp_mgmt_block(
        nas_name="ccr", accel_host="1.2.3.4", username="rtr-x", password="pw")
    assert "/interface sstp-client remove [find name=hr-sstp-mgmt]" in sstp
    assert sstp.index("sstp-client remove") < sstp.index("sstp-client add")
    assert "/interface pptp-client remove [find name=hr-pptp-mgmt]" in pptp
    assert pptp.index("pptp-client remove") < pptp.index("pptp-client add")
    assert prov.render_sstp_mgmt_block(
        nas_name="ccr", accel_host="1.2.3.4", username="rtr-x",
        password="pw") == sstp                   # run-twice identical


def test_data_and_mgmt_names_never_conflict():
    """The data client (hobe-data-*) and the mgmt tunnel (hr-*-mgmt) use distinct
    interface names, so neither script's cleanup removes the other's client."""
    from app.radius.services import mt_provisioner as prov
    data = _sstp() + "\n" + _pptp() + "\n" + _wg()
    mgmt = prov.render_sstp_mgmt_block(
        nas_name="ccr", accel_host="1.2.3.4", username="rtr-x", password="pw")
    assert "hr-sstp-mgmt" not in data and "hr-pptp-mgmt" not in data
    assert "hobe-data-sstp" not in mgmt and "hobe-data-pptp" not in mgmt
