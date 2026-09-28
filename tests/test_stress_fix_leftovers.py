"""Stress-campaign LEFTOVER wave (2026-09-28) — regression tests.

One test (or a few) per leftover finding: payments dry run, the effective
system currency, store wallets (active + CORS + admin paths), the subscriber
password minimum, the card checker (username wins), general-adjustments dry
run, device-health 404s, web /online paging, the batch plan re-derivation,
batch lookup by code, next_batch_id + digits default and the self-updater.
API and web route are both covered where they share the path.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest

TOKEN = "leftovers-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
EXPIRE = datetime(2030, 1, 1, 12, 0, 0)
FETCH = {"X-Requested-With": "fetch", "Accept": "application/json"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "leftovers.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "leftovers-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_left")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
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


# ─────────────── helpers ───────────────

def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_left", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(name=None, *, price=30.0, days=30, currency="ILS") -> int:
    now = datetime.utcnow().isoformat()
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        (name or "p_" + uuid4().hex[:6], days * 1440, days, price, currency, now, now))
    return int(cur.lastrowid)


def _sub(username=None, *, plan_id, balance=0.0, password="secret", expire_at=EXPIRE):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "u_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password=password, plan_id=plan_id,
        full_name="Left User", mobile="0599000000", status="enabled", expire_at=expire_at))
    _db().execute("UPDATE subscribers SET balance=? WHERE tenant_id=1 AND username=?",
                  (float(balance), username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _count(sql, *args) -> int:
    return int(_db().execute(sql, args).fetchone()[0] or 0)


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status, code=None):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is False, body
    if code:
        assert body["error"]["code"] == code, body
    return body["error"]


def _generate(client, pid, **extra):
    body = {"plan_id": pid, "count": 1, "username_prefix": "lf" + uuid4().hex[:4]}
    body.update(extra)
    return _data(client.post("/api/v1/cards/generate", json=body, headers=AUTH), 201)


# ─────────────── 1. POST /payments dry_run writes nothing ───────────────

def test_payments_dry_run_is_a_real_preview(client):
    pid = _plan(price=30, days=30)
    s = _sub(plan_id=pid, balance=0)
    before_pay = _count("SELECT COUNT(*) FROM payment_transactions")
    before_ledger = _count("SELECT COUNT(*) FROM accounting_ledger_entries")
    d = _data(client.post("/api/v1/payments", headers=AUTH, json={
        "username": s.username, "amount": 15, "apply_to_radius": True,
        "dry_run": True, "notes": "preview"}), 200)
    assert d["dry_run"] is True
    p = d["payment"]
    assert p["id"] is None and p["status"] == "preview"
    assert p["earned_minutes"] == 15 * 24 * 60  # half the 30-day plan
    assert p["activation_result"]["applied_to_radius"] is False
    assert _count("SELECT COUNT(*) FROM payment_transactions") == before_pay
    assert _count("SELECT COUNT(*) FROM accounting_ledger_entries") == before_ledger
    assert _get(s.username).expire_at == EXPIRE
    # a real payment afterwards still records
    real = _data(client.post("/api/v1/payments", headers=AUTH, json={
        "username": s.username, "amount": 15}), 201)
    assert real["payment"]["id"]
    assert _count("SELECT COUNT(*) FROM payment_transactions") == before_pay + 1


def test_dry_run_does_not_consume_the_idempotency_key(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    hdr = dict(AUTH, **{"Idempotency-Key": "k-" + uuid4().hex})
    _data(client.post("/api/v1/payments", headers=hdr, json={
        "username": s.username, "amount": 5, "dry_run": True}), 200)
    real = client.post("/api/v1/payments", headers=hdr, json={
        "username": s.username, "amount": 5})
    assert real.status_code == 201 and real.headers.get("Idempotent-Replay") is None
    assert real.get_json()["data"]["payment"]["id"]


def test_web_payment_dry_run_writes_nothing(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid)
    before = _count("SELECT COUNT(*) FROM payment_transactions")
    res = client.post(f"/admin/radius/users/{s.username}/payments", data={
        "_csrf_token": csrf, "amount": "10", "method": "cash",
        "apply_to_radius": "1", "dry_run": "1"}, headers=FETCH)
    body = res.get_json()
    assert res.status_code == 200 and body["ok"] is True, body
    assert "معاينة" in body["message"]
    assert _count("SELECT COUNT(*) FROM payment_transactions") == before
    assert _get(s.username).expire_at == EXPIRE


# ─────────────── 2. effective system currency ───────────────

def test_settings_expose_the_effective_currency_when_unset(client):
    from app.radius.core.system_config import default_currency
    d = _data(client.get("/api/v1/settings", headers=AUTH))
    assert d["settings"]["billing.currency"] == "ILS"
    assert d["system"]["currency"] == "ILS" == default_currency()
    item = next(i for i in d["items"] if i["key"] == "billing.currency")
    assert item["value"] == "ILS" and item["default"] == "ILS"


def test_settings_currency_follows_the_stored_value_and_blank(client):
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "billing.currency", "usd", by=0)
    d = _data(client.get("/api/v1/settings", headers=AUTH))
    assert d["settings"]["billing.currency"] == "USD"
    assert d["system"]["currency"] == "USD" and d["system"]["currency_symbol"] == "$"
    tenants_repo.set_setting(1, "billing.currency", "", by=0)
    d = _data(client.get("/api/v1/settings", headers=AUTH))
    assert d["settings"]["billing.currency"] == "ILS"


def test_admin_me_carries_the_effective_currency(client):
    res = client.post("/api/admin/login",
                      json={"username": "owner_left", "password": "owner-pass"})
    token = res.get_json()["data"]["token"]
    d = _data(client.get("/api/admin/me", headers={"Authorization": "Bearer " + token}))
    assert d["system"]["currency"] == "ILS"
    assert d["system"]["currency_symbol"] == "₪"


def test_web_settings_default_currency_is_ils():
    from app.radius.routes.settings import _SETTINGS_KEYS
    assert {k: d for k, _l, d in _SETTINGS_KEYS}["billing.currency"] == "ILS"


# ─────────────── 3. store wallets: active + CORS + admin paths ───────────────

def test_wallet_create_honours_active(client):
    d = _data(client.post("/api/v1/store/admin/payment-methods", headers=AUTH,
                          json={"method": "bank", "label": "Bank A", "active": 0}), 201)
    assert d["payment_method"]["active"] in (0, False)
    d = _data(client.post("/api/v1/store/admin/payment-methods", headers=AUTH,
                          json={"method": "bank", "label": "Bank B"}), 201)
    assert d["payment_method"]["active"] in (1, True)
    _err(client.post("/api/v1/store/admin/payment-methods", headers=AUTH,
                     json={"method": "bank", "label": "Bank C", "active": "maybe"}), 422)


def test_web_wallet_create_honours_active(client):
    csrf = _web_login(client)
    client.post("/admin/radius/store-support/payment-methods", data={
        "_csrf_token": csrf, "method": "bank", "label": "Web Off", "active": "0"})
    row = _db().execute(
        "SELECT active FROM store_payment_methods WHERE label='Web Off'").fetchone()
    assert row is not None and int(row["active"]) == 0


def test_store_cors_preflight_allows_patch_and_delete(client):
    res = client.open("/api/v1/store/admin/payment-methods/1", method="OPTIONS",
                      headers={"Origin": "http://localhost:5000",
                               "Access-Control-Request-Method": "DELETE"})
    assert res.status_code == 204
    allowed = res.headers.get("Access-Control-Allow-Methods", "")
    for m in ("PATCH", "DELETE", "PUT"):
        assert m in allowed
    d = _data(client.post("/api/v1/store/admin/payment-methods", headers=AUTH,
                          json={"method": "bank", "label": "CORS"}), 201)
    mid = d["payment_method"]["id"]
    res = client.patch(f"/api/v1/store/admin/payment-methods/{mid}", headers=AUTH,
                       json={"active": False})
    assert res.status_code == 200, res.get_json()
    assert "DELETE" in res.headers["Access-Control-Allow-Methods"]
    assert res.get_json()["data"]["payment_method"]["active"] in (0, False)


def test_store_admin_paths_do_not_need_the_customer_store_key(client):
    from app.radius.services.store_key import get_or_create_store_key
    get_or_create_store_key(1)  # the store was published
    _data(client.get("/api/v1/store/admin/payment-methods", headers=AUTH))
    # customer endpoints stay gated by the key
    res = client.get("/api/v1/store/payment-methods")
    assert res.status_code == 403


# ─────────────── 4. subscriber password minimum (4) ───────────────

def test_api_create_and_reset_refuse_short_passwords(client):
    pid = _plan()
    _err(client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "shortpw1", "password": "abc", "plan_id": pid}), 422)
    assert _get("shortpw1") is None
    _data(client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "shortpw2", "password": "abcd", "plan_id": pid}), 201)
    _err(client.post("/api/v1/accounts/shortpw2/reset_password", headers=AUTH,
                     json={"new_password": "12"}), 422)
    _data(client.post("/api/v1/accounts/shortpw2/reset_password", headers=AUTH,
                      json={"new_password": "1234"}))


def test_web_create_refuses_short_password_and_legacy_edit_still_saves(client):
    csrf = _web_login(client)
    pid = _plan()
    form = {"_csrf_token": csrf, "full_name": "W", "plan_id": str(pid),
            "status": "enabled", "user_type": "subscriber"}
    res = client.post("/admin/radius/users",
                      data=dict(form, username="webshort", password="ab"))
    assert res.status_code == 400
    assert _get("webshort") is None
    # a migrated account with a 3-char password can still be edited as is …
    legacy = _sub(plan_id=pid, password="abc")
    res = client.post(f"/admin/radius/users/{legacy.username}",
                      data=dict(form, password="abc", full_name="Renamed"))
    assert res.status_code in (302, 303), res.status_code
    assert _get(legacy.username).full_name == "Renamed"
    # … but a CHANGED password must be ≥ 4
    res = client.post(f"/admin/radius/users/{legacy.username}",
                      data=dict(form, password="xy", full_name="Renamed"))
    assert res.status_code == 400
    assert _get(legacy.username).password == "abc"


# ─────────────── 5. card checker: the username wins ───────────────

def test_card_checker_username_wins_and_id_only_when_explicit(client):
    pid = _plan()
    a = _generate(client, pid)["cards"][0]
    b = _generate(client, pid)["cards"][0]
    # card A's username is exactly card B's numeric id
    _db().execute("UPDATE cards SET username=? WHERE id=?", (str(b["id"]), a["id"]))
    got = _data(client.get("/api/v1/cards/check", headers=AUTH,
                           query_string={"query": str(b["id"])}))["card"]
    assert got["exists"] and got["id"] == a["id"]
    # the id only when asked for explicitly
    got = _data(client.get("/api/v1/cards/check", headers=AUTH,
                           query_string={"query": f"id:{b['id']}"}))["card"]
    assert got["exists"] and got["id"] == b["id"]
    got = _data(client.get("/api/v1/cards/check", headers=AUTH,
                           query_string={"card_id": b["id"]}))["card"]
    assert got["exists"] and got["id"] == b["id"]
    _err(client.get("/api/v1/cards/check", headers=AUTH,
                    query_string={"card_id": "x1"}), 422)


def test_card_checker_short_number_never_opens_another_card(client):
    pid = _plan()
    c = _generate(client, pid)["cards"][0]
    got = _data(client.get("/api/v1/cards/check", headers=AUTH,
                           query_string={"query": str(c["id"])}))["card"]
    assert got["exists"] is False  # was: opened card id N
    # the printed username typed in upper case still resolves
    got = _data(client.get("/api/v1/cards/check", headers=AUTH,
                           query_string={"query": c["username"].upper()}))["card"]
    assert got["exists"] and got["id"] == c["id"]


def test_card_checker_reads_arabic_indic_digits():
    from app.radius.services.card_checker import parse_checker_query
    assert parse_checker_query("١٢٣٤") == ("1234", None)
    assert parse_checker_query("ID:٨٨") == ("ID:88", 88)
    with pytest.raises(ValueError):
        parse_checker_query("id:abc")


def test_web_checker_lookup_username_wins(client):
    _web_login(client)
    pid = _plan()
    c = _generate(client, pid)["cards"][0]
    res = client.get("/admin/radius/cards/checker/api/lookup",
                     query_string={"q": str(c["id"])})
    assert res.get_json()["result"]["exists"] is False
    res = client.get("/admin/radius/cards/checker/api/lookup",
                     query_string={"card_id": str(c["id"])})
    assert res.get_json()["result"]["id"] == c["id"]


# ─────────────── 6a. general adjustments: a real dry run ───────────────

def test_general_adjustments_dry_run_predicts_the_real_run(client):
    pid = _plan()
    a = _sub(plan_id=pid, expire_at=datetime(2031, 1, 1))
    d = _data(client.post("/api/v1/tools/general-adjustments", headers=AUTH, json={
        "action": "extend", "minutes": 90, "dry_run": True,
        "usernames": [a.username, "ghost_user", 12345]}))
    assert d["dry_run"] is True
    assert d["targets"] == [a.username]
    assert set(d["not_found"]) == {"ghost_user", "12345"}
    assert d["would_succeed"] == 1 and d["would_fail"] == 2
    item = next(i for i in d["items"] if i["username"] == a.username)
    assert item["new_expire_at"].startswith("2031-01-01T01:30")
    assert _get(a.username).expire_at == datetime(2031, 1, 1)  # nothing written
    # the real run matches the preview, with Arabic per-user reasons
    d = _data(client.post("/api/v1/tools/general-adjustments", headers=AUTH, json={
        "action": "extend", "minutes": 90, "usernames": [a.username, "ghost_user"]}))
    assert d["success"] == 1 and d["failed"] == 1
    bad = next(i for i in d["items"] if not i["ok"])
    assert bad["error"] == "المستخدم غير موجود."
    assert _get(a.username).expire_at == datetime(2031, 1, 1, 1, 30)


@pytest.mark.parametrize("body", [
    {"action": "extend", "usernames": ["x"], "dry_run": True},              # no minutes
    {"action": "extend", "minutes": "abc", "usernames": ["x"]},
    {"action": "reset_password", "usernames": ["x"], "dry_run": True},     # no password
    {"action": "reset_password", "new_password": "12", "usernames": ["x"]},
    {"action": "explode", "usernames": ["x"]},
    {"action": "enable", "usernames": []},
])
def test_general_adjustments_bad_request_is_422_before_anything(client, body):
    _err(client.post("/api/v1/tools/general-adjustments", headers=AUTH, json=body), 422)


def test_web_general_adjustments_preview_writes_nothing(client):
    csrf = _web_login(client)
    pid = _plan()
    a = _sub(plan_id=pid)
    res = client.post("/admin/radius/tools/general_adjustments", data={
        "_csrf_token": csrf, "action": "disable", "dry_run": "1",
        "usernames": f"{a.username}\nnobody_here"})
    html = res.get_data(as_text=True)
    assert res.status_code == 200 and "data-ga-preview-summary" in html
    assert "nobody_here" in html
    assert _get(a.username).status == "enabled"
    res = client.post("/admin/radius/tools/general_adjustments", data={
        "_csrf_token": csrf, "action": "extend", "minutes": "0", "usernames": a.username})
    assert res.status_code == 400


# ─────────────── 6b. device-health unknown ids → 404 ───────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/device-health/devices/999999/events"),
    ("get", "/api/v1/device-health/devices/999999/alerts"),
    ("post", "/api/v1/device-health/devices/999999/enable"),
    ("post", "/api/v1/device-health/devices/999999/disable"),
    ("delete", "/api/v1/device-health/devices/999999"),
    ("patch", "/api/v1/device-health/devices/999999"),
    ("post", "/api/v1/device-health/devices/999999/test-ping"),
])
def test_device_health_unknown_device_is_404(client, method, path):
    kw = {"json": {}} if method in ("post", "patch") else {}
    res = getattr(client, method)(path, headers=AUTH, **kw)
    _err(res, 404, "not_found")


def test_web_device_health_unknown_device_is_404(client):
    _web_login(client)
    for path in ("/admin/radius/device-health/api/devices/999999/events",
                 "/admin/radius/device-health/api/devices/999999/alerts"):
        res = client.get(path)
        assert res.status_code == 404 and res.get_json()["ok"] is False


# ─────────────── 6c. web /online: real server-side paging ───────────────

def _seed_open_sessions(n: int, prefix: str = "on"):
    from datetime import timedelta
    from app.radius.db.connection import transaction
    base = datetime.utcnow() - timedelta(hours=1)
    pid = _plan()
    now = datetime.utcnow().isoformat()
    with transaction() as c:
        subs, accts = [], []
        for i in range(n):
            user = f"{prefix}{i:04d}"
            st = (base + timedelta(seconds=i)).isoformat() + "Z"
            subs.append((user, pid, now))
            accts.append((f"sid-{user}", f"uid-{user}", user, "10.20.30.1",
                          f"10.9.{i // 250}.{i % 250 + 1}", st, st))
        c.executemany("INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                      "status, created_at) VALUES (1, ?, 'xxxx', ?, 'enabled', ?)", subs)
        c.executemany(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
            "nasipaddress, framedipaddress, acctstarttime, acctupdatetime, "
            "acctinputoctets, acctoutputoctets, acctstoptime) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1, 2, NULL)", accts)


def test_web_online_pages_every_open_session(client):
    _web_login(client)
    _seed_open_sessions(230)
    res = client.get("/admin/radius/online?per_page=100")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert html.count('class="online-row') == 100
    assert "230 جلسة" in html and "data-online-pager" in html
    res = client.get("/admin/radius/online?per_page=100&page=3")
    assert res.get_data(as_text=True).count('class="online-row') == 30
    # the search runs over ALL sessions, not the first page
    res = client.get("/admin/radius/online?per_page=100&q=on0229")
    html = res.get_data(as_text=True)
    assert html.count('class="online-row') == 1 and "on0229" in html


# ─────────────── 7a. PDF: form XObjects carry their own resources ───────────────

def test_pdf_form_carries_extgstate_and_shading_resources():
    import io
    from reportlab.pdfbase import pdfdoc
    from reportlab.pdfgen import canvas
    from app.radius.services.card_renderer import _end_form_with_resources
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(300, 200))
    pdf.beginForm("f0", 0, 0, 100, 60)
    pdf.setFillAlpha(0.5)
    pdf.rect(5, 5, 50, 30, fill=1)
    pdf.linearGradient(0, 0, 100, 0, [(1, 0, 0), (0, 0, 1)], extend=False)
    _end_form_with_resources(pdf, "f0")
    form = pdf._doc.idToObject[pdfdoc.xObjectName("f0")]
    assert form.Resources is not None and form.Resources.ExtGState
    assert form.Resources.Shading
    pdf.doForm("f0")
    pdf.showPage()
    pdf.save()
    fitz = pytest.importorskip("fitz")
    fitz.TOOLS.mupdf_warnings()
    doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
    for page in doc:
        page.get_pixmap()
    assert "cannot find" not in fitz.TOOLS.mupdf_warnings()


def _template(client) -> int:
    res = client.post("/api/v1/print-templates", headers=AUTH, json={
        "name": "lf-" + uuid4().hex[:6],
        "layout": {"render_engine": "ar_vertical", "card_width_mm": 54,
                   "card_height_mm": 85.6, "design_preset": "modern",
                   "brand_name": "Left", "surface_opacity": 0.8, "show_qr": True,
                   "hotspot_login_url": "http://hotspot.local/login"}})
    assert res.status_code == 201, res.get_json()
    return int(res.get_json()["data"]["template"]["id"])


def test_card_and_page_preview_pdfs_have_no_missing_resources(client):
    fitz = pytest.importorskip("fitz")
    tid = _template(client)
    for mode in ("card", "page"):
        res = client.post("/api/v1/print-templates/preview.pdf", headers=AUTH,
                          json={"template_id": tid, "mode": mode})
        assert res.status_code == 200, res.get_data(as_text=True)[:300]
        fitz.TOOLS.mupdf_warnings()
        doc = fitz.open(stream=res.data, filetype="pdf")
        for page in doc:
            page.get_pixmap()
        assert "cannot find" not in fitz.TOOLS.mupdf_warnings(), mode


# ─────────────── 7b. QR stays square when the card is stretched ───────────────

def test_stretched_placement_draws_a_square_qr():
    import io
    from reportlab.pdfgen import canvas
    from app.radius.services.card_renderer import place_card_qr

    class Rec(canvas.Canvas):
        def scale(self, x, y):
            self.scales.append((x, y))
            super().scale(x, y)

    pdf = Rec(io.BytesIO(), pagesize=(243, 153))
    pdf.scales = []
    model = {"canvas": {"width": 500, "height": 300},
             "elements": [{"kind": "qr", "id": "qr", "x": 380, "y": 170, "size": 100,
                           "payload": "http://hotspot.local/login?u=1", "style": "boxed"}]}
    # a 1.667 canvas onto a 1.585 card (85.6 x 54 mm): sx != sy
    place_card_qr(pdf, model, slot_x=0, slot_y=0, slot_width=242.6, slot_height=153.1,
                  stretch=True)
    assert pdf.scales and all(abs(x - y) < 1e-9 for x, y in pdf.scales)


def test_card_mode_preview_draws_the_qr_outside_the_stretched_form(client, monkeypatch):
    import app.radius.services.card_renderer as cr
    calls = []
    real = cr.place_card_qr

    def spy(*a, **k):
        calls.append(k)
        return real(*a, **k)

    monkeypatch.setattr(cr, "place_card_qr", spy)
    tid = _template(client)
    res = client.post("/api/v1/print-templates/preview.pdf", headers=AUTH,
                      json={"template_id": tid, "mode": "card"})
    assert res.status_code == 200
    assert len(calls) == 1 and calls[0]["stretch"] is True


# ─────────────── 7c. plan change re-derives an INHERITED window ───────────────

def _batch_row(bid):
    return dict(_db().execute("SELECT * FROM card_batches WHERE id=?", (bid,)).fetchone())


def test_plan_change_rederives_the_inherited_window(client):
    day = _plan(days=1)
    now = datetime.utcnow().isoformat()
    hour = int(_db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, price, "
        "currency, enabled, created_at, updated_at) VALUES(1,?,60,0,1,'ILS',1,?,?)",
        ("hour_" + uuid4().hex[:4], now, now)).lastrowid)
    gen = _generate(client, day, count=2)
    bid = gen["batch"]["id"]
    row = _batch_row(bid)
    assert (row["time_value"], row["time_unit"]) == (1, "days")  # inherited
    # a disabled (revoked) unused card must follow the batch too
    _db().execute("UPDATE cards SET revoked=1 WHERE id=?", (gen["cards"][0]["id"],))
    _data(client.patch(f"/api/v1/cards/batches/{bid}", headers=AUTH, json={"plan_id": hour}))
    row = _batch_row(bid)
    assert (row["plan_id"], row["time_value"], row["time_unit"]) == (hour, 1, "hours")
    plans = {r["plan_id"] for r in _db().execute(
        "SELECT plan_id FROM cards WHERE batch_id=?", (bid,)).fetchall()}
    assert plans == {hour}


def test_plan_change_keeps_an_explicit_window(client):
    day = _plan(days=1)
    other = _plan(days=30)
    bid = _generate(client, day, time_value=5, time_unit="hours")["batch"]["id"]
    _data(client.patch(f"/api/v1/cards/batches/{bid}", headers=AUTH, json={"plan_id": other}))
    row = _batch_row(bid)
    assert (row["plan_id"], row["time_value"], row["time_unit"]) == (other, 5, "hours")
    # the same window sent with the plan (the web form sends every field) still
    # counts as «not changed» → an inherited window follows the new plan
    bid2 = _generate(client, day)["batch"]["id"]
    _data(client.patch(f"/api/v1/cards/batches/{bid2}", headers=AUTH,
                       json={"plan_id": other, "time_value": 1, "time_unit": "days"}))
    assert (_batch_row(bid2)["time_value"], _batch_row(bid2)["time_unit"]) == (30, "days")


# ─────────────── 8. batch lookup by its visible code ───────────────

def test_batches_lookup_by_code_and_distributor_link_by_code(client):
    pid = _plan()
    batch = _generate(client, pid)["batch"]
    code = batch["batch_code"]
    d = _data(client.get("/api/v1/cards/batches", headers=AUTH,
                         query_string={"code": code.lower()}))
    assert [i["id"] for i in d["items"]] == [batch["id"]]
    d = _data(client.get("/api/v1/cards/batches", headers=AUTH,
                         query_string={"code": "B-00000000-9999"}))
    assert d["items"] == [] and d["total"] == 0
    dist = _data(client.post("/api/v1/distributors", headers=AUTH,
                             json={"name": "d_" + uuid4().hex[:6]}), 201)
    dist_id = (dist.get("distributor") or dist)["id"]
    d = _data(client.post(f"/api/v1/distributors/{dist_id}/assign-batch", headers=AUTH,
                          json={"batch_code": code}))
    assert int(d["assignment"]["batch_id"]) == int(batch["id"])
    _err(client.post(f"/api/v1/distributors/{dist_id}/assign-batch", headers=AUTH,
                     json={"batch_id": "B-00000000-9999"}), 404)


def test_batch_search_treats_percent_literally(client):
    pid = _plan()
    _generate(client, pid)
    d = _data(client.get("/api/v1/cards/batches", headers=AUTH, query_string={"q": "%"}))
    assert d["total"] == 0


# ─────────────── 9. next_batch_id + digits default ───────────────

def test_batches_list_exposes_next_batch_id(client):
    pid = _plan()
    _generate(client, pid)
    seq = _db().execute("SELECT seq FROM sqlite_sequence WHERE name='card_batches'").fetchone()
    d = _data(client.get("/api/v1/cards/batches", headers=AUTH))
    assert d["meta"]["next_batch_id"] == int(seq["seq"]) + 1 == d["next_batch_id"]
    nxt = d["next_batch_id"]
    gen = _generate(client, pid)
    assert gen["batch"]["id"] == nxt


def test_generate_defaults_to_digits_passwords(client):
    pid = _plan()
    gen = _generate(client, pid, count=5, password_length=6)
    assert gen["batch"]["password_generation_type"] == "digits"
    assert all(c["password"].isdigit() for c in gen["cards"])
    # an explicit type is still honoured
    gen = _generate(client, pid, count=3, password_length=8,
                    password_generation_type="strong")
    assert gen["batch"]["password_generation_type"] == "strong"


# ─────────────── 11. deferred items ───────────────

def test_print_job_cancel_and_queue_cap(client, monkeypatch):
    import app.radius.services.operations as ops
    monkeypatch.setattr(ops._PRINT_EXPORT_EXECUTOR, "submit", lambda *a, **k: None)
    tid = _template(client)
    res = client.post(f"/api/v1/print-templates/{tid}/export-jobs", headers=AUTH, json={})
    assert res.status_code in (200, 201, 202), res.get_json()
    job = res.get_json()["data"]["job"]
    d = _data(client.post(f"/api/v1/print-jobs/{job['id']}/cancel", headers=AUTH))
    assert d["job"]["status"] == "cancelled"
    _data(client.delete(f"/api/v1/print-jobs/{job['id']}", headers=AUTH))  # idempotent
    _err(client.get(f"/api/v1/print-jobs/{job['id']}/download", headers=AUTH), 422)
    _err(client.post("/api/v1/print-jobs/999999/cancel", headers=AUTH), 404)
    # the worker never starts a cancelled job
    from app.radius.services.operations import get_operations_service
    get_operations_service()._run_print_template_export_job(
        1, int(job["id"]), tid, {}, None, {}, {}, "t")
    assert _db().execute("SELECT status FROM print_jobs WHERE id=?",
                         (job["id"],)).fetchone()["status"] == "cancelled"
    for _ in range(ops.PRINT_JOBS_MAX_PENDING):
        client.post(f"/api/v1/print-templates/{tid}/export-jobs", headers=AUTH, json={})
    _err(client.post(f"/api/v1/print-templates/{tid}/export-jobs", headers=AUTH, json={}), 409)


def test_recharge_value_is_bounded(client):
    _plan()
    res = client.post("/api/v1/cards/recharge", headers=AUTH, json={
        "package_name": "r", "denominations": [{"value": 1e12, "count": 1}]})
    assert res.status_code == 422, res.get_json()


def test_card_user_short_password_refused_on_create(client):
    _err(client.post("/api/v1/card-users", headers=AUTH,
                     json={"display_name": "Card User", "password": "ab"}), 422)
    _data(client.post("/api/v1/card-users", headers=AUTH,
                      json={"display_name": "Card User", "password": "abcd"}), 201)


def test_card_serializer_exposes_the_mac_lock(client):
    from app.radius.db.repos import cards_repo
    pid = _plan()
    card = _generate(client, pid)["cards"][0]
    cards_repo.set_card_locked_mac(1, card["id"], "AA:BB:CC:DD:EE:01", actor="t")
    d = _data(client.get(f"/api/v1/cards/{card['id']}", headers=AUTH))
    got = d.get("card") or d
    assert got["locked_mac"] == "AA:BB:CC:DD:EE:01"


def test_migrated_managers_get_the_least_privileged_role(app):
    from app.radius.db.repos import admins_repo
    from app.radius.services.migration import engine
    idmap = {engine.SEC_MANAGERS: {}}
    admin_id = engine._ensure_manager(1, "mig_mgr_" + uuid4().hex[:4], idmap, "t", False)
    admin = admins_repo.get_admin(admin_id)
    assert admin.role_id == admins_repo.least_privileged_role_id()
    assert not admin.is_super_admin


def test_reports_split_totals_per_currency(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    _data(client.post("/api/v1/payments", headers=AUTH,
                      json={"username": s.username, "amount": 10, "currency": "ILS"}), 201)
    _data(client.post("/api/v1/payments", headers=AUTH,
                      json={"username": s.username, "amount": 4, "currency": "USD"}), 201)
    from app.radius.db.repos import accounting_repo
    totals = accounting_repo.subscriber_payment_totals(1)
    split = {r["currency"]: r["total"] for r in totals["by_currency"]}
    assert split == {"ILS": 10.0, "USD": 4.0} and totals["mixed_currency"] is True
    loans = accounting_repo.loan_totals(1)
    assert "by_currency" in loans


def test_reencoding_skipped_for_an_already_small_background():
    import base64 as b64
    import io
    from PIL import Image
    from app.radius.routes.print_templates import _optimize_background_image
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (10, 120, 200)).save(buf, format="JPEG", quality=80)
    raw = buf.getvalue()
    out = _optimize_background_image(raw, "bg.jpg", "image/jpeg")
    assert b64.b64decode(out["background_image_data_url"].split(",", 1)[1]) == raw
    again = _optimize_background_image(raw, "bg.jpg", "image/jpeg")
    assert again["background_image_data_url"] == out["background_image_data_url"]


# ─────────────── 10. self-updater: FreeRADIUS + nginx ───────────────

_UPDATER = os.path.join(os.path.dirname(__file__), "..", "deploy", "updater",
                        "hoberadius-updater.sh")


def _bash():
    import shutil
    for cand in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe",
                 shutil.which("bash")):
        if cand and os.path.exists(cand) and "system32" not in cand.lower():
            return cand
    return None


_FAKE_DOCKER = r'''#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
state="$FAKE_STATE"
case "$1" in
  image)
    if [ "$2" = "inspect" ]; then
      img="${@: -1}"
      case "$img" in
        *freeradius:rollback) cat "$state/fr_rollback" 2>/dev/null || exit 1 ;;
        *freeradius*) cat "$state/fr_live" 2>/dev/null || exit 1 ;;
        *) echo "sha:panel" ;;
      esac
    fi
    exit 0 ;;
  inspect) echo healthy; exit 0 ;;
  tag)
    case "$2" in
      *freeradius:latest) cp "$state/fr_live" "$state/fr_rollback" 2>/dev/null ;;
      *freeradius:rollback) cp "$state/fr_rollback" "$state/fr_live" 2>/dev/null ;;
    esac
    exit 0 ;;
  compose)
    if echo "$*" | grep -q " build freeradius" && [ "${FAKE_FR_CHANGES:-0}" = "1" ]; then
      echo "sha:fr-new" > "$state/fr_live"
    fi
    if echo "$*" | grep -q " build freeradius" && [ "${FAKE_FR_BUILD_FAIL:-0}" = "1" ]; then
      exit 1
    fi
    exit 0 ;;
esac
exit 0
'''

_FAKE_GIT = r'''#!/usr/bin/env bash
echo "git $*" >> "$FAKE_LOG"
case " $* " in
  *" rev-parse HEAD"*) echo "c0ffee" ;;
  *" diff --name-only"*)
    paths=(); seen=0
    for a in "$@"; do [ "$seen" = 1 ] && paths+=("$a"); [ "$a" = "--" ] && seen=1; done
    IFS=',' read -ra files <<< "${FAKE_DIFF:-}"
    for f in "${files[@]}"; do
      for p in "${paths[@]}"; do
        case "$f" in "$p"|"$p"/*) echo "$f" ;; esac
      done
    done ;;
esac
exit 0
'''

_FAKE_SQLITE = r'''#!/usr/bin/env bash
case "$2" in
  .backup*) out="${2#.backup }"; out="${out//\'/}"; : > "$out" ;;
  PRAGMA*) echo ok ;;
esac
exit 0
'''


def _run_updater(tmp_path, *, fr_changes: bool, diff: str, fr_build_fail: bool = False):
    import subprocess
    bash = _bash()
    if not bash:
        pytest.skip("bash not available")
    root, upd, fake, state = (tmp_path / n for n in ("root", "upd", "fakebin", "state"))
    for d in (root / "deploy", root / "instance", upd, fake, state):
        d.mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text("", encoding="utf-8")
    (root / "deploy" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    db_file = root / "instance" / "hoberadius.db"
    db_file.write_text("x", encoding="utf-8")
    (state / "fr_live").write_text("sha:fr-old\n", encoding="utf-8")
    import json as _json
    (upd / "update-request.json").write_text(_json.dumps({
        "requested_version": "latest", "requested_at": "2026-09-28T10:00:00Z",
        "requested_by_name": "owner"}), encoding="utf-8")
    import sys as _sys
    py = _sys.executable.replace("\\", "/")
    for name, body in (("docker", _FAKE_DOCKER), ("git", _FAKE_GIT),
                       ("sqlite3", _FAKE_SQLITE),
                       ("python3", f'#!/usr/bin/env bash\nexec "{py}" "$@"\n'),
                       ("curl", "#!/usr/bin/env bash\nexit 0\n"),
                       ("sleep", "#!/usr/bin/env bash\nexit 0\n")):
        p = fake / name
        p.write_bytes(body.encode("utf-8"))
        p.chmod(0o755)
    log = tmp_path / "docker.log"
    env = dict(os.environ)
    env.update({
        "PATH": str(fake) + os.pathsep + env.get("PATH", ""),
        "HOBERADIUS_PROJECT_ROOT": str(root).replace("\\", "/"),
        "HOBERADIUS_UPDATE_DIR": str(upd).replace("\\", "/"),
        "HOBERADIUS_DB_PATH": str(db_file).replace("\\", "/"),
        "HOBERADIUS_BACKUP_DIR": str(root / "backups").replace("\\", "/"),
        "HOBERADIUS_UPDATER_LOG": str(tmp_path / "updater.log").replace("\\", "/"),
        "HOBERADIUS_UPDATER_HEALTH_TIMEOUT": "5",
        "FAKE_LOG": str(log).replace("\\", "/"),
        "FAKE_STATE": str(state).replace("\\", "/"),
        "FAKE_FR_CHANGES": "1" if fr_changes else "0",
        "FAKE_FR_BUILD_FAIL": "1" if fr_build_fail else "0",
        "FAKE_DIFF": diff,
    })
    # a Windows checkout (autocrlf) may hand us CRLF — the host runs it as LF
    script_path = tmp_path / "hoberadius-updater.sh"
    script_path.write_bytes(open(_UPDATER, "rb").read().replace(b"\r\n", b"\n"))
    script = str(script_path).replace(chr(92), "/")
    env["FAKEBIN"] = str(fake).replace("\\", "/")
    # the fakes must win over a login shell's own PATH (Git-bash puts
    # /mingw64/bin first) — prepend them inside bash itself.
    runner = ('PATH="$(cygpath -u "$FAKEBIN" 2>/dev/null || echo "$FAKEBIN"):$PATH"; '
              'exec bash "$0" --once')
    subprocess.run([bash, "-c", runner, script], env=env, timeout=120,
                   capture_output=True, text=True)
    status = _json.loads((upd / "update-status.json").read_text(encoding="utf-8"))
    return status, log.read_text(encoding="utf-8") if log.exists() else ""


def test_updater_rebuilds_and_recreates_freeradius_when_its_image_changed(tmp_path):
    status, log = _run_updater(tmp_path, fr_changes=True,
                               diff="deploy/freeradius/mods-enabled/rest")
    assert status["state"] == "success", status
    assert " build --no-cache hoberadius" in log
    assert " build freeradius" in log
    assert "up -d --no-deps --force-recreate freeradius" in log
    assert "--force-recreate nginx" not in log     # nginx config untouched


def test_updater_leaves_radius_running_when_its_image_is_unchanged(tmp_path):
    status, log = _run_updater(tmp_path, fr_changes=False, diff="app/api/v1/cards.py")
    assert status["state"] == "success", status
    assert " build freeradius" in log
    assert "--force-recreate freeradius" not in log
    assert "--force-recreate nginx" not in log


def test_updater_recreates_nginx_when_its_config_changed(tmp_path):
    status, log = _run_updater(tmp_path, fr_changes=False, diff="deploy/nginx.conf")
    assert status["state"] == "success", status
    assert "up -d --no-deps --force-recreate nginx" in log


def test_updater_rolls_back_when_the_radius_build_fails(tmp_path):
    status, log = _run_updater(tmp_path, fr_changes=False, diff="",
                               fr_build_fail=True)
    assert status["state"] == "failed" and status.get("failed_stage") == "companions"
    assert status.get("rolled_back") is True
    assert "reset --hard c0ffee" in log  # code back to the rollback point


# ─────────────── 12. FreeRADIUS accounting: pool, fsync, Stop/Interim fallback ───────────────

_SQL_CONF = os.path.join(os.path.dirname(__file__), "..", "deploy", "freeradius",
                         "mods-enabled", "sql")


def _sql_conf_text() -> str:
    return open(_SQL_CONF, encoding="utf-8").read().replace("\r\n", "\n")


def _acct_queries() -> dict[str, list[str]]:
    """type → [query, fallback…] exactly as rlm_sql reads them (a `\\`+newline
    continues the string)."""
    import re
    text = _sql_conf_text().replace("\\\n", " ")
    acct = text[re.search(r"^    accounting \{", text, re.M).start():]
    out: dict[str, list[str]] = {}
    for name in ("start", "interim-update", "stop"):
        block = acct[re.search(r"^            " + name + r" \{", acct, re.M).start():]
        block = block[:block.index("\n            }")]
        out[name] = re.findall(r'query = "(.*?)"\s*$', block, re.M)
    return out


def _xlat(query: str, attrs: dict) -> str:
    import re
    q = query.replace("${....acct_table1}", "radacct").replace("${....acct_table2}", "radacct")
    q = re.sub(r"%\{%\{([\w-]+)\}:-(\w*)\}", lambda m: str(attrs.get(m.group(1), m.group(2))), q)
    return re.sub(r"%\{([\w-]+)\}", lambda m: str(attrs.get(m.group(1), "")), q)


def _rlm_sql(kind: str, attrs: dict) -> int:
    """rlm_sql semantics: run the type's queries in order until one changes
    a row; returns the rows changed (0 = noop, still ACKed)."""
    for q in _acct_queries()[kind]:
        cur = _db().execute(_xlat(q, attrs))
        if cur.rowcount:
            return cur.rowcount
    return 0


_ACCT = {"User-Name": "acctuser", "Acct-Session-Id": "sess-1",
         "Packet-Src-IP-Address": "10.50.0.1", "NAS-Port": "7",
         "Acct-Session-Time": "300", "Acct-Input-Octets": "1000",
         "Acct-Output-Octets": "2000", "Calling-Station-Id": "AA:BB:CC:00:00:01",
         "Acct-Terminate-Cause": "User-Request", "Framed-IP-Address": "10.9.9.9"}


def _acct_rows(sid="sess-1"):
    return [dict(r) for r in _db().execute(
        "SELECT * FROM radacct WHERE acctsessionid = ? ORDER BY radacctid", (sid,))]


def test_accounting_pool_and_open_query(app):
    text = _sql_conf_text()
    pool = text[text.index("pool {"):]
    assert "max            = 32" in pool[:300]
    assert 'open_query = "PRAGMA synchronous = NORMAL"' in text


def test_stop_without_start_inserts_the_closed_session_once(app):
    assert _rlm_sql("stop", _ACCT) == 1          # UPDATE 0 rows → fallback INSERT
    rows = _acct_rows()
    assert len(rows) == 1 and rows[0]["acctstoptime"]
    assert int(rows[0]["acctsessiontime"]) == 300
    assert int(rows[0]["acctinputoctets"]) == 1000
    start = datetime.strptime(rows[0]["acctstarttime"], "%Y-%m-%d %H:%M:%S")
    stop = datetime.strptime(rows[0]["acctstoptime"], "%Y-%m-%d %H:%M:%S")
    assert 295 <= (stop - start).total_seconds() <= 305
    assert _rlm_sql("stop", _ACCT) == 0          # retransmit → noop, no duplicate
    assert len(_acct_rows()) == 1


def test_normal_start_stop_never_uses_the_fallback(app):
    assert _rlm_sql("start", _ACCT) == 1
    assert _rlm_sql("start", _ACCT) == 0         # idempotent Start
    assert _rlm_sql("stop", _ACCT) == 1          # the UPDATE closes it
    rows = _acct_rows()
    assert len(rows) == 1 and rows[0]["acctstoptime"]


def test_interim_without_start_opens_the_row_but_never_reopens(app):
    attrs = dict(_ACCT, **{"Acct-Session-Id": "sess-2"})
    assert _rlm_sql("interim-update", attrs) == 1
    rows = _acct_rows("sess-2")
    assert len(rows) == 1 and rows[0]["acctstoptime"] is None and rows[0]["acctupdatetime"]
    assert _rlm_sql("stop", attrs) == 1
    assert _rlm_sql("interim-update", attrs) == 0   # late interim: stays closed
    assert len(_acct_rows("sess-2")) == 1 and _acct_rows("sess-2")[0]["acctstoptime"]


# ─────────────── 13. the «اكتف» cap vs phantom sessions ───────────────

def _open_rows(n, *, user="capuser", mac="AA:BB:CC:DD:EE:01", nas="10.60.0.1", start=None):
    from datetime import timedelta
    st = (start or datetime.utcnow() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(n):
        _db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
            "nasipaddress, callingstationid, acctstarttime) VALUES(1,?,?,?,?,?,?)",
            (f"ph-{user}-{mac}-{i}", f"u-{user}-{mac}-{i}", user, nas, mac, st))


def test_cap_counts_one_session_per_device(app):
    from app.radius.services import provider_grant
    _open_rows(40)                                   # 40 lost Stops, one device
    _open_rows(1, user="other", mac="AA:BB:CC:DD:EE:02")
    _open_rows(3, user="nomac", mac="")              # no MAC/IP → each counts
    assert provider_grant.count_active_sessions(1) == 1 + 1 + 3


def test_cap_live_verification_trusts_only_fresh_non_empty_reads(app):
    from app.radius.services import nas_liveness, provider_grant
    for i in range(10):
        _open_rows(1, user=f"u{i}", mac=f"AA:BB:CC:DD:EE:{i:02X}")
    assert provider_grant.count_active_sessions(1, live_verify=True) == 10
    nas_liveness.record_reachable(1, "10.60.0.1", active_count=0)   # empty read
    assert provider_grant.count_active_sessions(1, live_verify=True) == 10
    nas_liveness.record_reachable(1, "10.60.0.1", active_count=3)
    assert provider_grant.count_active_sessions(1, live_verify=True) == 3
    assert provider_grant.count_active_sessions(1) == 10            # no verify
    nas_liveness.record_unreachable(1, "10.60.0.1")
    assert provider_grant.count_active_sessions(1, live_verify=True) == 10


def test_cap_check_lets_a_new_user_in_despite_phantoms(app, monkeypatch):
    from app.radius.core.types import Subscriber
    from app.radius.services import policy_engine, provider_grant
    monkeypatch.setattr(provider_grant, "get_active_online_cap", lambda tid: 5)
    for i in range(3):
        _open_rows(50, user=f"storm{i}", mac=f"AA:BB:CC:00:00:{i:02X}")  # 150 phantoms
    sub = Subscriber(id=None, tenant_id=1, username="newcomer", password="pw12")
    req = policy_engine.AuthRequest(username="newcomer", password="pw12", tenant_id=1)
    assert policy_engine._check_provider_active_cap(sub, req) is None
    for i in range(3, 6):
        _open_rows(1, user=f"real{i}", mac=f"AA:BB:CC:11:00:{i:02X}")
    bad = policy_engine._check_provider_active_cap(sub, req)
    assert bad is not None and bad.reason == "provider_active_cap"


# ─────────────── 14. multi-process: shared state + process layout ───────────────

_CHILD = r'''
import os, sys, time
os.environ["HOBERADIUS_DB_PATH"] = sys.argv[1]
from app.radius.services import nas_liveness
from app.radius.auth import login_throttle
from app.radius.db import shared_state
from app.workers import heartbeat
nas_liveness.record_reachable(1, "10.70.0.1", active_count=7)
for _ in range(login_throttle.max_failures()):
    login_throttle.register_failure("admin_login", "victim", "1.2.3.4")
heartbeat.beat("child_worker", info={"routers": 3})
shared_state.kv_put("cardgen", "job-x", {"status": "running", "done": 5}, ttl=60)
print("child-ok", shared_state.try_lock("data_reset", ttl=60) is not None)
'''


def test_state_written_by_another_process_is_seen_here(app, tmp_path):
    import subprocess
    import sys
    from app.radius.auth import login_throttle
    from app.radius.db import shared_state
    from app.radius.routes.cards import _get_generate_job
    from app.radius.services import nas_liveness
    from app.workers import heartbeat
    db_path = os.environ["HOBERADIUS_DB_PATH"]
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env = dict(os.environ, PYTHONPATH=root)
    out = subprocess.run([sys.executable, str(script), db_path], cwd=root, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "child-ok True" in out.stdout, out.stderr[-2000:]
    # nothing of this lives in THIS process's memory — only in the shared DB
    assert nas_liveness.is_reachable(1, "10.70.0.1") is True
    assert nas_liveness.active_for(1, "10.70.0.1") == 7
    assert login_throttle.retry_after("admin_login", "victim", "1.2.3.4") > 0
    assert heartbeat.get_info("child_worker") == {"routers": 3}
    assert any(h["name"] == "child_worker" and h["is_alive"] for h in heartbeat.snapshot())
    assert _get_generate_job("job-x")["done"] == 5
    assert shared_state.try_lock("data_reset", ttl=60) is None      # held by the child


def test_web_login_lockout_is_shared_state(client):
    from app.radius.db import shared_state
    for _ in range(10):
        client.post("/api/admin/login", json={"username": "owner_left", "password": "bad"})
    assert _count("SELECT COUNT(*) FROM rate_events WHERE scope = 'login_fail'") >= 10
    shared_state.reset_memory()   # a fresh process has no memory of it…
    res = client.post("/api/admin/login", json={"username": "owner_left", "password": "owner-pass"})
    assert res.status_code == 429  # …and is still locked


def test_exclusive_operations_hold_across_processes(app):
    from app.radius.db import shared_state
    from app.radius.routes import data_reset
    assert data_reset._WIPE_LOCK.acquire(blocking=False) is True
    other = shared_state.SharedOpLock("data_reset")   # «another process»
    assert other.acquire(blocking=False) is False
    data_reset._WIPE_LOCK.release()
    assert other.acquire(blocking=False) is True
    other.release()


def test_hotspot_blob_is_fetchable_from_another_process(app):
    from app.radius.db import shared_state
    from app.radius.services import hotspot_publish_store as hps
    tok = hps.stash(b"<html>x</html>", content_type="text/html")
    shared_state.reset_memory()
    assert hps.take(tok) == (b"<html>x</html>", "text/html")
    assert hps.take(tok) is None


def test_gunicorn_layout_defaults(monkeypatch):
    import runpy
    conf = os.path.join(os.path.dirname(__file__), "..", "deploy", "gunicorn.conf.py")
    for k in ("HOBERADIUS_SEPARATE_WORKER", "HOBERADIUS_NO_WORKER", "GUNICORN_WORKERS",
              "HOBERADIUS_GUNICORN_ROLE"):
        monkeypatch.delenv(k, raising=False)
    assert runpy.run_path(conf)["workers"] == 1           # threads in-process
    monkeypatch.setenv("GUNICORN_WORKERS", "4")
    assert runpy.run_path(conf)["workers"] == 1           # never 2× the threads
    monkeypatch.setenv("HOBERADIUS_SEPARATE_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    assert runpy.run_path(conf)["workers"] == 4           # .env override
    monkeypatch.delenv("GUNICORN_WORKERS")
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert runpy.run_path(conf)["workers"] == 2
    monkeypatch.setattr(os, "cpu_count", lambda: 1)
    assert runpy.run_path(conf)["workers"] == 1


def test_entrypoint_starts_the_worker_process():
    ep = open(os.path.join(os.path.dirname(__file__), "..", "deploy", "entrypoint.sh"),
              encoding="utf-8").read()
    assert "python -m app.worker_main" in ep
    assert "export HOBERADIUS_NO_WORKER=1" in ep and "HOBERADIUS_WORKER_PROCESS" in ep
    import app.worker_main as wm
    assert callable(wm.main)
