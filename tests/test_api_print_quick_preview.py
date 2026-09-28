"""API for the mobile app's «طباعة الكروت» screen (2026-09-28).

- POST /api/v1/print-templates/preview.pdf renders an UNSAVED template with
  the same drawing code as the export, and writes nothing.
- POST /api/v1/print-templates/background optimizes an image with the web's
  Pillow optimizer.
- GET/PUT /api/v1/print-templates/last-settings share the web quick screen's
  remembered sheet settings.
"""
from __future__ import annotations

import base64
import io
import secrets

import pytest


@pytest.fixture
def app(monkeypatch):
    token = "print-quick-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    created = create_app()
    created.config["TEST_API_TOKEN"] = token
    return created


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(client) -> dict:
    return {"Authorization": "Bearer " + client.application.config["TEST_API_TOKEN"]}


def _plan_id(client) -> int:
    # Other test files share the DB and may delete plan 1 — use any plan,
    # or create one.
    items = client.get("/api/v1/profiles", headers=_auth(client)).get_json()["data"]["items"]
    if items:
        return int(items[0]["id"])
    # Plan creation needs a full speed profile; when a previous test file
    # emptied the shared DB, skip rather than fail (the project runs test
    # files in isolation — see test-isolation-per-file).
    pytest.skip("no plan left in the shared test DB")


def _batch(client, count: int = 3) -> dict:
    res = client.post(
        "/api/v1/cards/generate",
        json={"plan_id": _plan_id(client), "count": count,
              "username_prefix": "pq" + secrets.token_hex(2)},
        headers=_auth(client),
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["batch"]


def _png_data_url(w: int = 64, h: int = 40) -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 120, 200)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _count(table: str) -> int:
    from app.radius.db.connection import db
    return int(db().execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def _pages(pdf: bytes) -> int:
    return pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page\r") + pdf.count(b"/Type /Page ")


QUICK = {
    "name": "قالب سريع",
    "show_qr": False,
    "layout": {
        "render_engine": "ar_vertical",
        "card_width_mm": 54,
        "card_height_mm": 85.6,
        "font_size_unit": "pt",
        "show_username": True,
        "show_password": True,
        "show_qr": False,
    },
}


def test_preview_page_is_a_pdf_and_writes_nothing(client):
    batch = _batch(client, count=3)
    jobs_before = _count("card_print_jobs") if _has_table("card_print_jobs") else None
    res = client.post(
        "/api/v1/print-templates/preview.pdf",
        json={
            "template": QUICK,
            "batch_id": batch["id"],
            "print_settings": {"print_columns": 3, "print_rows": 2,
                               "print_cut_lines": True},
        },
        headers=_auth(client),
    )
    assert res.status_code == 200, res.get_data(as_text=True)[:300]
    assert res.mimetype == "application/pdf"
    assert res.data.startswith(b"%PDF")
    assert res.headers["Cache-Control"].startswith("no-store")
    if jobs_before is not None:
        assert _count("card_print_jobs") == jobs_before  # no print job row


def _has_table(name: str) -> bool:
    from app.radius.db.connection import db
    return db().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def test_preview_card_mode_page_is_card_sized(client):
    res = client.post(
        "/api/v1/print-templates/preview.pdf",
        json={"template": QUICK, "mode": "card"},
        headers=_auth(client),
    )
    assert res.status_code == 200, res.get_data(as_text=True)[:300]
    # 54 x 85.6 mm → 153.07 x 242.65 pt
    assert b"/MediaBox [ 0 0 153.0709 242.6457 ]" in res.data or b"153.07" in res.data


def test_preview_uses_unsaved_edits_of_a_saved_template(client):
    created = client.post("/api/v1/print-templates",
                          json={**QUICK, "name": "pq_" + secrets.token_hex(3)},
                          headers=_auth(client))
    assert created.status_code == 201, created.get_json()
    tid = created.get_json()["data"]["template"]["id"]

    from app.radius.services.operations import get_operations_service
    row = get_operations_service().preview_print_template_row(
        tenant_id=1, template_id=tid,
        data={"layout": {"username_font_size": 17}, "username_x": 12.5},
    )
    assert row["layout_json"]["username_font_size"] == 17
    assert row["username_x"] == 12.5
    # …and the stored template is unchanged.
    got = client.get("/api/v1/print-templates", headers=_auth(client)).get_json()
    stored = next(t for t in got["data"]["items"] if t["id"] == tid)
    assert stored["username_x"] == 0

    res = client.post("/api/v1/print-templates/preview.pdf",
                      json={"template_id": tid, "template": {"layout": {"username_font_size": 17}}},
                      headers=_auth(client))
    assert res.status_code == 200


def test_preview_unknown_template_is_404(client):
    res = client.post("/api/v1/print-templates/preview.pdf",
                      json={"template_id": 999999, "template": {}},
                      headers=_auth(client))
    assert res.status_code == 404


def test_background_endpoint_optimizes_like_the_web(client):
    res = client.post("/api/v1/print-templates/background",
                      json={"data_url": _png_data_url(), "name": "bg.png"},
                      headers=_auth(client))
    assert res.status_code == 200, res.get_json()
    bg = res.get_json()["data"]["background"]
    assert bg["background_image_optimized"] is True
    assert bg["background_image_data_url"].startswith("data:image/jpeg;base64,")
    assert bg["background_image_name"] == "bg.png"

    bad = client.post("/api/v1/print-templates/background",
                      json={"data_url": "not-an-image"}, headers=_auth(client))
    assert bad.status_code == 422


def test_create_optimizes_a_new_background_once(client):
    raw = _png_data_url()
    created = client.post(
        "/api/v1/print-templates",
        json={**QUICK, "name": "pq_bg_" + secrets.token_hex(3),
              "layout": {**QUICK["layout"], "background_image_data_url": raw}},
        headers=_auth(client),
    )
    assert created.status_code == 201, created.get_json()
    layout = created.get_json()["data"]["template"]["layout_json"]
    assert layout["background_image_data_url"].startswith("data:image/jpeg")
    assert layout.get("background_image_optimized") is True


def test_last_settings_round_trip(client):
    put = client.put("/api/v1/print-templates/last-settings",
                     json={"print_columns": 4, "print_rows": 7,
                           "print_cut_lines": "1", "evil": "x"},
                     headers=_auth(client))
    assert put.status_code == 200, put.get_json()
    got = client.get("/api/v1/print-templates/last-settings",
                     headers=_auth(client)).get_json()["data"]["settings"]
    assert got["print_columns"] == "4"
    assert got["print_rows"] == "7"
    assert "evil" not in got


def test_export_still_prints_every_card_after_the_refactor(client):
    batch = _batch(client, count=5)
    created = client.post("/api/v1/print-templates",
                          json={**QUICK, "name": "pq_ex_" + secrets.token_hex(3)},
                          headers=_auth(client))
    tid = created.get_json()["data"]["template"]["id"]
    res = client.post(f"/api/v1/print-templates/{tid}/export.pdf",
                      json={"batch_id": batch["id"],
                            "print_settings": {"print_columns": 2, "print_rows": 2}},
                      headers=_auth(client))
    assert res.status_code == 200
    assert res.data.startswith(b"%PDF")
    # 5 cards at 4 per page → 2 pages.
    assert _pages(res.data) == 2
