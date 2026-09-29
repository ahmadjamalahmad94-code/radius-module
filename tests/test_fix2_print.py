# -*- coding: utf-8 -*-
"""Fix wave 2 (2026-09-29) — card printing (r06 N1–N8, F9/F10 residues,
r13 I1/I2, r11 M-4).

* 6  the QR never covers the username/password (render model → every path);
     no room at all → the save is refused (422).
* 7  quick-elements / preview report the boxes actually used; the QR box is
     the drawn square.
* 8  print-job cancel is final and consistent (cancelled / 409, never a
     «cancelled» job that ends «success» or «failed»).
* 9  web designer «تحديث القالب» with no edits changes nothing.
* 10 validation: name/card size/font caps, enums, GIF, NaN message, JSON 404
     for bad job ids, designer-svg 422, quick-screen error redirect, label vs
     value inside a pill.
* 11 no borrowed QR login link, per-admin last template, 409 duplicate_name,
     footer cut at a word, «auto» size sent back = auto.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from html.parser import HTMLParser
from uuid import uuid4

import pytest

TOKEN = "f2-print-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "f2_print.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "f2-print-secret")
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


# the app's QuickPrintForm.toFields() keys
QUICK_FIELDS = {
    "name": "", "font_size_unit": "pt", "image_fit": "stretch",
    "background_style": "preset", "render_engine": "ar_horizontal",
    "card_width_mm": "85.6", "card_height_mm": "54",
    "design_preset": "modern", "hotspot_address": "hotspot.local",
    "background_image_data_url": "", "background_image_name": "",
    "show_username": "1", "show_password": "1",
    "username_font_size": "", "password_font_size": "",
    "show_qr": "1", "show_price": "0", "hotspot_login_url": "",
    "credential_background_enabled": "1",
    "username_surface_enabled": "1", "password_surface_enabled": "1",
    "surface_color": "#e8f7fb", "username_surface_color": "#e8f7fb",
    "password_surface_color": "#e8f7fb",
    "username_x": "0", "username_y": "0", "password_x": "0", "password_y": "0",
    "qr_x": "0", "qr_y": "0", "qr_size_pct": "0",
}
VERTICAL = {"render_engine": "ar_vertical", "card_width_mm": "54", "card_height_mm": "85.6"}


def _create_template(client, name=None, top=None, **layout) -> int:
    body = {"name": name or f"f2-{uuid4().hex[:8]}",
            "layout": {"render_engine": "ar_horizontal", "card_width_mm": 85.6,
                       "card_height_mm": 54, "design_preset": "modern",
                       "font_size_unit": "pt", **layout}, **(top or {})}
    res = client.post("/api/v1/print-templates", json=body, headers=AUTH)
    assert res.status_code == 201, res.get_json()
    return int(res.get_json()["data"]["template"]["id"])


def _stored(tid: int) -> dict:
    from app.radius.db.repos import operations_repo
    return operations_repo.get_print_template(1, tid)


def _model(top=None, card=None, **layout):
    from app.radius.services.card_renderer import build_card_render_model
    from app.radius.services.operations import _template_layout
    base = {"render_engine": "ar_horizontal", "card_width_mm": 85.6, "card_height_mm": 54,
            "show_qr": 1, "hotspot_address": "hotspot.local", "font_size_unit": "pt",
            "footer_text": "احتفظ ببيانات الدخول حتى انتهاء الصلاحية"}
    base.update(layout)
    tpl = {"id": 1, "layout_json": _template_layout({"layout": base, **base}), **(top or {})}
    return build_card_render_model(
        tpl, card or {"id": 313, "username": "0123456789012", "password": "848827"})


def _pills_and_qr(model):
    pills = [e for e in model["elements"] if e.get("kind") == "pill"]
    qr = next((e for e in model["elements"] if e.get("kind") == "qr"), None)
    return pills, qr


def _intersects(qr, p) -> bool:
    return (qr["x"] < p["x"] + p["width"] and qr["x"] + qr["size"] > p["x"]
            and qr["y"] < p["y"] + p["height"] and qr["y"] + qr["size"] > p["y"])


def _web_login(client, username=None) -> str:
    from app.radius.db.repos import admins_repo
    u = username or f"f2p_{uuid4().hex[:10]}"
    try:
        admins_repo.create_admin(username=u, password="f2p-pass", full_name="F2",
                                 is_super_admin=True)
    except Exception:  # noqa: BLE001 — already there (second login)
        pass
    res = client.post("/admin/radius/login", data={"username": u, "password": "f2p-pass"})
    assert res.status_code in {302, 303}
    client.get("/admin/radius/cards/print")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


# ══ 6 · the QR never covers the credentials ═══════════════════════════

QR_CASES = [
    # r06 N1 repros
    {"top": {"qr_x": 45, "qr_y": 25}},
    {"top": {"qr_x": 60, "qr_y": 20}, "qr_size_pct": 40},
    {"top": {"qr_x": 20, "qr_y": 45}, **{k: float(v) if k != "render_engine" else v
                                         for k, v in VERTICAL.items()}},
    # big QR at its automatic place, and dragged onto each pill
    {"qr_size_pct": 48},
    {"qr_size_pct": 48, "render_engine": "ar_vertical", "card_width_mm": 54, "card_height_mm": 85.6},
    {"top": {"qr_x": 50, "qr_y": 28}, "qr_size_pct": 30},
    {"top": {"qr_x": 5, "qr_y": 5}, "qr_size_pct": 48},
]


@pytest.mark.parametrize("engine", ["ar_horizontal", "en_horizontal", "ar_vertical", "en_vertical"])
@pytest.mark.parametrize("case", QR_CASES)
def test_qr_never_overlaps_the_credentials_in_the_model(app, engine, case):
    case = dict(case)
    top = case.pop("top", None)
    layout = {**case, "render_engine": engine}
    if engine.endswith("vertical"):
        layout.update(card_width_mm=54, card_height_mm=85.6)
    else:
        layout.update(card_width_mm=85.6, card_height_mm=54)
    for fonts in ({}, {"username_font_size": 20, "password_font_size": 24}):
        model = _model(top=top, **layout, **fonts)
        pills, qr = _pills_and_qr(model)
        assert len(pills) == 2
        cw, ch = model["canvas"]["width"], model["canvas"]["height"]
        if qr is None:
            assert model["qr_conflict"] and model["warnings"]
            continue
        assert 0 <= qr["x"] and qr["x"] + qr["size"] <= cw + 1e-6
        assert 0 <= qr["y"] and qr["y"] + qr["size"] <= ch + 1e-6
        for p in pills:
            assert not _intersects(qr, p), (engine, case, fonts, qr, p)


def test_qr_moved_off_the_pills_on_every_output_path(client):
    """quick-elements, preview.pdf (header) and the web designer-svg all read
    the SAME model: the QR repro of r06 N1 is moved there."""
    form = {**QUICK_FIELDS, "qr_x": "45", "qr_y": "25"}
    res = client.post("/api/v1/print-templates/quick-elements", json={"form": form}, headers=AUTH)
    data = res.get_json()["data"]
    els = data["elements"]
    qr, user, pw = els["qr"], els["username"], els["password"]

    def hit(a, b):
        return a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"] and \
            a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"]
    assert not hit(qr, user) and not hit(qr, pw)
    assert qr["adjusted"] is True and qr["requested"] == {"x": 45.0, "y": 25.0}
    assert any("QR" in w for w in data["warnings"])

    for mode in ("card", "page"):
        res = client.post("/api/v1/print-templates/preview.pdf",
                          json={"form": form, "mode": mode}, headers=AUTH)
        assert res.status_code == 200 and res.data[:4] == b"%PDF"
        import fitz
        doc = fitz.open(stream=res.data, filetype="pdf")
        assert doc.page_count == 1 and doc[0].get_pixmap(dpi=40).width > 0
        header = json.loads(res.headers["X-Print-Elements"])
        assert header["elements"]["qr"] == qr

    csrf = _web_login(client)
    res = client.post("/admin/radius/print-templates/designer-svg",
                      data={**form, "_csrf_token": csrf})
    assert res.status_code == 200 and res.mimetype == "image/svg+xml"
    svg = res.get_data(as_text=True)
    m = re.search(r'<g class="card-qr" data-el-x="([\d.]+)" data-el-y="([\d.]+)"', svg)
    assert m, "the web preview draws the QR"
    # the web preview puts the QR where the model (and the API) put it
    assert abs(float(m.group(1)) - qr["x"] / 85.6 * 1000) < 0.2
    assert abs(float(m.group(2)) - qr["y"] / 54 * 600) < 0.2
    assert abs(float(m.group(1)) - 45 / 85.6 * 1000) > 50


def test_export_pdf_of_an_overlapping_template_moves_the_qr(client):
    """A template stored before the fix (QR over the pills) still prints —
    with the QR moved — on the export path (sync export.pdf)."""
    from app.radius.db.repos import operations_repo
    tid = _create_template(client)
    operations_repo.update_print_template(1, tid, {**_stored(tid), "qr_x": 45, "qr_y": 25,
                                                   "layout": _stored(tid)["layout_json"]},
                                          actor="t")
    res = client.post(f"/api/v1/print-templates/{tid}/export.pdf", json={}, headers=AUTH)
    assert res.status_code == 200 and res.data[:4] == b"%PDF"
    from app.radius.services.card_renderer import build_card_render_model
    model = build_card_render_model(_stored(tid), {"id": 1, "username": "0123456789012",
                                                   "password": "123456"})
    pills, qr = _pills_and_qr(model)
    assert qr is not None and not any(_intersects(qr, p) for p in pills)


# a 30×20 mm card with 20 pt credentials: the pills fill it, no QR fits
NO_ROOM = {"card_width_mm": 30, "card_height_mm": 20, "username_font_size": 20,
           "password_font_size": 20, "qr_size_pct": 48}


def test_no_room_for_the_qr_refuses_the_save(client, app):
    model = _model(**NO_ROOM)
    pills, qr = _pills_and_qr(model)
    assert model["qr_conflict"] and qr is None and len(pills) == 2  # drawn without QR
    body = {"name": f"f2-{uuid4().hex[:6]}",
            "layout": {"render_engine": "ar_horizontal", "font_size_unit": "pt", **NO_ROOM}}
    res = client.post("/api/v1/print-templates", json=body, headers=AUTH)
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert "QR" in msg and "اسم المستخدم" in msg
    form = {**QUICK_FIELDS, "name": f"f2-{uuid4().hex[:6]}",
            **{k: str(v) for k, v in NO_ROOM.items()}}
    res = client.post("/api/v1/print-templates/quick-save", json={"form": form}, headers=AUTH)
    assert res.status_code == 422 and "QR" in res.get_json()["error"]["message"]
    # the live preview still answers (no QR drawn) and says why
    data = client.post("/api/v1/print-templates/quick-elements", json={"form": form},
                       headers=AUTH).get_json()["data"]
    assert data["qr_conflict"] is True and "qr" not in data["elements"]
    assert any("QR" in w for w in data["warnings"])
    # web: the quick screen's «حفظ» flashes it and stays on the quick screen
    csrf = _web_login(client)
    res = client.post("/admin/radius/print-templates", data={
        **form, "return_to": "quick", "_csrf_token": csrf}, follow_redirects=True)
    assert "لا توجد مساحة لرمز QR" in res.get_data(as_text=True)


def test_qr_guard_helper_raises_arabic_422(app, monkeypatch):
    from app.radius.core.errors import RadiusValidationError
    from app.radius.services import card_renderer, operations
    monkeypatch.setattr(card_renderer, "build_card_render_model",
                        lambda *a, **k: {"qr_conflict": True,
                                         "warnings": [card_renderer.QR_NO_ROOM_MESSAGE]})
    with pytest.raises(RadiusValidationError) as err:
        operations._reject_qr_over_credentials({"layout_json": {}})
    assert "QR" in err.value.message and "اسم المستخدم" in err.value.message


def test_qr_placement_removes_the_qr_when_nothing_fits(app):
    from app.radius.services.card_renderer import _keep_qr_clear_of_credentials
    els = [{"kind": "pill", "id": "user", "x": 0, "y": 0, "width": 1000, "height": 290},
           {"kind": "pill", "id": "pass", "x": 0, "y": 300, "width": 1000, "height": 290},
           {"kind": "qr", "id": "qr", "x": 100, "y": 100, "size": 270}]
    adj = []
    assert _keep_qr_clear_of_credentials(els, 1000, 600, adjustments=adj) == "removed"
    assert not any(e["kind"] == "qr" for e in els) and adj[0]["action"] == "removed"


# ══ 7 · reported boxes = drawn boxes ══════════════════════════════════

class _FakePdf:
    def __init__(self):
        self.ops = []

    def saveState(self): pass
    def restoreState(self): pass
    def translate(self, x, y): self.ops.append(("t", x, y))
    def scale(self, x, y): self.ops.append(("s", x, y))


def test_quick_elements_qr_box_is_the_drawn_square(client, monkeypatch):
    from reportlab.lib.units import mm
    from app.radius.services import card_renderer
    form = {**QUICK_FIELDS, "qr_size_pct": "40", "qr_y": "18"}
    data = client.post("/api/v1/print-templates/quick-elements", json={"form": form},
                       headers=AUTH).get_json()["data"]
    qr = data["elements"]["qr"]
    assert qr["w"] == qr["h"]
    # what place_card_qr draws on a card-sized page (card-mode preview)
    from app.radius.services.operations import get_operations_service
    row = get_operations_service().preview_print_template_row(
        tenant_id=1, template_id=None,
        data={"layout": {**{k: v for k, v in form.items()}, "font_size_unit": "pt"},
              "qr_x": 0, "qr_y": 18})
    model = card_renderer.build_card_render_model(row, {"id": "", "username": "0123456789012",
                                                        "password": "123456"})
    monkeypatch.setattr(card_renderer, "_pdf_qr", lambda *a, **k: None)
    pdf = _FakePdf()
    w, h = 85.6 * mm, 54 * mm
    card_renderer.place_card_qr(pdf, model, slot_x=0, slot_y=0, slot_width=w,
                                slot_height=h, stretch=True)
    (_, tx, ty), (_, s, _s2) = pdf.ops[0], pdf.ops[1]
    el = next(e for e in model["elements"] if e["kind"] == "qr")
    side_mm = el["size"] * s / mm
    left_mm, top_mm = tx / mm, (h - (ty + el["size"] * s)) / mm
    assert abs(side_mm - qr["w"]) < 0.02
    assert abs(left_mm - qr["x"]) < 0.02 and abs(top_mm - qr["y"]) < 0.02


def test_dropped_username_stays_where_it_was_dropped(client):
    """r06 N7: username dropped next to the QR at x 16.1 mm was drawn at
    29.96 mm (narrowed against the QR). Now the QR yields; the reported box is
    the dropped one."""
    form = {**QUICK_FIELDS, "username_x": "16.1", "username_y": "20"}
    data = client.post("/api/v1/print-templates/quick-elements", json={"form": form},
                       headers=AUTH).get_json()["data"]
    user = data["elements"]["username"]
    assert abs(user["x"] - 16.1) < 0.05 and user["adjusted"] is False
    assert user["requested"] == {"x": 16.1, "y": 20.0}


def test_reflowed_password_reports_adjusted(client):
    form = {**QUICK_FIELDS, "password_y": "44.7", "password_x": "40"}
    data = client.post("/api/v1/print-templates/quick-elements", json={"form": form},
                       headers=AUTH).get_json()["data"]
    pw = data["elements"]["password"]
    assert pw["requested"] == {"x": 40.0, "y": 44.7}
    assert pw["adjusted"] is (abs(pw["y"] - 44.7) > 0.2)


# ══ 8 · print-job cancel ══════════════════════════════════════════════

def _batch_with_cards(app, n=6) -> int:
    from app.radius.services.cards import get_cards_service
    svc = get_cards_service()
    res = svc.generate_cards(**{
        "count": n, "batch_name": f"f2-{uuid4().hex[:6]}", "username_length": 8,
        "password_length": 6, "actor": "t"}) if "batch_name" in \
        svc.generate_cards.__code__.co_varnames else None
    batch_id = (res or {}).get("batch_id") if isinstance(res, dict) else getattr(res, "batch_id", None)
    return int(batch_id or 0)


@pytest.fixture
def job_env(app, client, monkeypatch):
    """A queued export job whose worker we run by hand (deterministic)."""
    from app.radius.services import operations
    submitted = []
    monkeypatch.setattr(operations._PRINT_EXPORT_EXECUTOR, "submit",
                        lambda fn, *a: submitted.append((fn, a)))
    tid = _create_template(client)
    res = client.post(f"/api/v1/print-templates/{tid}/export-jobs",
                      json={"sample": {"cards": [{"id": i, "username": f"u{i:08d}",
                                                  "password": "123456"} for i in range(8)]},
                            "print_settings": {"print_columns": 1, "print_rows": 1}},
                      headers=AUTH)
    assert res.status_code == 202, res.get_json()
    job_id = res.get_json()["data"]["job"]["id"]
    return {"svc": operations.get_operations_service(), "job_id": job_id,
            "run": lambda: submitted[0][0](*submitted[0][1]), "ops": operations}


def _job(client, job_id):
    return client.get(f"/api/v1/print-jobs/{job_id}", headers=AUTH).get_json()["data"]["job"]


def test_cancel_while_rendering_ends_cancelled_not_failed(client, job_env, monkeypatch):
    ops, job_id = job_env["ops"], job_env["job_id"]
    real = ops._draw_print_cards

    def draw(pdf, **kw):
        on_card = kw["on_card"]

        def hooked(idx):
            if idx == 2:
                r = client.post(f"/api/v1/print-jobs/{job_id}/cancel", headers=AUTH)
                assert r.status_code == 200
            on_card(idx)
        return real(pdf, **{**kw, "on_card": hooked})
    monkeypatch.setattr(ops, "_draw_print_cards", draw)
    job_env["run"]()
    job = _job(client, job_id)
    assert job["status"] == "cancelled"
    assert job["message"] == "أُلغيت مهمة الطباعة."
    assert job["download_ready"] is False
    dl = client.get(f"/api/v1/print-jobs/{job_id}/download", headers=AUTH)
    assert dl.status_code == 409 and "أُلغيت" in dl.get_json()["error"]["message"]


def test_late_cancel_during_finalizing_is_final(client, job_env, monkeypatch):
    svc, job_id = job_env["svc"], job_env["job_id"]
    ops = job_env["ops"]
    real_export = ops.OperationsService.export_print_template_pdf

    def export(self, **kw):
        payload = real_export(self, **kw)
        r = client.post(f"/api/v1/print-jobs/{job_id}/cancel", headers=AUTH)
        assert r.status_code == 200 and r.get_json()["data"]["job"]["status"] == "cancelled"
        return payload
    monkeypatch.setattr(ops.OperationsService, "export_print_template_pdf", export)
    job_env["run"]()
    job = _job(client, job_id)
    assert job["status"] == "cancelled" and job["download_ready"] is False
    assert client.get(f"/api/v1/print-jobs/{job_id}/download", headers=AUTH).status_code == 409
    export_dir = svc._print_export_dir(1)
    assert not list(export_dir.glob(f"*-job-{job_id}.pdf"))


def test_cancel_racing_the_success_write_loses_nothing(client, job_env, monkeypatch):
    """The worker checked «not cancelled», then the cancel landed before the
    success write: the conditional UPDATE keeps «cancelled» and the file goes."""
    ops, job_id, svc = job_env["ops"], job_env["job_id"], job_env["svc"]
    monkeypatch.setattr(ops, "_print_job_cancelled", lambda *a: False)
    real_finish = ops.operations_repo.finish_print_job_if

    def finish(tenant_id, jid, **kw):
        if kw.get("status") == "success":
            svc.cancel_print_job(tenant_id=tenant_id, job_id=jid, actor="t")
        return real_finish(tenant_id, jid, **kw)
    monkeypatch.setattr(ops.operations_repo, "finish_print_job_if", finish)
    job_env["run"]()
    job = _job(client, job_id)
    assert job["status"] == "cancelled" and job["message"] == "أُلغيت مهمة الطباعة."
    assert not list(svc._print_export_dir(1).glob(f"*-job-{job_id}.pdf"))


def test_cancel_after_success_is_409(client, job_env):
    job_env["run"]()
    job_id = job_env["job_id"]
    assert _job(client, job_id)["status"] == "success"
    res = client.post(f"/api/v1/print-jobs/{job_id}/cancel", headers=AUTH)
    assert res.status_code == 409
    msg = res.get_json()["error"]["message"]
    assert "لا يمكن إلغاؤها" in msg and "مكتملة" in msg
    assert _job(client, job_id)["status"] == "success"
    assert client.get(f"/api/v1/print-jobs/{job_id}/download", headers=AUTH).status_code == 200


def test_cancel_twice_is_idempotent_and_a_failure_keeps_its_message(client, job_env, monkeypatch):
    job_id = job_env["job_id"]
    assert client.post(f"/api/v1/print-jobs/{job_id}/cancel", headers=AUTH).status_code == 200
    assert client.delete(f"/api/v1/print-jobs/{job_id}", headers=AUTH).status_code == 200
    job_env["run"]()  # the queued job never starts
    assert _job(client, job_id)["status"] == "cancelled"


def test_a_real_failure_is_failed_with_a_message(client, job_env, monkeypatch):
    ops = job_env["ops"]

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(ops, "_draw_print_cards", boom)
    job_env["run"]()
    job = _job(client, job_env["job_id"])
    assert job["status"] == "failed" and job["message"]


# ══ 9 · web designer: «تحديث القالب» with no edits is a no-op ═════════

class _FormParser(HTMLParser):
    """Collect what a browser would post for the designer form — applying
    the page's submit rule (untouched sliders post data-orig / 0 if auto)."""

    def __init__(self):
        super().__init__()
        self.in_form = False
        self.fields: list[tuple[str, str]] = []
        self._select = None
        self._select_first = None
        self._select_chosen = None
        self._textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and "data-pr-form" in a:
            self.in_form = True
            return
        if not self.in_form:
            return
        if tag == "input":
            t = (a.get("type") or "text").lower()
            name = a.get("name")
            if not name or t in {"file", "submit", "button"}:
                return
            if t == "checkbox" and "checked" not in a:
                return
            value = a.get("value") or ""
            if t == "range" and "data-orig" in a:
                value = "0" if a.get("data-auto-default") == "1" else a["data-orig"]
            self.fields.append((name, value))
        elif tag == "select":
            self._select, self._select_first, self._select_chosen = a.get("name"), None, None
        elif tag == "option" and self._select:
            v = a.get("value") or ""
            if self._select_first is None:
                self._select_first = v
            if "selected" in a:
                self._select_chosen = v
        elif tag == "textarea":
            self._textarea = [a.get("name"), ""]

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea[1] += data

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False
        elif tag == "select" and self._select:
            v = self._select_chosen if self._select_chosen is not None else self._select_first
            self.fields.append((self._select, v or ""))
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            if self._textarea[0]:
                self.fields.append((self._textarea[0], self._textarea[1].strip("\n")))
            self._textarea = None


