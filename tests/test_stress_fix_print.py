# -*- coding: utf-8 -*-
"""Stress-campaign fixes (2026-09-28) — card printing (a07 F1–F9 + a06 M9).

* F1  quick-save on an EXISTING template merges (brand/footer/price/colours kept)
      — API quick-save, web quick screen, and the previews that mirror them.
* F2  QR clamped inside the card (all output paths share the model).
* F3  Arabic validation / not-found messages.
* F4  ₪ never tofu (word fallback), meta line ordered per part, serial kept.
* F5  export jobs validate settings before queueing (422, not 202 → fail).
* F7  light template list (+ ?full=1, single GET, background endpoint).
* F8  last-settings validated like the export; 0 is stored.
* F9  big credential fonts reflow — no overlap with meta/footer/each other.
* M9  printing an archived batch → 409.
"""
from __future__ import annotations

import base64
import io
import os
from uuid import uuid4

import pytest

TOKEN = "sf-print-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sf_print.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "sf-print-secret")
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


# exactly the app's QuickPrintForm.toFields() keys — no brand/footer/price/colours
QUICK_FIELDS = {
    "name": "", "font_size_unit": "pt", "image_fit": "stretch",
    "background_style": "preset", "render_engine": "ar_vertical",
    "card_width_mm": "54", "card_height_mm": "85.6",
    "design_preset": "modern", "hotspot_address": "hotspot.local",
    "background_image_data_url": "", "background_image_name": "",
    "show_username": "1", "show_password": "1",
    "username_font_size": "", "password_font_size": "",
    "show_qr": "1", "show_price": "1", "hotspot_login_url": "",
    "credential_background_enabled": "1",
    "username_surface_enabled": "1", "password_surface_enabled": "1",
    "surface_color": "#e8f7fb", "username_surface_color": "#e8f7fb",
    "password_surface_color": "#e8f7fb",
    "username_x": "0", "username_y": "0", "password_x": "0", "password_y": "0",
    "qr_x": "0", "qr_y": "0", "qr_size_pct": "0",
}

DESIGN = {
    "brand_name": "st07 نت", "footer_text": "Keep this card safe", "price_text": "5 ₪",
    "gradient_start": "#1adca9", "gradient_end": "#255126",
    "credential_label_font_size": 8, "surface_opacity": 0.88,
}


def _png_data_url() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 25), (200, 30, 30)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _create_template(client, name=None, **layout) -> int:
    body = {"name": name or f"sf-{uuid4().hex[:8]}",
            "layout": {"render_engine": "ar_vertical", "card_width_mm": 54,
                       "card_height_mm": 85.6, "design_preset": "modern",
                       **DESIGN, **layout}}
    res = client.post("/api/v1/print-templates", json=body, headers=AUTH)
    assert res.status_code == 201, res.get_json()
    return int(res.get_json()["data"]["template"]["id"])


def _stored(tid: int) -> dict:
    from app.radius.db.repos import operations_repo
    return operations_repo.get_print_template(1, tid)


def _assert_design_kept(tid: int):
    layout = _stored(tid)["layout_json"]
    for key, value in DESIGN.items():
        assert layout[key] == value, (key, layout[key])


# ── F1 ────────────────────────────────────────────────────────────────

def test_app_quick_save_on_existing_template_keeps_its_design(client):
    tid = _create_template(client)
    name = _stored(tid)["name"]
    res = client.post("/api/v1/print-templates/quick-save", json={
        "template_id": tid, "form": {**QUICK_FIELDS, "name": name, "qr_size_pct": "30"}},
        headers=AUTH)
    assert res.status_code == 200, res.get_json()
    _assert_design_kept(tid)
    assert _stored(tid)["layout_json"]["qr_size_pct"] == 30  # sent fields still apply


def test_quick_save_keeps_stored_background_when_the_form_has_no_bytes(client):
    tid = _create_template(client, background_style="image",
                           background_image_data_url=_png_data_url())
    name = _stored(tid)["name"]
    res = client.post("/api/v1/print-templates/quick-save", json={
        "template_id": tid,
        "form": {**QUICK_FIELDS, "name": name, "background_style": "image"}},
        headers=AUTH)
    assert res.status_code == 200
    layout = _stored(tid)["layout_json"]
    assert layout["background_style"] == "image"
    assert layout["background_image_data_url"].startswith("data:image/")


def test_changing_the_preset_without_colours_takes_the_new_preset_colours(client):
    from app.radius.services.operations import _PRINT_PRESETS
    tid = _create_template(client)
    other = next(k for k in _PRINT_PRESETS if k != "modern")
    res = client.post("/api/v1/print-templates/quick-save", json={
        "template_id": tid,
        "form": {**QUICK_FIELDS, "name": _stored(tid)["name"], "design_preset": other}},
        headers=AUTH)
    assert res.status_code == 200
    layout = _stored(tid)["layout_json"]
    assert layout["gradient_start"] == _PRINT_PRESETS[other]["gradient_start"]
    assert layout["brand_name"] == DESIGN["brand_name"]


