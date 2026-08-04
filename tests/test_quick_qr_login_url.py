"""رابط دخول الهوت سبوت في المنشئ السريع — يُحفظ ويصنع QR دخول ميكروتك."""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "quick_qr.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()

    @flask_app.before_request
    def _bind():
        os.environ["HOBERADIUS_DB_PATH"] = db_file
        from app.radius.db.connection import reset_for_tests

        reset_for_tests(db_file)

    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return flask_app


def _admin_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "admin"
        sess["admin_name"] = "المدير العام"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["permissions"] = ["cards.print", "cards.view"]
        sess["_csrf_token"] = "t"


def test_quick_page_exposes_login_url_field(app):
    client = app.test_client()
    _admin_session(client)
    html = client.get("/admin/radius/cards/print/quick").get_data(as_text=True)
    assert 'name="hotspot_login_url"' in html
    assert "data-qk-qrurl" in html


def test_saving_from_quick_persists_login_url_and_qr_encodes_it(app):
    client = app.test_client()
    _admin_session(client)
    resp = client.post("/admin/radius/print-templates", data={
        "name": "قالب سريع",
        "hotspot_login_url": "10.5.50.1",       # بلا بروتوكول عمدًا
        "show_qr": "1",
        "return_to": "quick",
        "_csrf_token": "t",
    }, follow_redirects=False)
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:300]

    with app.app_context():
        row = db().execute(
            "SELECT layout_json FROM card_print_templates ORDER BY id DESC LIMIT 1"
        ).fetchone()
    import json
    layout = json.loads(row[0])
    # طُبِّع بإضافة البروتوكول
    assert layout["hotspot_login_url"] == "http://10.5.50.1"

    # محتوى الـQR = رابط دخول ميكروتك الكامل
    from app.radius.services.card_renderer import _qr_login_payload
    payload = _qr_login_payload(layout, "0123456789012", "123456", "1")
    assert payload.startswith("http://10.5.50.1/login?")
    assert "username=0123456789012" in payload and "password=123456" in payload
    assert "u=0123456789012" in payload and "p=123456" in payload


def test_without_url_qr_is_plain_username(app):
    from app.radius.services.card_renderer import _qr_login_payload

    assert _qr_login_payload({}, "0123456789012", "123456", "1") == "0123456789012"


def test_quick_suggests_previously_saved_url(app):
    """رابط ضُبط على قالب سابق يُقترح تلقائيًا في المنشئ (لا يُكتب مرّتين)."""
    client = app.test_client()
    _admin_session(client)
    client.post("/admin/radius/print-templates", data={
        "name": "قالب فيه رابط", "hotspot_login_url": "hotspot.example.net",
        "show_qr": "1", "return_to": "quick", "_csrf_token": "t",
    })
    client.post("/admin/radius/print-templates", data={
        "name": "قالب بلا رابط", "return_to": "quick", "_csrf_token": "t",
    })
    html = client.get("/admin/radius/cards/print/quick").get_data(as_text=True)
    assert "hotspot.example.net" in html