def test_web_designer_update_without_edits_changes_nothing(client, app):
    tid = _create_template(client, **{
        "surface_opacity": 0.88, "image_opacity": 0.82, "pattern_color": "#abcdef",
        "brand_name": "st07 نت", "footer_text": "Keep this card safe",
        "watermark_opacity": 0.27})
    before = _stored(tid)
    csrf = _web_login(client)
    page = client.get(f"/admin/radius/print-templates?edit_template={tid}")
    assert page.status_code == 200
    parser = _FormParser()
    parser.feed(page.get_data(as_text=True))
    from werkzeug.datastructures import MultiDict
    form = MultiDict(parser.fields)
    form["_csrf_token"] = csrf
    res = client.post(f"/admin/radius/print-templates/{tid}/edit", data=form)
    assert res.status_code in {302, 303}
    after = _stored(tid)
    for key in ("username_x", "username_y", "password_x", "password_y", "qr_x", "qr_y"):
        assert after[key] == before[key], key
    keys = ("surface_opacity", "image_opacity", "pattern_color", "username_font_size",
            "password_font_size", "credential_label_font_size", "qr_size_pct",
            "card_width_mm", "card_height_mm", "brand_name", "footer_text",
            "watermark_opacity", "design_preset", "render_engine")
    for key in keys:
        assert after["layout_json"].get(key) == before["layout_json"].get(key), \
            (key, before["layout_json"].get(key), after["layout_json"].get(key))


