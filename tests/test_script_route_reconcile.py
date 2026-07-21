# -*- coding: utf-8 -*-
"""Reconciliation: ONE authoritative script per router.

The panel had two pages that produced DIFFERENT scripts for the same v6 router:
  • /admin/radius/mt/<id>/script           (legacy render_routeros_script)
  • /admin/radius/mt/<id>/onboarding-script (authoritative build_onboarding_script)

Decision: for a v6 SSTP/PPTP row, /script REDIRECTS to /onboarding-script (the
authoritative generator), and the v6 SSTP page links only to the onboarding
script. /script remains for v7 WireGuard + legacy/direct rows (the onboarding
generator doesn't cover those).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_recon_")
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
    username = f"wiz_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=username, password="wiz-pass",
                             full_name="Wizard", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": "wiz-pass"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _csrf(client, url="/admin/radius/mt/setup"):
    client.get(url)
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _make_v6_sstp_router(client):
    token = _csrf(client)
    client.post("/admin/radius/mt/setup",
                data={"_csrf_token": token, "name": "CafeNoor",
                      "ros_version": "6", "v6_mode": "sstp_mgmt",
                      "server_ip": "187.77.70.18"}, follow_redirects=False)
    from app.radius.db.connection import db
    return int(db().execute(
        "SELECT id FROM nas_devices WHERE name='CafeNoor'").fetchone()["id"])


def test_v6_script_route_redirects_to_onboarding(app, client):
    """The legacy /script for a v6 SSTP row must redirect to the authoritative
    /onboarding-script — never serve a second, divergent script."""
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        res = client.get(f"/admin/radius/mt/{nas_id}/script",
                         follow_redirects=False)
        assert res.status_code in {301, 302, 303}
        assert f"/mt/{nas_id}/onboarding-script" in res.headers["Location"]


def test_v6_script_redirect_lands_on_authoritative_script(app, client):
    """Following the redirect yields the authoritative 9-section script (firewall
    ordering + the authoritative RADIUS-disable cleanup), not the legacy one."""
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        res = client.get(f"/admin/radius/mt/{nas_id}/script",
                         follow_redirects=True)
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        # authoritative markers absent from the legacy generator:
        assert ":foreach r in=[/radius find] do={ /radius disable $r }" in html
        assert "02 mgmt SSTP iface" in html            # firewall ordering section
        assert "11 walled-garden allow" in html        # forward allow block
        # «20 expired pool reject» أُزيلت عمدًا من المولّد الرسميّ (كتلة hr-fw
        # سماحات فقط — راجع router_onboarding_script.py)، فغيابها اليوم دليل
        # على أنّنا وصلنا المولّد الرسميّ لا القديم.
        assert "expired pool reject" not in html


def test_v6_sstp_page_links_only_to_onboarding_script(app, client):
    """The v6 SSTP credentials page must offer ONE script button (the
    authoritative onboarding one) — the old «سكربت الإعداد» link is gone."""
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        res = client.get(f"/admin/radius/mt/{nas_id}/sstp")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert f"/mt/{nas_id}/onboarding-script" in html          # authoritative link present
        assert f'href="/admin/radius/mt/{nas_id}/script"' not in html  # legacy button removed


def test_v7_wireguard_script_route_not_redirected(app, client):
    """A v7 WireGuard / non-v6 row still uses /script (the only generator that
    handles WG + the one-time key) — it must NOT redirect to onboarding-script
    (which is SSTP/PPTP-only)."""
    with app.app_context():
        _login(client)
        from app.radius.db.connection import db, transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices (tenant_id,name,address,secret,vendor,"
                " nas_type,enabled,ros_version,connection_mode,"
                " management_tunnel_type,api_user,api_password,created_at) "
                "VALUES (1,'WgRouter','10.10.0.7','s','mikrotik','hotspot',1,'7',"
                "'vpn','',  'hobe-api','apipw','2026-01-01T00:00:00Z')")
        rid = int(db().execute(
            "SELECT id FROM nas_devices WHERE name='WgRouter'").fetchone()["id"])
        res = client.get(f"/admin/radius/mt/{rid}/script", follow_redirects=False)
        # not a redirect to the SSTP-only onboarding generator
        loc = res.headers.get("Location", "")
        assert "onboarding-script" not in loc
