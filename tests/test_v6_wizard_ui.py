"""C6 — RouterOS v6 wizard UI.

أُزيل من لوحة العميل — يُعاد مركزياً عبر لوحة التراخيص (قرار معماري):
كانت بطاقة نفق الإدارة SSTP + قسم نفق تغيير العنوان المدفوع تظهران لراوتر
v6 في معالج الإضافة. حُذفت؛ يبقى هنا حارس انحدار يؤكد غيابها + اختبار
إنشاء v7 العادي (مسار WireGuard لم يُمَسّ).
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


def test_add_form_no_longer_renders_v6_tunnel_strategy(app):
    """حارس انحدار: لا أثر لقسم أنفاق v6 (SSTP/الترافيك) في معالج الإضافة."""
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/mt/setup").get_data(as_text=True)
    assert "نفق الإدارة الموصى به" not in html
    assert "نفق تغيير العنوان وتمرير الحركة" not in html
    assert 'name="sstp_verify_certificate"' not in html
    assert 'name="traffic_mode"' not in html
    assert 'name="full_tunnel_confirmed"' not in html
    assert "data-mt-v6-tunnels" not in html
    # المعالج الأساسي يبقى يعمل (حقول الهوية + اختيار الإصدار).
    assert 'name="ros_version"' in html


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