def test_quick_preview_of_an_existing_template_uses_its_stored_design(client, app):
    tid = _create_template(client)
    captured = {}
    from app.radius.services import operations
    real = operations.OperationsService.render_print_preview_pdf

    def spy(self, **kw):
        captured["row"] = self.preview_print_template_row(
            tenant_id=kw["tenant_id"], template_id=kw["template_id"], data=kw["data"])
        return real(self, **kw)

    import unittest.mock as um
    with um.patch.object(operations.OperationsService, "render_print_preview_pdf", spy):
        res = client.post("/api/v1/print-templates/preview.pdf", json={
            "template_id": tid, "form": QUICK_FIELDS, "mode": "card"}, headers=AUTH)
    assert res.status_code == 200 and res.data[:4] == b"%PDF"
    assert captured["row"]["layout_json"]["brand_name"] == DESIGN["brand_name"]


def _web_login(client) -> str:
    from app.radius.db.repos import admins_repo
    u = f"sfp_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="sfp-pass", full_name="SF",
                             is_super_admin=True)
    res = client.post("/admin/radius/login", data={"username": u, "password": "sfp-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/cards/print")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def test_web_quick_screen_save_keeps_the_design(client):
    tid = _create_template(client)
    csrf = _web_login(client)
    form = {**QUICK_FIELDS, "name": _stored(tid)["name"], "return_to": "quick",
            "_csrf_token": csrf}
    res = client.post(f"/admin/radius/print-templates/{tid}/edit", data=form)
    assert res.status_code in {302, 303}
    _assert_design_kept(tid)


def test_web_full_designer_still_replaces(client):
    """Without return_to=quick the full designer's form is authoritative."""
    tid = _create_template(client)
    csrf = _web_login(client)
    form = {**QUICK_FIELDS, "name": _stored(tid)["name"], "_csrf_token": csrf,
            "brand_name": "New Brand"}
    res = client.post(f"/admin/radius/print-templates/{tid}/edit", data=form)
    assert res.status_code in {302, 303}
    assert _stored(tid)["layout_json"]["brand_name"] == "New Brand"
    assert _stored(tid)["layout_json"]["footer_text"] != DESIGN["footer_text"]


# ── F2 / F9: renderer geometry ────────────────────────────────────────

def _model(**layout):
    from app.radius.services.card_renderer import build_card_render_model
    from app.radius.services.operations import _template_layout
    base = {"render_engine": "ar_horizontal", "card_width_mm": 85.6, "card_height_mm": 54,
            "show_qr": 1, "hotspot_address": "hotspot.local", "footer_text": "احتفظ بالبطاقة"}
    top = {k: layout.pop(k) for k in list(layout) if k.endswith(("_x", "_y"))}
    base.update(layout)
    tpl = {"id": 1, "layout_json": _template_layout({"layout": base, **base}), **top}
    return build_card_render_model(tpl, {"id": 314, "username": "st07673521", "password": "e1baqm"})


def _box(el):
    if el["kind"] == "qr":
        return el["x"], el["y"], el["x"] + el["size"], el["y"] + el["size"]
    if el["kind"] == "pill":
        return el["x"], el["y"], el["x"] + el["width"], el["y"] + el["height"]
    return el["x"], el["y"], el["x"] + el["max_width"], el["y"] + el["size"] * 1.2


def _overlap(a, b):
    ax0, ay0, ax1, ay1 = _box(a)
    bx0, by0, bx1, by1 = _box(b)
    return ax0 < bx1 - 0.5 and ax1 > bx0 + 0.5 and ay0 < by1 - 0.5 and ay1 > by0 + 0.5


@pytest.mark.parametrize("layout", [
    {"qr_size_pct": 40, "qr_y": 19.44},
    {"qr_x": 84, "qr_y": 53},
    {"qr_size_pct": 48, "qr_x": 80, "qr_y": 50, "render_engine": "ar_vertical",
     "card_width_mm": 54, "card_height_mm": 85.6},
])
def test_qr_is_always_inside_the_card(app, layout):
    m = _model(**layout)
    cw, ch = m["canvas"]["width"], m["canvas"]["height"]
    qr = next(e for e in m["elements"] if e["kind"] == "qr")
    assert qr["x"] >= 0 and qr["y"] >= 0
    assert qr["x"] + qr["size"] <= cw + 1e-6 and qr["y"] + qr["size"] <= ch + 1e-6


def test_quick_elements_reports_the_clamped_qr(client):
    res = client.post("/api/v1/print-templates/quick-elements", json={
        "form": {**QUICK_FIELDS, "render_engine": "ar_horizontal", "card_width_mm": "85.6",
                 "card_height_mm": "54", "qr_size_pct": "40", "qr_y": "19.44"}},
        headers=AUTH)
    data = res.get_json()["data"]
    qr, card = data["elements"]["qr"], data["card"]
    assert qr["y"] + qr["h"] <= card["height_mm"] + 0.05
    assert qr["x"] + qr["w"] <= card["width_mm"] + 0.05


@pytest.mark.parametrize("layout", [
    # the owner's template 1: credentials dragged + huge fonts
    {"show_qr": 0, "username_x": 8, "username_y": 0.1, "password_x": 32, "password_y": 36,
     "username_font_size": 90, "password_font_size": 118, "credential_label_font_size": 55},
    {"username_font_size": 20.5, "password_font_size": 32.5, "font_size_unit": "pt"},
    {"username_font_size": 30, "password_font_size": 30, "font_size_unit": "pt",
     "render_engine": "ar_vertical", "card_width_mm": 54, "card_height_mm": 85.6},
])
def test_big_fonts_never_overlap_meta_footer_or_each_other(app, layout):
    m = _model(**layout)
    els = {e["id"]: e for e in m["elements"] if e.get("id") in ("user", "pass", "meta", "footer", "qr")}
    assert "meta" in els and "footer" in els
    for pill in ("user", "pass"):
        for other in ("meta", "footer", "qr"):
            if other in els:
                assert not _overlap(els[pill], els[other]), (pill, other, _box(els[pill]), _box(els[other]))
    assert not _overlap(els["user"], els["pass"])
    assert not _overlap(els["meta"], els["footer"])


def test_default_layout_is_not_moved_by_the_reflow(app):
    m = _model(show_qr=0, footer_text="")
    user = next(e for e in m["elements"] if e.get("id") == "user")
    pas = next(e for e in m["elements"] if e.get("id") == "pass")
    assert (round(user["y"]), round(pas["y"])) == (300, 396)


# ── F4: glyphs + meta line ────────────────────────────────────────────

def test_shekel_becomes_a_word_when_no_font_has_it(monkeypatch):
    from app.radius.services import card_renderer as cr
    monkeypatch.setattr(cr, "_symbol_fallback_font_candidates", lambda **k: [])
    monkeypatch.setattr(cr, "_font_path_for_arabic", lambda **k: cr._ALMARAI_BOLD_PATH)
    monkeypatch.setattr(cr, "_pil_supports_raqm", lambda: False)
    assert cr._printable_symbols("10 ₪") == "10 شيكل"


def test_cmap_reader_needs_no_fonttools():
    from app.radius.services import card_renderer as cr
    cps = cr._read_cmap_codepoints(cr._ALMARAI_REGULAR_PATH)
    assert cps and ord("ب") in cps and ord("#") in cps
    assert 0x20AA not in cps  # the reason ₪ was tofu


def test_meta_line_keeps_each_part_intact():
    from app.radius.services import card_renderer as cr
    text, visual = cr._meta_line(["hotspot.local", "10 شيكل", "#314"], direction="rtl")
    assert visual is True
    parts = text.split(cr._META_SEP)
    assert parts[0] == "#314" and parts[-1] == "hotspot.local"   # RTL reading order
    assert "10" in parts[1] and "·" not in parts[1]
    latin, visual2 = cr._meta_line(["hotspot.local", "#314"], direction="rtl")
    assert (latin, visual2) == ("hotspot.local  ·  #314", False)


def test_vertical_card_keeps_the_serial(app):
    m = _model(render_engine="ar_vertical", card_width_mm=54, card_height_mm=85.6,
               show_price=1, price_text="10 شيكل للبطاقة الواحدة", validity_text="يوم واحد كامل",
               hotspot_address="very-long-hotspot-address.example.net")
    meta = next(e for e in m["elements"] if e.get("id") == "meta")
    assert "#314" in meta["text"]


# ── F3/F5/F8: settings validation + Arabic ────────────────────────────

def _arabic(msg: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in msg)


@pytest.mark.parametrize("settings", [
    {"print_columns": 13}, {"print_columns": 0}, {"print_columns": "abc"},
    {"print_columns": 2.5}, {"print_rows": 21}, {"print_page_size": "A3"},
    {"print_orientation": "diag"}, {"print_margin_mm": -50},
    {"print_columns": 12, "print_column_gap_mm": 60},
])
def test_preview_settings_errors_are_arabic_422(client, settings):
    res = client.post("/api/v1/print-templates/preview.pdf", json={
        "form": QUICK_FIELDS, "print_settings": settings}, headers=AUTH)
    assert res.status_code == 422
    assert _arabic(res.get_json()["error"]["message"])


def test_not_found_messages_are_arabic(client):
    for url in ("/api/v1/print-jobs/999999", "/api/v1/print-templates/999999"):
        res = client.get(url, headers=AUTH)
        assert res.status_code == 404 and _arabic(res.get_json()["error"]["message"])


def _batch(app, count=3):
    from app.radius.services.cards import get_cards_service
    from app.radius.db.connection import db
    with app.test_request_context():
        from flask import g
        g.tenant_id = 1
        pid = db().execute(
            "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, price,"
            " currency, speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at)"
            " VALUES(1,?,60,1,1,'ILS',1024,1024,0,datetime('now'),datetime('now'))",
            (f"p-{uuid4().hex[:6]}",)).lastrowid
        batch, _ = get_cards_service().generate_batch(
            actor="t", plan_id=int(pid), count=count, username_length=10)
    return batch.id


def test_export_job_with_bad_settings_is_422_before_queueing(client, app):
    tid = _create_template(client)
    bid = _batch(app)
    from app.radius.db.connection import db
    before = db().execute("SELECT COUNT(*) AS c FROM print_jobs").fetchone()["c"]
    for settings in ({"print_columns": 13}, {"print_page_size": "A3"},
                     {"print_columns": 12, "print_column_gap_mm": 60}):
        res = client.post(f"/api/v1/print-templates/{tid}/export-jobs",
                          json={"batch_id": bid, "print_settings": settings}, headers=AUTH)
        assert res.status_code == 422 and _arabic(res.get_json()["error"]["message"])
    after = db().execute("SELECT COUNT(*) AS c FROM print_jobs").fetchone()["c"]
    assert after == before


def test_archived_batch_cannot_be_printed(client, app):
    tid = _create_template(client)
    bid = _batch(app)
    from app.radius.db.repos import cards_repo
    cards_repo.archive_batch(1, bid, actor="t")
    res = client.post(f"/api/v1/print-templates/{tid}/export-jobs",
                      json={"batch_id": bid}, headers=AUTH)
    assert res.status_code == 409 and _arabic(res.get_json()["error"]["message"])
    res = client.post(f"/api/v1/print-templates/{tid}/export.pdf",
                      json={"batch_id": bid}, headers=AUTH)
    assert res.status_code == 409


def test_last_settings_validated_and_zero_kept(client):
    bad = client.put("/api/v1/print-templates/last-settings", json={
        "print_columns": 99, "print_page_size": "A3", "print_margin_mm": -50}, headers=AUTH)
    assert bad.status_code == 422 and _arabic(bad.get_json()["error"]["message"])
    ok = client.put("/api/v1/print-templates/last-settings", json={
        "print_columns": 4, "print_margin_mm": 0}, headers=AUTH)
    assert ok.status_code == 200
    s = ok.get_json()["data"]["settings"]
    assert s["print_columns"] == "4" and s["print_margin_mm"] == "0"


def test_quick_save_with_bad_print_settings_saves_nothing(client):
    name = f"sf-{uuid4().hex[:6]}"
    res = client.post("/api/v1/print-templates/quick-save", json={
        "form": {**QUICK_FIELDS, "name": name}, "print_settings": {"print_columns": 99}},
        headers=AUTH)
    assert res.status_code == 422
    from app.radius.db.connection import db
    assert db().execute("SELECT COUNT(*) AS c FROM card_print_templates WHERE name = ?",
                        (name,)).fetchone()["c"] == 0


# ── F7: light list ────────────────────────────────────────────────────

def test_template_list_is_light_by_default(client):
    tid = _create_template(client, background_style="image",
                           background_image_data_url=_png_data_url())
    res = client.get("/api/v1/print-templates?limit=200", headers=AUTH)
    item = next(i for i in res.get_json()["data"]["items"] if i["id"] == tid)
    assert "background_image_data_url" not in item["layout_json"]
    assert item["has_background_image"] is True
    assert item["layout_json"]["has_background_image"] is True
    assert item["layout_json"]["brand_name"] == DESIGN["brand_name"]  # everything else stays
    full = client.get("/api/v1/print-templates?limit=200&full=1", headers=AUTH)
    fitem = next(i for i in full.get_json()["data"]["items"] if i["id"] == tid)
    assert fitem["layout_json"]["background_image_data_url"].startswith("data:image/")
    one = client.get(f"/api/v1/print-templates/{tid}", headers=AUTH)
    assert one.get_json()["data"]["template"]["layout_json"]["background_image_data_url"]
    img = client.get(item["background_image_url"], headers=AUTH)
    assert img.status_code == 200 and img.mimetype.startswith("image/")
    svg = client.get(item["thumbnail_url"], headers=AUTH)
    assert svg.status_code == 200 and b"<svg" in svg.data[:400]