def test_web_designer_keeps_explicit_values_exactly(client, app):
    tid = _create_template(client, top={"username_x": 16.13, "username_y": 20.07},
                           username_font_size=10.25, qr_size_pct=27.3)
    before = _stored(tid)
    csrf = _web_login(client)
    parser = _FormParser()
    parser.feed(client.get(f"/admin/radius/print-templates?edit_template={tid}").get_data(as_text=True))
    from werkzeug.datastructures import MultiDict
    form = MultiDict(parser.fields)
    form["_csrf_token"] = csrf
    client.post(f"/admin/radius/print-templates/{tid}/edit", data=form)
    after = _stored(tid)
    assert after["username_x"] == before["username_x"] == 16.13
    assert after["layout_json"]["username_font_size"] == 10.25
    assert after["layout_json"]["qr_size_pct"] == 27.3


# ══ 10 · validation ═══════════════════════════════════════════════════

@pytest.mark.parametrize("body,needle", [
    ({"name": "x" * 121}, "اسم القالب"),
    ({"layout": {"card_width_mm": 10}}, "عرض البطاقة"),
    ({"layout": {"card_width_mm": 1e9}}, "عرض البطاقة"),
    ({"layout": {"card_height_mm": 301}}, "ارتفاع البطاقة"),
    ({"layout": {"font_size_unit": "pt", "username_font_size": 37}}, "خطّ اسم المستخدم"),
    ({"layout": {"font_size_unit": "pt", "password_font_size": 120}}, "خطّ كلمة المرور"),
])
def test_template_bounds_are_422_in_arabic(client, body, needle):
    payload = {"name": f"f2-{uuid4().hex[:6]}", **body}
    payload["layout"] = {"render_engine": "ar_horizontal", "card_width_mm": 85.6,
                         "card_height_mm": 54, **(body.get("layout") or {})}
    res = client.post("/api/v1/print-templates", json=payload, headers=AUTH)
    assert res.status_code == 422, res.get_json()
    assert needle in res.get_json()["error"]["message"]


