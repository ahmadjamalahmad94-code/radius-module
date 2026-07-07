# -*- coding: utf-8 -*-
"""Dedicated SSTP/PPTP management-tunnel credentials UI (routes).

Covers the page + the three actions end-to-end through the test client:
  * GET  /mt/<id>/sstp           renders, shows the RADIUS sync status
  * POST /mt/<id>/sstp/sync      idempotently (re)provisions → "synced"
  * POST /mt/<id>/sstp/test      runs the diagnostic (ok / wrong_password)
  * POST /mt/<id>/sstp/reset     reveals a fresh password + MikroTik block

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sstp_ui_")
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
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _make_v6_sstp_router(client):
    """Create a v6 SSTP router through the real wizard POST; return its id."""
    token = _csrf(client)
    res = client.post("/admin/radius/mt/setup",
                      data={"_csrf_token": token, "name": "CCR4",
                            "ros_version": "6", "v6_mode": "sstp_mgmt",
                            "server_ip": "187.77.70.18"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}, res.get_data(as_text=True)[:400]
    from app.radius.db.connection import db
    row = db().execute("SELECT id FROM nas_devices WHERE name='CCR4'").fetchone()
    return int(row["id"])


def test_routes_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    for ep in ("radius.mt_sstp_credentials", "radius.mt_sstp_test",
               "radius.mt_sstp_sync", "radius.mt_sstp_reset"):
        assert ep in rules


def test_page_renders_synced_after_creation(app, client):
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        res = client.get(f"/admin/radius/mt/{nas_id}/sstp")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        # Provisioning during creation already wrote an MSCHAP-ready account.
        assert "rtr-CCR4" in html
        assert 'data-sstp-sync-badge="synced"' in html


def test_non_v6_row_404(app, client):
    with app.app_context():
        _login(client)
        # A v7 (WireGuard) router has no SSTP credential surface.
        token = _csrf(client)
        client.post("/admin/radius/mt/setup",
                    data={"_csrf_token": token, "name": "V7Box",
                          "ros_version": "7", "server_ip": "203.0.113.5"},
                    follow_redirects=False)
        from app.radius.db.connection import db
        row = db().execute("SELECT id FROM nas_devices WHERE name='V7Box'").fetchone()
        # WG path may be unconfigured in test → row may not exist; if it does,
        # the SSTP page must 404 (no mgmt_tunnel_type=sstp/pptp).
        if row:
            res = client.get(f"/admin/radius/mt/{int(row['id'])}/sstp")
            assert res.status_code == 404


def test_sync_action_makes_synced(app, client):
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        # Wipe the radcheck account to simulate the ccr4 incident (missing user).
        from app.radius.db.repos import freeradius_repo as fr
        fr.delete_user(1, "rtr-CCR4")
        res = client.get(f"/admin/radius/mt/{nas_id}/sstp")
        assert 'data-sstp-sync-badge="not-synced"' in res.get_data(as_text=True)
        # Sync re-provisions idempotently.
        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        res = client.post(f"/admin/radius/mt/{nas_id}/sstp/sync",
                          data={"_csrf_token": token}, follow_redirects=True)
        assert res.status_code == 200
        assert 'data-sstp-sync-badge="synced"' in res.get_data(as_text=True)


def test_test_action_reports_ok_and_wrong_password(app, client):
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        # Read the real provisioned password from radcheck.
        from app.radius.services import router_mgmt_tunnel as rmt
        st = rmt.tunnel_radius_status("CCR4", tenant_id=1, reveal_secret=True)
        good = st.cleartext

        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        res = client.post(f"/admin/radius/mt/{nas_id}/sstp/test",
                          data={"_csrf_token": token, "password": good},
                          follow_redirects=True)
        assert 'data-sstp-diag="ok"' in res.get_data(as_text=True)

        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        res = client.post(f"/admin/radius/mt/{nas_id}/sstp/test",
                          data={"_csrf_token": token, "password": "WRONG"},
                          follow_redirects=True)
        assert 'data-sstp-diag="wrong_password"' in res.get_data(as_text=True)


def test_reset_reveals_password_and_mikrotik_block(app, client):
    with app.app_context():
        _login(client)
        nas_id = _make_v6_sstp_router(client)
        token = _csrf(client, f"/admin/radius/mt/{nas_id}/sstp")
        res = client.post(f"/admin/radius/mt/{nas_id}/sstp/reset",
                          data={"_csrf_token": token, "password": "Chosen-Pw-77"},
                          follow_redirects=True)
        html = res.get_data(as_text=True)
        # The chosen password + a profile=default-encryption SSTP block are
        # revealed once (owner decision 2026).
        assert "Chosen-Pw-77" in html
        assert "/interface sstp-client add" in html
        assert "profile=default-encryption" in html
        # Re-rendering the page (GET) must NOT show it again (reveal-once).
        res2 = client.get(f"/admin/radius/mt/{nas_id}/sstp")
        assert "Chosen-Pw-77" not in res2.get_data(as_text=True)


# ════════════ SSTP/PPTP credential MANAGEMENT surface ════════════
def test_users_list_renders_with_accounts(app, client):
    with app.app_context():
        _login(client)
        _make_v6_sstp_router(client)
        res = client.get("/admin/radius/mt/sstp-users")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "rtr-CCR4" in html
        # Plaintext password present (revealable) + reveal toggle wired.
        from app.radius.services import router_mgmt_tunnel as rmt
        pw = rmt.tunnel_radius_status("CCR4", tenant_id=1, reveal_secret=True).cleartext
        assert pw in html
        assert "data-sstp-pw-toggle" in html


def test_users_toggle_disable_enable(app, client):
    with app.app_context():
        _login(client)
        _make_v6_sstp_router(client)
        from app.radius.services import router_mgmt_tunnel as rmt
        token = _csrf(client, "/admin/radius/mt/sstp-users")
        # Disable.
        client.post("/admin/radius/mt/sstp-users/toggle",
                    data={"_csrf_token": token, "username": "rtr-CCR4",
                          "enabled": "0"}, follow_redirects=True)
        assert rmt.tunnel_radius_status("CCR4", tenant_id=1).disabled
        # Enable.
        token = _csrf(client, "/admin/radius/mt/sstp-users")
        client.post("/admin/radius/mt/sstp-users/toggle",
                    data={"_csrf_token": token, "username": "rtr-CCR4",
                          "enabled": "1"}, follow_redirects=True)
        assert not rmt.tunnel_radius_status("CCR4", tenant_id=1).disabled


def test_users_reset_password(app, client):
    with app.app_context():
        _login(client)
        _make_v6_sstp_router(client)
        from app.radius.services import router_mgmt_tunnel as rmt
        token = _csrf(client, "/admin/radius/mt/sstp-users")
        client.post("/admin/radius/mt/sstp-users/reset",
                    data={"_csrf_token": token, "username": "rtr-CCR4",
                          "password": "NewClearPw-1"}, follow_redirects=True)
        st = rmt.tunnel_radius_status("CCR4", tenant_id=1, reveal_secret=True)
        assert st.cleartext == "NewClearPw-1" and st.has_nt and st.synced


def test_users_set_expiry(app, client):
    with app.app_context():
        _login(client)
        _make_v6_sstp_router(client)
        from app.radius.services import router_mgmt_tunnel as rmt
        token = _csrf(client, "/admin/radius/mt/sstp-users")
        client.post("/admin/radius/mt/sstp-users/expiry",
                    data={"_csrf_token": token, "username": "rtr-CCR4",
                          "expire_at": "2020-01-01T00:00"}, follow_redirects=True)
        assert rmt.tunnel_radius_status("CCR4", tenant_id=1).expired


def test_users_delete(app, client):
    with app.app_context():
        _login(client)
        _make_v6_sstp_router(client)
        from app.radius.services import router_mgmt_tunnel as rmt
        token = _csrf(client, "/admin/radius/mt/sstp-users")
        client.post("/admin/radius/mt/sstp-users/delete",
                    data={"_csrf_token": token, "username": "rtr-CCR4"},
                    follow_redirects=True)
        assert not rmt.tunnel_radius_status("CCR4", tenant_id=1).exists


def test_users_mutation_rejects_unknown_account(app, client):
    with app.app_context():
        _login(client)
        _make_v6_sstp_router(client)
        token = _csrf(client, "/admin/radius/mt/sstp-users")
        # A non-existent rtr- user → 404 (no arbitrary radcheck writes).
        res = client.post("/admin/radius/mt/sstp-users/toggle",
                          data={"_csrf_token": token, "username": "rtr-ghost",
                                "enabled": "0"})
        assert res.status_code == 404
        # A non-rtr username → 400.
        token = _csrf(client, "/admin/radius/mt/sstp-users")
        res = client.post("/admin/radius/mt/sstp-users/delete",
                          data={"_csrf_token": token, "username": "evil"})
        assert res.status_code == 400
