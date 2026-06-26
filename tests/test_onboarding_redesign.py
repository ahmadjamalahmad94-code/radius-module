# -*- coding: utf-8 -*-
"""Premium redesign of the onboarding-script page + its section splitter.

Asserts the unified design-system shell is in place (megahero, code card,
secrets banner, section outline, master + per-section copy) while the generated
script + its secrets render verbatim (behaviour unchanged — the route tests in
test_onboarding_route.py still cover the firewall-ordering invariant).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


# ─── pure unit: split_sections is an exact line-slice of the script ──────────

def test_split_sections_is_lossless_and_labelled():
    from app.radius.services.router_onboarding_script import (
        build_onboarding_script, OnboardingParams, split_sections)
    p = OnboardingParams(
        router_name="CCR-Split", router_id=7, accel_host="187.77.70.18",
        sstp_port=443, tunnel_user="rtr-CCR-Split",
        tunnel_password="Xk9-Tunnel-Secret-7741", tunnel_ip="10.50.0.2",
        radius_ip="187.77.70.18", radius_secret="rad-secret-9921xZ",
        api_user="hobe-api", api_password="Api-Pw-7731xQ",
        walled_garden=["example.com"], block_page_url="http://203.0.113.5/p/x",
        hotspot_pool="10.5.50.0/24", pppoe_pool="10.5.60.0/24")
    script = build_onboarding_script(p)
    secs = split_sections(script)
    # banner + the nine numbered sections
    assert len(secs) == 10
    # every body is an exact substring of the canonical script (no drift)
    assert all(s["body"] in script for s in secs)
    # the sections rejoin to exactly the original (the join used by build_*)
    assert "\n\n".join(s["body"] for s in secs) + "\n" == script
    # start lines are strictly increasing and 1-based
    starts = [s["start_line"] for s in secs]
    assert starts[0] == 1 and starts == sorted(starts) and len(set(starts)) == len(starts)
    # titles are non-empty
    assert all(s["title"].strip() for s in secs)


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_obrd_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "187.77.70.18")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.50.0.0/24")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    from app.radius.db.repos import admins_repo
    u = f"a_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw", full_name="A",
                             is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "pw"}, follow_redirects=False)
    assert res.status_code in {302, 303}


def _csrf(client, url="/admin/radius/mt/setup"):
    client.get(url)
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _v6_router(client):
    token = _csrf(client)
    client.post("/admin/radius/mt/setup",
                data={"_csrf_token": token, "name": "CCR-OB",
                      "ros_version": "6", "v6_mode": "sstp_mgmt",
                      "server_ip": "187.77.70.18"}, follow_redirects=False)
    from app.radius.db.connection import db
    return int(db().execute(
        "SELECT id FROM nas_devices WHERE name='CCR-OB'").fetchone()["id"])


def test_page_uses_design_system(app, client):
    with app.app_context():
        _login(client)
        nas_id = _v6_router(client)
        html = client.get(
            f"/admin/radius/mt/{nas_id}/onboarding-script").get_data(as_text=True)
        # unified hero + KPI strip
        assert "uds-hero" in html and "uds-hero-kpis" in html
        # reusable premium code card (component) + line-numbered code
        assert "hcode-card" in html and "hcode-code" in html and "hcode-ln" in html
        # prominent secrets-once + firewall-order callouts (component)
        assert "hcode-callout--warn" in html and "hcode-callout--ok" in html
        # master copy button (design-system, no native alert) + steps
        assert 'data-cc-copy="ob"' in html and "hcode-steps" in html
        assert "alert(" not in html and "return confirm(" not in html


def test_section_outline_and_per_section_copy_present(app, client):
    with app.app_context():
        _login(client)
        nas_id = _v6_router(client)
        html = client.get(
            f"/admin/radius/mt/{nas_id}/onboarding-script").get_data(as_text=True)
        # outline chips with jump + per-section copy (component)
        assert "hcode-outline" in html and 'data-cc-jump="ob"' in html
        assert 'data-cc-seccopy="ob"' in html
        # verbatim full-script node for the master copy + per-section bodies
        assert 'id="ob-full"' in html
        assert 'id="ob-sec-0"' in html


def test_script_and_secrets_still_render_verbatim(app, client):
    with app.app_context():
        _login(client)
        nas_id = _v6_router(client)
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.connection import db
        pw = rmt.tunnel_radius_status("CCR-OB", tenant_id=1,
                                      reveal_secret=True).cleartext
        secret = db().execute(
            "SELECT secret FROM nas_devices WHERE id=?", (nas_id,)).fetchone()["secret"]
        html = client.get(
            f"/admin/radius/mt/{nas_id}/onboarding-script").get_data(as_text=True)
        # the generated script + its unique secrets are intact (no behaviour change)
        assert "/interface sstp-client add" in html
        assert "rtr-CCR-OB" in html
        assert pw and pw in html
        assert secret and secret in html
        # firewall ordering invariant still holds in the rendered output
        assert html.index("02 mgmt SSTP iface") < html.index("20 expired pool reject")