def test_bounds_edges_are_accepted(client):
    _create_template(client, name="x" * 120, username_font_size=36, card_width_mm=20,
                     card_height_mm=300)
    # legacy canvas units (no font_size_unit) keep their own range
    res = client.post("/api/v1/print-templates", json={
        "name": f"f2-{uuid4().hex[:6]}",
        "layout": {"card_width_mm": 85.6, "card_height_mm": 54, "username_font_size": 90}},
        headers=AUTH)
    assert res.status_code == 201


def test_quick_save_and_web_quick_screen_apply_the_same_bounds(client):
    res = client.post("/api/v1/print-templates/quick-save", json={
        "form": {**QUICK_FIELDS, "name": "x" * 121}}, headers=AUTH)
    assert res.status_code == 422 and "اسم القالب" in res.get_json()["error"]["message"]
    res = client.post("/api/v1/print-templates/quick-save", json={
        "form": {**QUICK_FIELDS, "name": "f2 font", "username_font_size": "40"}}, headers=AUTH)
    assert res.status_code == 422
    csrf = _web_login(client)
    res = client.post("/admin/radius/print-templates", data={
        **QUICK_FIELDS, "name": "f2 web font", "username_font_size": "40",
        "return_to": "quick", "_csrf_token": csrf})
    assert res.status_code in {302, 303}
    assert "/cards/print/quick" in res.headers["Location"]


