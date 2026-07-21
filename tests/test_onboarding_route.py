# -*- coding: utf-8 -*-
"""Route: GET /admin/radius/mt/<id>/onboarding-script — the one-paste generator.

Verifies the page renders end-to-end from a real v6 SSTP router, embeds the
router's UNIQUE secrets, and preserves the firewall ordering invariant.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_onboard_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "187.77.70.18")
    monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_POOL", "10.50.0.0/24")
    monkeypatch.setenv("HOBERADIUS_WALLED_GARDEN", "renew.hoberadius.com, 9.9.9.9")
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
    row = db().execute("SELECT id FROM nas_devices WHERE name='CafeNoor'").fetchone()
    return int(row["id"])


def test_route_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    assert "radius.mt_onboarding_script" in rules


def test_onboarding_page_renders_with_ordered_firewall(app, client):
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        res = client.get(f"/admin/radius/mt/{nas_id}/onboarding-script")
        assert res.status_code == 200, res.get_data(as_text=True)[:400]
        html = res.get_data(as_text=True)
        # script present + our naming
        assert "/interface sstp-client add" in html
        assert "hr-sstp-mgmt" in html and "hr-walled-garden" in html
        assert "rtr-CafeNoor" in html                  # unique tunnel user
        assert "868868" not in html                    # not a shared constant
        # walled-garden operator entry threaded through settings
        assert "renew.hoberadius.com" in html
        # firewall ordering: the mgmt-iface allow comes before the forward
        # allows. «20 expired pool reject» أُزيلت عمدًا من المولّد — كتلة hr-fw
        # صارت سماحات فقط (راجع router_onboarding_script.py) فلا قاعدة حجب
        # تُرفَع فوق قواعد الهوت سبوت الديناميكيّة.
        i_mgmt = html.index("02 mgmt SSTP iface")
        i_wg = html.index("11 walled-garden allow")
        assert i_mgmt < i_wg
        assert "expired pool reject" not in html


def test_onboarding_embeds_unique_secrets(app, client):
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        # the real per-router secrets
        from app.radius.services import router_mgmt_tunnel as rmt
        from app.radius.db.connection import db
        pw = rmt.tunnel_radius_status("CafeNoor", tenant_id=1,
                                      reveal_secret=True).cleartext
        secret = db().execute(
            "SELECT secret FROM nas_devices WHERE id=?", (nas_id,)).fetchone()["secret"]
        res = client.get(f"/admin/radius/mt/{nas_id}/onboarding-script")
        html = res.get_data(as_text=True)
        assert pw and pw in html               # unique tunnel password embedded
        assert secret and secret in html       # unique NAS RADIUS secret embedded


def test_non_v6_router_404(app, client):
    with app.app_context():
        _login(client)
        # bogus id → 404
        res = client.get("/admin/radius/mt/99999/onboarding-script")
        assert res.status_code == 404
