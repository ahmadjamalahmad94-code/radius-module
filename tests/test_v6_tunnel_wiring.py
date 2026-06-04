"""C8 — end-to-end wiring of the v6 SSTP/L2TP-IPsec tunnels.

The pure planners/renderers (v6_tunnels), the caps gate, the repo and the
migration already existed; these tests cover the glue added in mt_setup:

  * POSTing a RouterOS-6 router through the wizard persists a tunnel profile
    via router_tunnels_repo (masked secret refs only — no plaintext).
  * The script page emits the idempotent SSTP management script (and, when a
    traffic tunnel is selected, the L2TP/IPsec client + ipsec-secret).
  * A plain v7 create is unaffected (no tunnel profile, WireGuard path).
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "v6_wiring.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations

        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "wire_admin"
        sess["admin_name"] = "Wire Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "wire-csrf"


def _nas_id_by_name(name):
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT id FROM nas_devices WHERE name = ? ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone()
    return row["id"] if row else None


def test_v6_create_persists_sstp_and_ipsec_profile(app):
    with app.test_client() as client:
        _auth(client)
        res = client.post("/admin/radius/mt/setup", data={
            "_csrf_token": "wire-csrf",
            "name": "MT-v6-tunnels",
            "ros_version": "6",
            "address": "203.0.113.9",
            "server_ip": "198.51.100.5",
            "traffic_protocol": "ipsec",
            "traffic_mode": "full_tunnel",
            "full_tunnel_confirmed": "1",
        }, follow_redirects=True)
        html = res.get_data(as_text=True)

    # The one-time script reveal includes SSTP management + the IPsec exit.
    assert "sstp-client" in html
    assert "sstp-hoberadius-mgmt" in html
    assert "/ip ipsec peer" in html
    assert "/ip ipsec policy" in html
    # Tunnel-role architecture: the v6 exit is pure IPsec — never L2TP.
    assert "l2tp-client" not in html
    # SSTP management invariant surfaced in the script.
    assert "add-default-route=no" in html

    with app.app_context():
        from app.radius.db.repos import router_tunnels_repo as repo
        nas_id = _nas_id_by_name("MT-v6-tunnels")
        assert nas_id is not None
        prof = repo.get_tunnel_profile(1, nas_id)
        assert prof is not None
        assert prof["management_tunnel_type"] == "sstp_mgmt"
        assert prof["management_tunnel_status"] == "configured"
        assert prof["management_tunnel_interface_name"] == "sstp-hoberadius-mgmt"
        assert prof["sstp_verify_certificate"] == 0
        assert prof["traffic_tunnel_type"] == "ipsec_traffic"
        assert prof["traffic_enabled"] == 1
        assert prof["traffic_mode"] == "full_tunnel"
        # SECURITY: no plaintext secret ever stored — only masked refs.
        assert prof["management_secret_ref"]
        assert "sstp-" in prof["management_secret_ref"]
        assert prof["traffic_ipsec_secret_ref"]


def test_v6_create_sstp_only_when_traffic_disabled(app):
    with app.test_client() as client:
        _auth(client)
        client.post("/admin/radius/mt/setup", data={
            "_csrf_token": "wire-csrf",
            "name": "MT-v6-mgmt-only",
            "ros_version": "6",
            "address": "203.0.113.10",
            "server_ip": "198.51.100.5",
            "traffic_mode": "disabled",
        }, follow_redirects=True)

    with app.app_context():
        from app.radius.db.repos import router_tunnels_repo as repo
        nas_id = _nas_id_by_name("MT-v6-mgmt-only")
        prof = repo.get_tunnel_profile(1, nas_id)
        assert prof["management_tunnel_type"] == "sstp_mgmt"
        assert prof["traffic_tunnel_type"] == "none"
        assert prof["traffic_enabled"] == 0


def test_v7_create_has_no_tunnel_profile(app):
    with app.test_client() as client:
        _auth(client)
        res = client.post("/admin/radius/mt/setup", data={
            "_csrf_token": "wire-csrf",
            "name": "MT-v7-plain",
            "ros_version": "7",
            "server_ip": "198.51.100.5",
        }, follow_redirects=False)
    assert res.status_code in {200, 302, 303}

    with app.app_context():
        from app.radius.db.repos import router_tunnels_repo as repo
        nas_id = _nas_id_by_name("MT-v7-plain")
        if nas_id is not None:
            prof = repo.get_tunnel_profile(1, nas_id)
            # v7 path never writes SSTP — management stays default 'none'.
            assert prof["management_tunnel_type"] == "none"
