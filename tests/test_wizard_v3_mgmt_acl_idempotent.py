# -*- coding: utf-8 -*-
"""Setup Wizard v3 unified bootstrap script — management-ACL + idempotency.

Regression guard for the robust-provisioning pass: the v3 `/generate-script`
flow (`SetupWizardV3Service._render_unified_script`) must bind WinBox/API/web to
BOTH management gateways (WG subnet + SSTP/RADIUS gateway) — a WG-only `set api
address=` clobbered the SSTP path and took a router offline — and every `add`
must be re-paste-safe (RADIUS/WG/user remove-before-add or add-if-missing).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_wizv3_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    yield application
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _script(app):
    with app.app_context():
        from app.radius.services.setup_wizard_v3 import WizardV3Service
        svc = WizardV3Service()
        return svc._render_unified_script(
            run_id=42, router_vpn_ip="10.10.0.2",
            vps_public_endpoint="vpn.example.net", vps_wg_pubkey="P"*43 + "=",
            wg_listen_port=13231, vps_endpoint_port=13231, short_code="ABCD",
            radius_secret="RadSecret1234567890", api_user="hr-api-0042",
            api_password="ApiPass1234567890")


def test_v3_binds_mgmt_services_to_both_gateways(app):
    """api/winbox/www bind to the COMBINED allow-list (WG subnet + SSTP gateway)
    — not a WG-only /32 that would clobber the SSTP management path."""
    s = _script(app)
    for svc in ("api", "winbox", "www"):
        assert f"/ip service set {svc} address=10.10.0.0/24,10.50.0.1/32" in s
    # the old WG-only clobbering line is gone
    assert "/ip service set api address=10.10.0.1/32" not in s
    # tunnel-only, never the WAN
    assert "0.0.0.0/0" not in s.split("Step 4")[0] if "Step 4" in s else True
    assert "address=0.0.0.0/0" not in s


def test_v3_adds_are_idempotent(app):
    """Every state-creating command is re-paste-safe: WG interface/address/route
    removed-or-guarded, peer wiped via interface removal, RADIUS + user
    remove-before-add. Running twice converges to one state."""
    s = _script(app)
    # WG cleanup before (re)create
    assert '/interface wireguard remove [find where name="hr-wg"' in s
    assert '/ip address remove [find where interface="hr-wg"' in s
    assert '/ip route remove [find where gateway="hr-wg"' in s
    # RADIUS remove-before-add (comment-tagged, only ours)
    assert '/radius remove [find where comment~"HOBERADIUS_SETUP:42:radius"]' in s
    assert s.index("/radius remove [find") < s.index("/radius add ")
    # user remove-before-add
    assert '/user remove [find where name="hr-api-0042"]' in s
    assert s.index('/user remove [find where name="hr-api-0042"]') < s.index("/user add ")
    # exactly one RADIUS server add (no pile-up)
    assert s.count("/radius add ") == 1


def test_v3_run_twice_is_identical(app):
    """Same inputs → byte-identical script (deterministic; no time/random drift
    in the body that would defeat re-paste convergence)."""
    a = _script(app)
    b = _script(app)
    assert a == b


def test_v3_script_sets_ntp_before_wireguard(app):
    """NTP client is enabled BEFORE the WG interface is created, so a stale
    router clock (post power-outage) can't reject the WG handshake with a
    replayed/old timestamp — the exact failure we diagnosed live."""
    s = _script(app)
    assert "/system ntp client set enabled=yes" in s
    assert "216.239.35.0" in s
    assert "$rosMajor >= 7" in s                       # ROS7 branch
    assert "primary-ntp=216.239.35.0" in s            # ROS6 fallback
    # ordering: clock is fixed BEFORE the WireGuard handshake config
    assert s.index("/system ntp client set") < s.index('/interface wireguard add name="hr-wg"')