def test_web_quick_screen_invalid_update_returns_to_the_quick_screen(client):
    tid = _create_template(client)
    csrf = _web_login(client)
    res = client.post(f"/admin/radius/print-templates/{tid}/edit", data={
        **QUICK_FIELDS, "name": _stored(tid)["name"], "qr_size_pct": "60",
        "return_to": "quick", "quick_batch_id": "", "_csrf_token": csrf})
    assert res.status_code in {302, 303}
    loc = res.headers["Location"]
    assert "/cards/print/quick" in loc and f"template_id={tid}" in loc
    page = client.get(loc)
    assert "حجم رمز QR" in page.get_data(as_text=True)  # the flash is shown there


def test_long_name_does_not_break_the_quick_page(client, app):
    from app.radius.db import connection
    tid = _create_template(client)
    connection.db().execute("UPDATE card_print_templates SET name = ? WHERE id = ?",
                            ("ن" * 10_004, tid))
    connection.db().commit()
    _web_login(client)
    html = client.get(f"/admin/radius/cards/print/quick?template_id={tid}").get_data(as_text=True)
    assert "max-width:100%" in html.replace(" ", "")
    options = re.findall(r"<option[^>]*>([^<]*)</option>", html)
    assert options and max(len(o) for o in options) <= 200


@pytest.mark.parametrize("settings", [
    {"print_fit_mode": "zoom"}, {"print_cut_lines": "maybe"}])
