# -*- coding: utf-8 -*-
"""Open WinBox routes — open/close from the UI, source-IP capture, the active
session surfaced on the SSTP page + sessions list, and super-admin gating.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ra_rt_")
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


def _login(client, *, super_admin=True):
    from app.radius.db.repos import admins_repo
    u = f"a_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw", full_name="A",
                             is_super_admin=super_admin)
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
    return int(db().execute("SELECT id FROM nas_devices WHERE name='CCR4'").fetchone()["id"])


def test_open_close_winbox_flow(app, client):
    with app.app_context():
        _login(client, super_admin=True)
        nas_id = _v6_router(client)
        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        # open — the admin's public IP comes from X-Forwarded-For
        res = client.post(f"/admin/radius/mt/{nas_id}/remote/winbox/open",
                          data={"_csrf_token": token},
                          headers={"X-Forwarded-For": "198.51.100.42"},
                          follow_redirects=True)
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        # the active session is surfaced with the public host:port + the IP lock
        from app.radius.db.repos import router_remote_sessions_repo as sr
        s = sr.active_for_router(1, nas_id)
        assert s and s["source_ip"] == "198.51.100.42"
        assert f"203.0.113.1:{s['public_port']}" in html
        # nginx config really got the locked block
        from app.radius.services import router_remote_access as ra
        cfg = open(ra._stream_dir() / ra.STREAM_FILE, encoding="utf-8").read()
        assert "allow 198.51.100.42;" in cfg and "deny all;" in cfg

        # close
        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        res = client.post(f"/admin/radius/mt/{nas_id}/remote/{s['id']}/close",
                          data={"_csrf_token": token}, follow_redirects=True)
        assert res.status_code == 200
        assert sr.active_for_router(1, nas_id) is None


def test_sessions_list_renders(app, client):
    with app.app_context():
        _login(client, super_admin=True)
        nas_id = _v6_router(client)
        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        client.post(f"/admin/radius/mt/{nas_id}/remote/winbox/open",
                    data={"_csrf_token": token},
                    headers={"X-Forwarded-For": "198.51.100.9"},
                    follow_redirects=True)
        res = client.get("/admin/radius/mt/remote-sessions")
        assert res.status_code == 200
        assert "198.51.100.9" in res.get_data(as_text=True)


def test_non_super_admin_denied(app, client):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        # create a super admin first so our test admin isn't the auto-primary
        admins_repo.create_admin(username="owner", password="pw",
                                 full_name="Owner", is_super_admin=True)
        _login(client, super_admin=False)
        # a non-super admin cannot reach the remote-sessions surface
        res = client.get("/admin/radius/mt/remote-sessions", follow_redirects=False)
        assert res.status_code in {302, 303, 403}
        if res.status_code in {302, 303}:
            assert "/admin/radius/mt/remote-sessions" not in res.headers.get("Location", "")
