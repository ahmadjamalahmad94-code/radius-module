"""UI for the PPTP Legacy traffic option: wizard protocol selector + ops badge."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "pptp_ui.db")
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
        sess["admin_user"] = "pptp_admin"
        sess["admin_name"] = "PPTP Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "pptp-csrf"


def test_wizard_offers_protocol_choice_with_pptp_warning(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/mt/setup").get_data(as_text=True)
    assert 'name="traffic_protocol"' in html
    assert "L2TP/IPsec — موصى به" in html
    assert "PPTP — قديم وغير آمن" in html
    # the PPTP insecurity warning block exists (toggled by JS)
    assert "data-mt-pptp-warn" in html
    assert "تشفيره (MS-CHAPv2) مخترَق" in html
    # L2TP/IPsec is the default selected option (PPTP is never default)
    assert 'value="l2tp_ipsec" selected' in html


def test_ops_badge_marks_pptp_as_insecure(app):
    with app.app_context():
        from app.radius.core.types import NasDevice
        from app.radius.db.repos import nas_repo, router_tunnels_repo as repo
        nas_id = nas_repo.upsert_nas(NasDevice(
            id=None, name="R6-pptp", address="10.0.0.6", secret="x",
            vendor="mikrotik", tenant_id=1)).id
        repo.update_tunnel_profile(
            1, nas_id,
            management_tunnel_type="sstp_mgmt", management_tunnel_status="connected",
            traffic_tunnel_type="pptp_traffic", traffic_mode="policy_routing",
            traffic_enabled=1)
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/mt/operations").get_data(as_text=True)
    assert "PPTP" in html
    assert "غير آمن" in html