def test_unknown_enum_values_are_422_everywhere(client, settings):
    res = client.put("/api/v1/print-templates/last-settings", json=settings, headers=AUTH)
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert ("print_fit_mode" in msg) or ("print_cut_lines" in msg)
    res = client.post("/api/v1/print-templates/quick-save", json={
        "form": {**QUICK_FIELDS, "name": f"f2-{uuid4().hex[:6]}"},
        "print_settings": settings}, headers=AUTH)
    assert res.status_code == 422
    res = client.post("/api/v1/print-templates/preview.pdf", json={
        "form": QUICK_FIELDS, "print_settings": settings}, headers=AUTH)
    assert res.status_code == 422
    tid = _create_template(client)
    res = client.post(f"/api/v1/print-templates/{tid}/export-jobs",
                      json={"print_settings": settings}, headers=AUTH)
    assert res.status_code == 422
    for good in ("stretch", "uniform"):
        assert client.put("/api/v1/print-templates/last-settings",
                          json={"print_fit_mode": good}, headers=AUTH).status_code == 200
    for good in (True, "1", "0", "on", "false"):
        assert client.put("/api/v1/print-templates/last-settings",
                          json={"print_cut_lines": good}, headers=AUTH).status_code == 200


def _gif_data_url() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), (1, 2, 3)).save(buf, format="GIF")
    return "data:image/gif;base64," + base64.b64encode(buf.getvalue()).decode()


def test_gif_background_is_rejected_like_background_endpoint(client):
    gif = _gif_data_url()
    bg = client.post("/api/v1/print-templates/background", json={"data_url": gif}, headers=AUTH)
    assert bg.status_code == 422
    res = client.post("/api/v1/print-templates/quick-save", json={"form": {
        **QUICK_FIELDS, "name": f"f2-{uuid4().hex[:6]}", "background_style": "image",
        "background_image_data_url": gif}}, headers=AUTH)
    assert res.status_code == 422
    assert res.get_json()["error"]["message"] == bg.get_json()["error"]["message"]
    res = client.post("/api/v1/print-templates", json={
        "name": f"f2-{uuid4().hex[:6]}", "layout": {"background_image_data_url": gif}},
        headers=AUTH)
    assert res.status_code == 422


@pytest.mark.parametrize("bad", ['"NaN"', '"Infinity"', "Infinity", "NaN", '"-inf"'])
def test_nan_margin_in_preview_names_the_margin(client, bad):
    # raw body: the test client's JSON encoder would turn a float inf into null
    body = '{"form": %s, "print_settings": {"print_margin_mm": %s}}' % (
        json.dumps(QUICK_FIELDS), bad)
    res = client.post("/api/v1/print-templates/preview.pdf", data=body,
                      content_type="application/json", headers=AUTH)
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert "معرّف القالب" not in msg and ("الهامش" in msg or "print_margin" in msg)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/print-jobs/abc"),
    ("get", "/api/v1/print-jobs/abc/download"),
    ("post", "/api/v1/print-jobs/abc/cancel"),
    ("delete", "/api/v1/print-jobs/abc"),
    ("get", "/api/v1/print-jobs/-1"),
    ("get", "/api/v1/print-jobs/999999"),
    ("post", "/api/v1/print-jobs/999999/cancel"),
])
def test_bad_or_unknown_job_ids_are_json_404(client, method, path):
    res = getattr(client, method)(path, headers=AUTH)
    assert res.status_code == 404
    body = res.get_json()
    assert body and body["error"]["code"] == "not_found"
    assert "مهمة الطباعة" in body["error"]["message"]


