"""C7 — tunnel status in the router operations page.

mt_operations shows each router's management + (optional) traffic tunnel
type/status from the migration-092 profile. No secrets are exposed.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "v6_ops.db")
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
        sess["admin_user"] = "ops_admin"
        sess["admin_name"] = "Ops Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "ops-csrf"


def _router(name="R", addr="10.0.0.9"):
    from app.radius.core.types import NasDevice
    from app.radius.db.repos import nas_repo
    return nas_repo.upsert_nas(NasDevice(
        id=None, name=name, address=addr, secret="x", vendor="mikrotik", tenant_id=1,
    )).id


def test_ops_page_shows_configured_tunnels(app):
    with app.app_context():
        nas_id = _router("R6-tunneled")
        from app.radius.db.repos import router_tunnels_repo as repo
        repo.update_tunnel_profile(
            1, nas_id,
            management_tunnel_type="sstp_mgmt",
            management_tunnel_status="connected",
            traffic_tunnel_type="l2tp_ipsec_traffic",
            traffic_mode="policy_routing",
            traffic_enabled=1,
        )
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert "data-mt-tunnels" in html
    assert "إدارة:" in html
    assert "SSTP" in html
    assert "ترافيك:" in html
    assert "L2TP/IPsec" in html
    assert "policy_routing" in html


def test_ops_page_router_without_tunnel_shows_placeholder(app):
    with app.app_context():
        _router("R-plain")
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert "لا نفق إدارة مُعدّ" in html
