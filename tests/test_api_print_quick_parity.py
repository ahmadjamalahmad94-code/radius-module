"""The app's «طباعة الكروت» saves through the SAME builder as the web
«منشئ كروت PDF» — a template saved from the app is identical to one saved from
the web with the same fields (defaults included), and its preview is drawn by
the export's own code (2026-09-28)."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

TOKEN = "quick-parity-token"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "parity.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "parity-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


AUTH = {"Authorization": "Bearer " + TOKEN}

QUICK_FIELDS = {
    "font_size_unit": "pt", "image_fit": "stretch",
    "background_style": "preset", "render_engine": "ar_vertical",
    "card_width_mm": "54", "card_height_mm": "85.6",
    "design_preset": "modern", "hotspot_address": "hotspot.local",
    "show_username": "1", "show_password": "1",
    "username_font_size": "14", "password_font_size": "12.5",
    "show_qr": "1", "show_price": "0",
    "hotspot_login_url": "10.5.50.1",
    "credential_background_enabled": "1",
    "username_surface_enabled": "1", "password_surface_enabled": "1",
    "surface_color": "#fde68a", "username_surface_color": "#fde68a",
    "password_surface_color": "#fde68a",
    "username_x": "6.5", "username_y": "40", "password_x": "0",
    "password_y": "0", "qr_x": "0", "qr_y": "0", "qr_size_pct": "30",
}


def _web_login(client) -> str:
    from app.radius.db.repos import admins_repo

    u = f"par_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="par-pass",
                             full_name="Parity", is_super_admin=True, role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
    res = client.post("/admin/radius/login", data={"username": u, "password": "par-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/cards/print")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _stored(tid: int) -> dict:
    from app.radius.db.repos import operations_repo
    return operations_repo.get_print_template(1, tid)


def _comparable(row: dict) -> dict:
    drop = {"id", "name", "created_at", "updated_at", "created_by", "updated_by"}
    return {k: v for k, v in row.items() if k not in drop}


def test_app_quick_save_equals_web_quick_save(client):
    token = _web_login(client)
    web = client.post("/admin/radius/print-templates", data={
        "_csrf_token": token, "return_to": "quick", "quick_export": "0",
        "name": "web-" + uuid4().hex[:6], **QUICK_FIELDS,
    })
    assert web.status_code in {302, 303}
    web_id = int(web.headers["Location"].split("template_id=")[1].split("&")[0])

    app = client.post("/api/v1/print-templates/quick-save", headers=AUTH, json={
        "form": {"name": "app-" + uuid4().hex[:6], **QUICK_FIELDS},
        "print_settings": {"print_columns": 6, "print_rows": 9},
    })
    assert app.status_code == 201, app.get_json()
    app_id = int(app.get_json()["data"]["template"]["id"])

    assert _comparable(_stored(app_id)) == _comparable(_stored(web_id))
    # Web normalization happened (IP → http://…, defaults filled).
    layout = _stored(app_id)["layout_json"]
    assert layout["hotspot_login_url"] == "http://10.5.50.1"
    assert layout["brand_name"] == "HobeRadius"
    # …and the sheet settings were remembered like the web does.
    got = client.get("/api/v1/print-templates/last-settings", headers=AUTH).get_json()
    assert got["data"]["settings"]["print_columns"] == "6"


def test_quick_save_updates_in_place(client):
    created = client.post("/api/v1/print-templates/quick-save", headers=AUTH,
                          json={"form": {"name": "u-" + uuid4().hex[:6], **QUICK_FIELDS}})
    tid = int(created.get_json()["data"]["template"]["id"])
    res = client.post("/api/v1/print-templates/quick-save", headers=AUTH, json={
        "template_id": tid,
        "form": {"name": "renamed", **QUICK_FIELDS, "username_font_size": "20"},
    })
    assert res.status_code == 200, res.get_json()
    assert _stored(tid)["name"] == "renamed"
    assert _stored(tid)["layout_json"]["username_font_size"] == 20


def test_preview_from_quick_form_page_and_card(client):
    for mode in ("page", "card"):
        res = client.post("/api/v1/print-templates/preview.pdf", headers=AUTH, json={
            "form": {"name": "p", **QUICK_FIELDS}, "mode": mode,
            "print_settings": {"print_columns": 3, "print_rows": 3,
                               "print_cut_lines": True},
        })
        assert res.status_code == 200, res.get_data(as_text=True)[:200]
        assert res.data.startswith(b"%PDF")


def test_quick_save_requires_a_name(client):
    res = client.post("/api/v1/print-templates/quick-save", headers=AUTH,
                      json={"form": {**QUICK_FIELDS}})
    assert res.status_code == 422


def test_quick_elements_report_real_positions_in_mm(client):
    res = client.post("/api/v1/print-templates/quick-elements", headers=AUTH,
                      json={"form": {"name": "e", **QUICK_FIELDS}})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["card"] == {"width_mm": 54.0, "height_mm": 85.6}
    user = data["elements"]["username"]
    # QUICK_FIELDS pins the username at x=6.5 / y=40 mm — the anchor the web
    # drag writes — so the reported box must start there.
    assert user["x"] == 6.5 and user["y"] == 40.0
    assert user["w"] > 0 and user["h"] > 0
    assert "password" in data["elements"] and "qr" in data["elements"]
    # Automatic (0) positions come back as real, non-zero places.
    assert data["elements"]["password"]["y"] > 0


def test_quick_elements_horizontal_card_swaps_the_mm_box(client):
    res = client.post("/api/v1/print-templates/quick-elements", headers=AUTH, json={
        "form": {"name": "h", **QUICK_FIELDS, "render_engine": "ar_horizontal",
                 "card_width_mm": "85.6", "card_height_mm": "54"}})
    card = res.get_json()["data"]["card"]
    assert card["width_mm"] > card["height_mm"]