@pytest.mark.parametrize("field,value", [
    ("qr_size_pct", "60"), ("username_font_size", "121"), ("password_font_size", "200"),
    ("card_width_mm", "5")])
def test_web_live_preview_out_of_range_is_422_json(client, field, value):
    tid = _create_template(client)
    csrf = _web_login(client)
    for extra in ({}, {"quick_template_id": str(tid)}):
        res = client.post("/admin/radius/print-templates/designer-svg",
                          data={**QUICK_FIELDS, field: value, "_csrf_token": csrf, **extra})
        assert res.status_code == 422, (field, extra, res.status_code)
        body = res.get_json()
        assert body and body["ok"] is False and re.search(r"[؀-ۿ]", body["error"]["message"])


def test_web_pages_show_the_live_preview_error(client):
    tid = _create_template(client)
    _web_login(client)
    quick = client.get(f"/admin/radius/cards/print/quick?template_id={tid}").get_data(as_text=True)
    assert "data-qk-preview-error" in quick and "showPreviewError" in quick
    designer = client.get(f"/admin/radius/print-templates?edit_template={tid}").get_data(as_text=True)
    assert "serverMessage" in designer


def _ink(p):
    from app.radius.services.card_renderer import _label_ink_extent, _value_ink
    h = p["height"]
    lm = p["y"] + h * p.get("label_mid_frac", 0.36)
    vm = p["y"] + h * p.get("value_mid_frac", 0.72)
    l_up, l_down, _ = _label_ink_extent(p["label"], 900)
    v_up, v_down = _value_ink(p["value"])
    ls, vs = p["label_font_size"], p["value_font_size"]
    return {"label_top": lm + (0.26 - l_up) * ls, "label_bottom": lm + (0.26 + l_down) * ls,
            "value_top": vm + (0.26 - v_up) * vs, "value_bottom": vm + (0.26 + v_down) * vs}


@pytest.mark.parametrize("layout", [
    {"render_engine": "ar_vertical", "card_width_mm": 54, "card_height_mm": 85.6},
    {"render_engine": "en_vertical", "card_width_mm": 54, "card_height_mm": 85.6},
    {"username_font_size": 36, "password_font_size": 36},
    {"render_engine": "ar_vertical", "card_width_mm": 54, "card_height_mm": 85.6,
     "username_font_size": 30, "password_font_size": 36, "credential_label_font_size": 14},
    # the owner's template 1 (legacy canvas units, dragged + huge fonts)
    {"show_qr": 0, "font_size_unit": "", "username_font_size": 90, "password_font_size": 118,
     "credential_label_font_size": 55},
])
def test_value_is_never_drawn_over_its_label(app, layout):
    top = {"username_x": 8, "username_y": 0.1, "password_x": 32, "password_y": 36} \
        if layout.get("font_size_unit") == "" else None
    model = _model(top=top, **layout)
    for p in [e for e in model["elements"] if e["kind"] == "pill"]:
        ink = _ink(p)
        assert ink["value_top"] >= ink["label_bottom"] - 0.5, (p["id"], ink)
        assert ink["label_top"] >= p["y"] - 0.5 and ink["value_bottom"] <= p["y"] + p["height"] + 0.5


def test_pill_anchors_reach_both_adapters(app):
    from app.radius.services.card_renderer import render_card_svg
    model = _model(render_engine="ar_vertical", card_width_mm=54, card_height_mm=85.6)
    user = next(e for e in model["elements"] if e["id"] == "user")
    assert "label_mid_frac" in user
    svg = render_card_svg(model, mask_password=False)
    label_y = user["y"] + user["height"] * user["label_mid_frac"]
    assert f'y="{label_y:.1f}"' in svg


# ══ 11 · small items ══════════════════════════════════════════════════

def test_quick_save_does_not_invent_a_qr_login_link(client, app):
    other = _create_template(client, hotspot_login_url="http://10.5.50.1/login")
    tid = _create_template(client)
    assert not _stored(tid)["layout_json"].get("hotspot_login_url")
    csrf = _web_login(client)
    page = client.get(f"/admin/radius/cards/print/quick?template_id={tid}").get_data(as_text=True)
    m = re.search(r'<input name="hotspot_login_url"[^>]*value="([^"]*)"', page)
    assert m and m.group(1) == ""                       # not pre-filled…
    assert "10.5.50.1/login" in page                    # …only suggested
    parser_fields = {**QUICK_FIELDS, "name": _stored(tid)["name"], "return_to": "quick",
                     "_csrf_token": csrf}
    client.post(f"/admin/radius/print-templates/{tid}/edit", data=parser_fields)
    assert not _stored(tid)["layout_json"].get("hotspot_login_url")
    res = client.post("/api/v1/print-templates/quick-save", json={
        "template_id": tid, "form": {k: v for k, v in QUICK_FIELDS.items()
                                     if k != "hotspot_login_url"} | {"name": _stored(tid)["name"]}},
        headers=AUTH)
    assert res.status_code == 200
    assert not _stored(tid)["layout_json"].get("hotspot_login_url")
    assert _stored(other)["layout_json"]["hotspot_login_url"] == "http://10.5.50.1/login"


