# -*- coding: utf-8 -*-
"""Premium redesign of the three management-tunnel pages — asserts the unified
design-system shell is in place on each, while every prior data hook still
renders (the behavioural coverage lives in test_sstp_credentials_routes.py and
test_remote_access_routes.py).

  * /mt/sstp-users      → megahero + KPIs + carded UDS table + action modals
  * /mt/<id>/sstp       → megahero + sync banner + kv + reset modal
  * /mt/remote-sessions → megahero + active table + audit table

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_redesign_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_NGINX_STREAM_DIR", tempfile.mkdtemp())
    monkeypatch.setenv("HOBERADIUS_PUBLIC_IP", "203.0.113.1")
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
                      data={"username": u, "password": "pw"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _csrf(client, url="/admin/radius/mt/setup"):
    client.get(url)
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _v6_router(client):
    token = _csrf(client)
    client.post("/admin/radius/mt/setup",
                data={"_csrf_token": token, "name": "CCR4",
                      "ros_version": "6", "v6_mode": "sstp_mgmt",
                      "server_ip": "187.77.70.18"}, follow_redirects=False)
    from app.radius.db.connection import db
    return int(db().execute(
        "SELECT id FROM nas_devices WHERE name='CCR4'").fetchone()["id"])


def test_sstp_users_uses_design_system(app, client):
    with app.app_context():
        _login(client)
        _v6_router(client)
        html = client.get("/admin/radius/mt/sstp-users").get_data(as_text=True)
        # Unified hero + KPI strip
        assert "uds-hero" in html and "uds-hero-kpis" in html
        # Carded, sortable, paginated UDS table
        assert 'data-uds-table' in html and 'class="uds-table"' in html
        # Action modals replace cramped inline forms / native confirm
        assert 'id="sstp-reset-modal"' in html
        assert 'id="sstp-expiry-modal"' in html
        assert 'data-uds-modal-open="sstp-reset-modal"' in html
        # No native confirm() left behind
        assert "onsubmit=\"return confirm(" not in html
        # Data hooks preserved
        assert "rtr-CCR4" in html and "data-sstp-pw-toggle" in html


def test_sstp_credentials_uses_design_system(app, client):
    with app.app_context():
        _login(client)
        nas_id = _v6_router(client)
        html = client.get(
            f"/admin/radius/mt/{nas_id}/sstp").get_data(as_text=True)
        assert "uds-hero" in html and "uds-hero-kpis" in html
        # Prominent sync banner + section cards
        assert "sstp-banner" in html and "hub-section" in html
        # Reset-password lives in a floating modal now
        assert 'id="sstp-reset-modal"' in html
        # Behavioural hook preserved
        assert 'data-sstp-sync-badge="synced"' in html


def test_remote_sessions_uses_design_system(app, client):
    with app.app_context():
        _login(client)
        nas_id = _v6_router(client)
        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        client.post(f"/admin/radius/mt/{nas_id}/remote/winbox/open",
                    data={"_csrf_token": token},
                    headers={"X-Forwarded-For": "198.51.100.9"},
                    follow_redirects=True)
        html = client.get(
            "/admin/radius/mt/remote-sessions").get_data(as_text=True)
        assert "uds-hero" in html and "uds-hero-kpis" in html
        assert 'data-uds-table' in html and "hub-section" in html
        # The active session row still surfaces the source-IP lock
        assert "198.51.100.9" in html


def test_remote_sessions_empty_state(app, client):
    with app.app_context():
        _login(client)
        html = client.get(
            "/admin/radius/mt/remote-sessions").get_data(as_text=True)
        # Honest empty state, never a silently blank table
        assert "hub-empty" in html
