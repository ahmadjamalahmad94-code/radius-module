"""C6 — RouterOS v6 tunnel strategy UI in the add-router wizard.

The add form surfaces, for v6 routers, the SSTP management card (recommended,
management-only, no default route) and the advanced L2TP/IPsec traffic
section (off by default, full-tunnel needs explicit confirmation). WireGuard
is the v7 path. UI-only — the create handler is unchanged.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "v6_wizard.db")
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
        sess["admin_user"] = "v6_admin"
        sess["admin_name"] = "V6 Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "v6-csrf"


def test_add_form_renders_v6_tunnel_strategy(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/mt/setup").get_data(as_text=True)
    # SSTP management card (recommended, management-only, no default route)
    assert "نفق الإدارة الموصى به" in html
    assert "موصى به للإدارة" in html
    assert "لن يتم ضبط المسار الافتراضي على نفق الإدارة" in html
    assert 'name="sstp_verify_certificate"' in html
    # L2TP/IPsec advanced traffic section
    assert "نفق تغيير العنوان وتمرير الحركة" in html
    assert "متقدم" in html
    assert 'name="traffic_mode"' in html
    assert 'name="full_tunnel_confirmed"' in html
    assert "أفهم أن تمرير كل الحركة قد يسبب انقطاعًا" in html
    # routing conflict warning
    assert "لا يمكن جعل نفق الإدارة ونفق الحركة يملكان مسارًا افتراضيًا في الوقت نفسه" in html
    # v6 block is hidden by default (v7 is the default selection)
    assert "data-mt-v6-tunnels" in html


def test_create_handler_still_accepts_v7_without_tunnel_fields(app):
    # The new fields are optional; a plain v7 create must still work
    # (handler unchanged, ignores unknown fields).
    with app.test_client() as client:
        _auth(client)
        res = client.post("/admin/radius/mt/setup", data={
            "_csrf_token": "v6-csrf",
            "name": "MT-v7", "ros_version": "7", "server_ip": "10.0.0.1",
        }, follow_redirects=False)
    assert res.status_code in {200, 302, 303}