def test_quick_print_opens_on_this_admins_last_template(client, app):
    a = _create_template(client, name="f2 admin A")
    b = _create_template(client, name="f2 admin B")
    newest = _create_template(client, name="f2 newest")
    csrf = _web_login(client, "f2p_alice")
    # alice saves template A from the quick screen
    client.post(f"/admin/radius/print-templates/{a}/edit", data={
        **QUICK_FIELDS, "name": "f2 admin A", "return_to": "quick", "_csrf_token": csrf})
    html = client.get("/admin/radius/cards/print/quick").get_data(as_text=True)
    assert re.search(rf'<option value="{a}"[^>]*selected', html)
    client.get("/admin/radius/logout")
    # bob opens B once; alice's memory is untouched
    other = app.test_client()
    _web_login(other, "f2p_bob")
    other.get(f"/admin/radius/cards/print/quick?template_id={b}")
    bob_html = other.get("/admin/radius/cards/print/quick").get_data(as_text=True)
    assert re.search(rf'<option value="{b}"[^>]*selected', bob_html)
    alice = app.test_client()
    _web_login(alice, "f2p_alice")
    html = alice.get("/admin/radius/cards/print/quick").get_data(as_text=True)
    assert re.search(rf'<option value="{a}"[^>]*selected', html)
    assert not re.search(rf'<option value="{newest}"[^>]*selected', html)


def test_api_last_settings_reports_the_callers_last_template(client):
    a = _create_template(client, name="f2 api A")
    _create_template(client, name="f2 api newer")
    data = client.get("/api/v1/print-templates/last-settings", headers=AUTH).get_json()["data"]
    assert "last_template_id" in data and "default_template_id" in data
    res = client.post("/api/v1/print-templates/quick-save", json={
        "template_id": a, "form": {**QUICK_FIELDS, "name": "f2 api A"}}, headers=AUTH)
    assert res.status_code == 200
    data = client.get("/api/v1/print-templates/last-settings", headers=AUTH).get_json()["data"]
    assert data["last_template_id"] == a


def test_duplicate_name_is_409_duplicate_name(client):
    _create_template(client, name="قالب سريع")
    res = client.post("/api/v1/print-templates/quick-save", json={
        "form": {**QUICK_FIELDS, "name": "قالب سريع"}}, headers=AUTH)
    assert res.status_code == 409
    err = res.get_json()["error"]
    assert err["code"] == "duplicate_name" and "يوجد قالب طباعة بهذا الاسم" in err["message"]
    res = client.post("/api/v1/print-templates", json={"name": "قالب سريع"}, headers=AUTH)
    assert res.status_code == 409 and res.get_json()["error"]["code"] == "duplicate_name"
    other = _create_template(client)
    res = client.patch(f"/api/v1/print-templates/{other}", json={"name": "قالب سريع"},
                       headers=AUTH)
    assert res.status_code == 409 and res.get_json()["error"]["code"] == "duplicate_name"


def test_web_duplicate_name_shows_the_flash(client):
    _create_template(client, name="f2 dup web")
    csrf = _web_login(client)
    res = client.post("/admin/radius/print-templates", data={
        **QUICK_FIELDS, "name": "f2 dup web", "return_to": "quick", "_csrf_token": csrf},
        follow_redirects=True)
    assert "يوجد قالب طباعة بهذا الاسم" in res.get_data(as_text=True)


def test_footer_is_cut_at_a_word_with_an_ellipsis(app):
    from app.radius.services.card_renderer import _fit_footer_text
    text = "احتفظ ببيانات الدخول حتى انتهاء الصلاحية"
    size, line = _fit_footer_text(text, 27.0, 150.0, direction="rtl")
    assert line != text and line.endswith("…")
    words = line[:-1].split()
    assert words == text.split()[:len(words)]
    # beside a big QR on a real card
    model = _model(qr_size_pct=48, render_engine="ar_vertical", card_width_mm=54,
                   card_height_mm=85.6, top={"qr_x": 2, "qr_y": 60})
    footer = next(e for e in model["elements"] if e.get("id") == "footer")
    if footer["text"] != text:
        assert footer["text"].endswith("…")
        assert footer["text"][:-1].split() == text.split()[:len(footer["text"][:-1].split())]


@pytest.mark.parametrize("vertical", [False, True])
def test_the_reported_auto_size_sent_back_prints_like_auto(client, vertical):
    base = {**QUICK_FIELDS, **(VERTICAL if vertical else {})}
    auto = client.post("/api/v1/print-templates/quick-elements", json={"form": base},
                       headers=AUTH).get_json()["data"]["elements"]
    u = auto["username"]
    assert u["font_auto"] is True
    mm_to_pt = 72 / 25.4
    app_shown = round(u["h"] * 0.52 * mm_to_pt * 2) / 2   # the app's autoFontPt()
    server_shown = round(u["font_pt"] * 2) / 2
    for sent in (u["font_pt"], server_shown, app_shown):
        again = client.post("/api/v1/print-templates/quick-elements", json={"form": {
            **base, "username_font_size": str(sent)}}, headers=AUTH).get_json()["data"]["elements"]
        assert again["username"]["h"] == u["h"], sent
        assert again["username"]["font_pt"] == u["font_pt"], sent
    bigger = client.post("/api/v1/print-templates/quick-elements", json={"form": {
        **base, "username_font_size": str(server_shown + 1.5)}}, headers=AUTH).get_json()["data"]["elements"]
    assert bigger["username"]["font_pt"] > u["font_pt"]
