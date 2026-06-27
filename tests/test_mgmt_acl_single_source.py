# -*- coding: utf-8 -*-
"""Management-ACL block is a SINGLE SOURCE OF TRUTH across every generator.

The two pages that build a connection script — `/admin/radius/mt/setup`
(`mt_provisioner.render_wg_block` / `render_routeros_script`) and
`/admin/radius/setup-wizard-v3` (`WizardV3Service._render_unified_script`),
plus the onboarding script — must all emit the EXACT same both-gateways
`/ip service set` block, rendered from `mgmt_acl.service_lockdown_lines`, so the
ACL can never drift between pages (a WG-only value once took a router offline).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_1src_")
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


def test_service_lockdown_lines_is_combined_and_tunnel_only(app):
    with app.app_context():
        from app.radius.services import mgmt_acl
        lines = mgmt_acl.service_lockdown_lines(wg_first=True)
        assert lines == [
            "/ip service set winbox address=10.10.0.0/24,10.50.0.1/32",
            "/ip service set api address=10.10.0.0/24,10.50.0.1/32",
            "/ip service set www address=10.10.0.0/24,10.50.0.1/32",
        ]
        # both gateways present, never the WAN
        for ln in lines:
            assert "10.10.0.0/24" in ln and "10.50.0.1/32" in ln
            assert "0.0.0.0/0" not in ln


def test_all_generators_emit_the_same_acl_block(app):
    """The WG block (mt/setup), the wizard-v3 bootstrap, and the wizard
    provisioning template all contain the byte-identical canonical block."""
    with app.app_context():
        from app.radius.services import mgmt_acl
        from app.radius.services import mt_provisioner as prov
        from app.radius.services.setup_wizard_v3 import WizardV3Service

        canonical = "\n".join(mgmt_acl.service_lockdown_lines(wg_first=True))

        wg = prov.render_wg_block(
            nas_name="r", router_private_key="K", server_pubkey="S",
            server_endpoint="1.2.3.4:13231", allowed_subnet="10.10.0.0/24",
            router_tunnel_ip="10.10.0.5/24", ros_version="7")
        assert canonical in wg

        rs = prov.render_routeros_script(
            nas_name="r", api_user="hr-a", api_password="p" * 20,
            radius_secret="r" * 20, server_ip="10.10.0.1", ros_version="7",
            api_allowed_address="10.10.0.0/24")
        assert canonical in rs

        wiz = WizardV3Service()._render_unified_script(
            run_id=1, router_vpn_ip="10.10.0.2", vps_public_endpoint="x",
            vps_wg_pubkey="P", wg_listen_port=13231, vps_endpoint_port=13231,
            short_code="AB", radius_secret="r" * 16, api_user="hr-api-1",
            api_password="p" * 16)
        assert canonical in wiz


def test_onboarding_emits_same_block_sstp_first(app):
    """The SSTP onboarding script leads with the SSTP gateway (its own path) but
    still renders from the same single source — both gateways present."""
    with app.app_context():
        from app.radius.services import mgmt_acl
        from app.radius.services import router_onboarding_script as ob
        canonical = "\n".join(
            mgmt_acl.service_lockdown_lines(sstp_gateway_ip="10.50.0.1"))
        p = ob.OnboardingParams(
            router_name="x", router_id=1, accel_host="1.2.3.4", sstp_port=443,
            tunnel_user="rtr-x", tunnel_password="Uniq-Pw-abc123XYZ",
            tunnel_ip="10.50.0.2", radius_ip="10.50.0.1",
            radius_secret="per-nas-secret-9931", api_user="hobe-api",
            api_password="api-uniq-77team", walled_garden=[],
            block_page_url="", hotspot_pool="10.5.50.0/24",
            pppoe_pool="10.5.60.0/24")
        assert canonical in ob._section_service_lockdown(p)
        assert "10.50.0.1/32" in canonical and "10.10.0.0/24" in canonical
