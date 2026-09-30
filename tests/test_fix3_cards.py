# -*- coding: utf-8 -*-
"""Fix wave 3 (2026-09-30) — `cardsnet` stream, cards half (FINAL CAMPAIGN F05).

* M1 — add-time on a plan WITH its own duration reaches Session-Timeout.
* M2 — card add-time: 1 year per operation, never beyond 2100 (web + API).
* M3 — designer: a no-op save keeps the saved brand.
* M4 — «إظهار السعر» prints the batch price + currency (web + export).
* M5 — money caps (wallet recharge / package price) + Arabic errors.
* leftovers — archived batch enable 409, local-day batch code, single CSV
  charset, idempotency key re-use 422, validity-days plan, QR vs headings,
  36 pt pill margin, generator preview rules, «(0 يوم)», batch currency,
  font size «abc».
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest

from app.radius.core.numbers import EXPIRY_TOO_FAR_AR, EXTEND_TOO_LONG_AR


@pytest.fixture
def app(monkeypatch, tmp_path):
    import os
    db_file = os.path.join(tmp_path, "f3_cards.db")
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
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True,
                                 role_id=admins_repo.get_role_by_name("super_admin").id)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth(client):
    res = client.post("/api/admin/login",
                      json={"username": "owner_root", "password": "x12345678"})
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _db():
    from app.radius.db.connection import db
    return db()


def _is_arabic(text) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in str(text or ""))


def _plan(*, duration_minutes=0, validity_days=0, session_timeout_sec=0,
          currency="ILS") -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " session_timeout_sec, price, currency, speed_down_kbps, speed_up_kbps,"
        " quota_total_mb, created_at, updated_at) VALUES(1,?,?,?,?,5.0,?,2048,2048,0,"
        "datetime('now'),datetime('now'))",
        ("f3-" + uuid.uuid4().hex[:6], duration_minutes, validity_days,
         session_timeout_sec, currency))
    _db().commit()
    return int(cur.lastrowid)


def _gen(client, auth, plan_id, **extra):
    body = {"plan_id": plan_id, "count": 1, "username_prefix": "f" + uuid.uuid4().hex[:4],
            "username_length": 10}
    body.update(extra)
    res = client.post("/api/v1/cards/generate", json=body, headers=auth)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    return data["batch"], data["cards"]


def _adjust(client, auth, card_id, **body):
    return client.post(f"/api/v1/cards/{card_id}/adjust-time", json=body, headers=auth)


def _authorize(app, card):
    with app.app_context():
        from app.radius.services import policy_engine
        return policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))


def _web_login(c):
    with c.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "owner_root"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "f3-csrf"


# ══════════ M1 — the grant reaches Session-Timeout on a «ساعة» plan ══════════

def test_grant_before_first_login_on_plan_with_own_duration(client, auth, app):
    pid = _plan(duration_minutes=60)
    _, cards = _gen(client, auth, pid)
    card = cards[0]
    assert _adjust(client, auth, card["id"], amount=30, unit="minutes",
                   op="add").status_code == 200
    dec = _authorize(app, card)
    assert dec.ok, dec.reason
    # was 3600: the window grew but the session was cut at 60 minutes
    assert 5390 <= int(dec.reply_attrs["Session-Timeout"]) <= 5400
    # the SUBSEQUENT session (stamped card) keeps the granted end too
    dec2 = _authorize(app, card)
    assert dec2.ok and 5300 <= int(dec2.reply_attrs["Session-Timeout"]) <= 5400


def test_grant_on_a_live_card_with_plan_duration(client, auth, app):
    pid = _plan(duration_minutes=60)
    _, cards = _gen(client, auth, pid)
    card = cards[0]
    assert _authorize(app, card).ok                      # first login stamps 60 min
    assert _adjust(client, auth, card["id"], amount=1, unit="days",
                   op="add").status_code == 200
    dec = _authorize(app, card)
    assert dec.ok and int(dec.reply_attrs["Session-Timeout"]) > 86000


def test_deduction_on_plan_with_duration_still_shortens(client, auth, app):
    pid = _plan(duration_minutes=60)
    _, cards = _gen(client, auth, pid)
    card = cards[0]
    _adjust(client, auth, card["id"], amount=45, unit="minutes", op="subtract")
    dec = _authorize(app, card)
    assert dec.ok and int(dec.reply_attrs["Session-Timeout"]) <= 900


def test_explicit_plan_session_timeout_still_caps(client, auth, app):
    """An operator-written `session_timeout_sec` is a deliberate session cap."""
    pid = _plan(duration_minutes=60, session_timeout_sec=1200)
    _, cards = _gen(client, auth, pid)
    card = cards[0]
    _adjust(client, auth, card["id"], amount=2, unit="hours", op="add")
    dec = _authorize(app, card)
    assert dec.ok and int(dec.reply_attrs["Session-Timeout"]) == 1200


def test_by_seconds_card_session_timeout_is_remaining_usage(client, auth, app):
    pid = _plan(duration_minutes=60)
    _, cards = _gen(client, auth, pid, count_by_seconds=True,
                    count_from_first_connect=False)
    card = cards[0]
    _adjust(client, auth, card["id"], amount=30, unit="minutes", op="add")
    _db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,"
        " acctstarttime, acctstoptime, acctsessiontime) VALUES(1,'s1','u1',?,'10.0.0.1',"
        "datetime('now','-20 minutes'), datetime('now','-10 minutes'), 600)",
        (card["username"],))
    _db().commit()
    dec = _authorize(app, card)
    assert dec.ok, dec.reason
    assert int(dec.reply_attrs["Session-Timeout"]) == 3600 + 1800 - 600


# ══════════ M2 — 1 year per operation + year 2100 (web & API) ══════════

def test_api_card_add_time_over_one_year_is_422(client, auth):
    _, cards = _gen(client, auth, _plan())
    cid = cards[0]["id"]
    for body in ({"amount": 366, "unit": "days", "op": "add"},
                 {"amount": 3650, "unit": "days", "op": "add"},
                 {"delta_seconds": 365 * 86400 + 1}):
        res = _adjust(client, auth, cid, **body)
        assert res.status_code == 422, body
        assert res.get_json()["error"]["message"] == EXTEND_TOO_LONG_AR
    assert _adjust(client, auth, cid, amount=365, unit="days", op="add").status_code == 200
    # deductions beyond 3650 days are refused in Arabic (never a 500)
    res = _adjust(client, auth, cid, amount=4000, unit="days", op="subtract")
    assert res.status_code == 422 and _is_arabic(res.get_json()["error"]["message"])


def test_repeated_grants_never_pass_year_2100(client, auth, app):
    _, cards = _gen(client, auth, _plan(), time_value=1, time_unit="days")
    cid = cards[0]["id"]
    last = None
    for _ in range(90):
        last = _adjust(client, auth, cid, amount=365, unit="days", op="add")
        if last.status_code != 200:
            break
    assert last.status_code == 422
    assert last.get_json()["error"]["message"] == EXPIRY_TOO_FAR_AR
    # first login after the last accepted grant stamps before 2101
    assert _authorize(app, cards[0]).ok
    row = _db().execute("SELECT expire_at FROM cards WHERE id=?", (cid,)).fetchone()
    assert row["expire_at"] < "2101-01-01"


def test_first_login_stamp_is_clamped_below_2101_for_legacy_grants(client, auth, app):
    _, cards = _gen(client, auth, _plan(), time_value=1, time_unit="days")
    card = cards[0]
    _db().execute("UPDATE cards SET extra_seconds=? WHERE id=?",
                  (120 * 365 * 86400, card["id"]))
    _db().commit()
    assert _authorize(app, card).ok
    row = _db().execute("SELECT expire_at FROM cards WHERE id=?", (card["id"],)).fetchone()
    assert row["expire_at"] and row["expire_at"] < "2101-01-01"


def test_web_checker_set_time_5000_days_refused(client, auth, app):
    _, cards = _gen(client, auth, _plan())
    card = cards[0]
    with app.test_client() as c:
        _web_login(c)
        r = c.post("/admin/radius/cards/checker", data={
            "_csrf_token": "f3-csrf", "_card_action": "set_time",
            "card_id": str(card["id"]), "username": card["username"],
            "time_amount": "5000", "time_unit": "days", "time_op": "add"},
            follow_redirects=True)
        assert EXTEND_TOO_LONG_AR in r.get_data(as_text=True)
    row = _db().execute("SELECT extra_seconds FROM cards WHERE id=?", (card["id"],)).fetchone()
    assert int(row["extra_seconds"] or 0) == 0


def test_shared_helper_is_the_single_rule():
    from app.radius.core.numbers import NonFiniteNumber, check_time_delta_seconds
    assert check_time_delta_seconds(365 * 86400) == 365 * 86400
    with pytest.raises(NonFiniteNumber):
        check_time_delta_seconds(365 * 86400 + 1)
    with pytest.raises(NonFiniteNumber):
        check_time_delta_seconds(-3651 * 86400)


# ══════════ M3 — designer no-op save keeps the brand ══════════

def test_designer_edit_page_does_not_apply_language_defaults(client, auth, app):
    res = client.post("/api/v1/print-templates/quick-save", json={"form": {
        "name": "f3-brand", "font_size_unit": "pt", "render_engine": "ar_horizontal",
        "background_style": "preset", "show_qr": "0"}}, headers=auth)
    assert res.status_code in (200, 201), res.get_json()
    tid = res.get_json()["data"]["template"]["id"]
    with app.test_client() as c:
        _web_login(c)
        html = c.get(f"/admin/radius/print-templates?edit_template={tid}").get_data(as_text=True)
    assert 'data-editing="1"' in html
    assert "syncRenderEngine({keepTexts: !!(form && form.dataset.editing === '1')})" in html
    assert 'value="HobeRadius"' in html
    # a brand-new form still offers the language default (not editing)
    with app.test_client() as c:
        _web_login(c)
        html = c.get("/admin/radius/print-templates").get_data(as_text=True)
    assert 'data-editing="0"' in html


# ══════════ M4 — «إظهار السعر» uses the batch price + currency ══════════

def test_price_label_matches_the_app_rule(app):
    from app.radius.services.card_batch_price import price_label
    assert price_label(2, "ILS") == "2 ILS"
    assert price_label(2.5, "USD") == "2.50 USD"
    assert price_label(0, "ILS") == "" and price_label("abc", "ILS") == ""
    assert price_label(float("inf"), "ILS") == ""


def test_export_prints_batch_price_when_show_price_on(client, auth, app):
    batch, _ = _gen(client, auth, _plan(currency="ILS"), price_per_card=2)
    with app.app_context():
        from app.radius.db.repos import cards_repo
        from app.radius.services.operations import _apply_batch_price_fallback
        b = cards_repo.get_batch(1, batch["id"])
        ov: dict = {}
        _apply_batch_price_fallback(1, {"layout_json": {"show_price": True}}, b, ov)
        assert ov == {"price_text": "2 ILS"}
        ov = {}
        _apply_batch_price_fallback(1, {"layout_json": {"show_price": False}}, b, ov)
        assert ov == {}
        ov = {"price_text": "5 شيكل"}           # the operator's text wins
        _apply_batch_price_fallback(1, {"layout_json": {"show_price": True}}, b, ov)
        assert ov == {"price_text": "5 شيكل"}
        ov = {}
        _apply_batch_price_fallback(1, {"layout_json": {"show_price": True,
                                                       "price_text": "مجاني"}}, b, ov)
        assert ov == {}


def test_real_export_uses_the_batch_price(client, auth, app, monkeypatch):
    batch, _ = _gen(client, auth, _plan(currency="ILS"), price_per_card=2)
    res = client.post("/api/v1/print-templates/quick-save", json={"form": {
        "name": "f3-price", "font_size_unit": "pt", "render_engine": "ar_horizontal",
        "background_style": "preset", "show_price": "1", "show_qr": "0"}}, headers=auth)
    assert res.status_code in (200, 201), res.get_json()
    tid = res.get_json()["data"]["template"]["id"]
    seen = []
    from app.radius.services import card_renderer
    real = card_renderer.build_card_render_model

    def spy(template, card=None, overrides=None):
        seen.append(dict(overrides or {}))
        return real(template, card, overrides=overrides)
    monkeypatch.setattr(card_renderer, "build_card_render_model", spy)
    with app.app_context():
        from app.radius.services.operations import get_operations_service
        pdf = get_operations_service().export_print_template_pdf(
            tenant_id=1, template_id=tid, batch_id=batch["id"])
    assert pdf[:4] == b"%PDF"
    assert seen and all(o.get("price_text") == "2 ILS" for o in seen)


def test_web_quick_screen_has_price_text_from_batch(client, auth, app):
    batch, _ = _gen(client, auth, _plan(currency="ILS"), price_per_card=2)
    with app.test_client() as c:
        _web_login(c)
        html = c.get(f"/admin/radius/cards/print/quick?batch_id={batch['id']}"
                     ).get_data(as_text=True)
    assert 'name="price_text"' in html and 'data-price="2 ILS"' in html


# ══════════ M5 — money caps + Arabic errors ══════════

def _card_user(client, auth) -> int:
    res = client.post("/api/v1/card-users", json={
        "display_name": "مستفيد اختبار", "mobile": "0599" + str(uuid.uuid4().int)[:6],
        "password": "1234"}, headers=auth)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["card_user"]["id"]


def test_wallet_recharge_cap_and_arabic_errors(client, auth):
    cu = _card_user(client, auth)
    for amount in (1e12, 100000.01, 500000):
        res = client.post(f"/api/v1/card-users/{cu}/recharge", json={"amount": amount},
                          headers=auth)
        assert res.status_code == 422, amount
        assert "100000" in res.get_json()["error"]["message"]
    for amount in ("abc", -5, 0, None):
        res = client.post(f"/api/v1/card-users/{cu}/recharge", json={"amount": amount},
                          headers=auth)
        assert res.status_code == 422, amount
        msg = res.get_json()["error"]["message"]
        assert _is_arabic(msg) and "must be" not in msg, msg
    res = client.post(f"/api/v1/card-users/{cu}/recharge", json={"amount": 100000},
                      headers=auth)
    assert res.status_code == 201


def test_web_wallet_recharge_cap(client, auth, app):
    cu = _card_user(client, auth)
    with app.test_client() as c:
        _web_login(c)
        r = c.post(f"/admin/radius/card-users/{cu}/recharge",
                   data={"_csrf_token": "f3-csrf", "amount": "500000"}, follow_redirects=True)
        assert "100000" in r.get_data(as_text=True)
    bal = _db().execute("SELECT COALESCE(SUM(balance_minor),0) AS b FROM wallets").fetchone()
    assert int(bal["b"] or 0) == 0


def test_marketplace_package_price_cap_and_int_errors(client, auth):
    pid = _plan()
    res = client.post("/api/v1/card-marketplace/packages",
                      json={"name": "f3", "plan_id": pid, "price": 1e12}, headers=auth)
    assert res.status_code == 422 and "100000" in res.get_json()["error"]["message"]
    res = client.post("/api/v1/card-marketplace/packages",
                      json={"name": "f3", "plan_id": "abc", "price": 2}, headers=auth)
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert "invalid literal" not in msg and _is_arabic(msg)
    cu = _card_user(client, auth)
    res = client.post(f"/api/v1/card-users/{cu}/purchase", json={"package_id": "abc"},
                      headers=auth)
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert "invalid literal" not in msg and _is_arabic(msg)
    ok = client.post("/api/v1/card-marketplace/packages",
                     json={"name": "f3-ok", "plan_id": pid, "price": 2}, headers=auth)
    assert ok.status_code == 201


def test_arabic_error_message_never_leaks_python_text():
    from app.radius.services.card_users_marketplace import arabic_error_message
    assert arabic_error_message(
        ValueError("invalid literal for int() with base 10: 'abc'")).startswith("قيمة غير صالحة")
    assert arabic_error_message(ValueError("amount must be numeric")) == \
        "المبلغ يجب أن يكون رقمًا صحيحًا."
    assert arabic_error_message(ValueError("رسالة عربية")) == "رسالة عربية"
    from app.radius.services.card_pricing import arabic_pricing_error
    assert arabic_pricing_error(ValueError("count must be positive")) == \
        "عدد البطاقات يجب أن يكون أكبر من صفر."


# ══════════ leftovers ══════════

def test_archived_batch_cards_report_revoked_and_refuse_enable(client, auth, app):
    batch, cards = _gen(client, auth, _plan(), count=2)
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        assert get_cards_service().archive_batch(actor="t", batch_id=batch["id"])
    items = client.get(f"/api/v1/cards/batches/{batch['id']}/cards",
                       headers=auth).get_json()["data"]["items"]
    assert items and all(i["revoked"] is True and i["archived"] is True for i in items)
    res = client.post(f"/api/v1/cards/{cards[0]['id']}/enable", headers=auth)
    assert res.status_code == 409, res.get_json()
    assert _is_arabic(res.get_json()["error"]["message"])
    st = _db().execute("SELECT status FROM subscribers WHERE username=?",
                       (cards[0]["username"],)).fetchone()
    assert st["status"] == "disabled"
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        get_cards_service().restore_batch(actor="t", batch_id=batch["id"])
    assert client.post(f"/api/v1/cards/{cards[0]['id']}/enable",
                       headers=auth).status_code == 200


def test_batch_code_uses_the_local_day(client, auth, monkeypatch):
    from app.radius.core import system_config
    monkeypatch.setattr(system_config, "local_today", lambda tid=None: date(2031, 5, 6))
    batch, _ = _gen(client, auth, _plan())
    assert batch["batch_code"].startswith("B-20310506-")


def test_csv_content_type_has_one_charset(client, auth):
    _gen(client, auth, _plan())
    res = client.get("/api/v1/cards/batches/export.csv", headers=auth)
    assert res.status_code == 200
    assert res.headers["Content-Type"].count("charset") == 1


def test_idempotency_key_reuse_with_another_body_is_422(client, auth):
    pid = _plan()
    h = {**auth, "Idempotency-Key": "f3-" + uuid.uuid4().hex}
    body = {"plan_id": pid, "count": 3, "username_prefix": "fidem", "username_length": 10}
    first = client.post("/api/v1/cards/generate", json=body, headers=h)
    assert first.status_code == 201
    again = client.post("/api/v1/cards/generate", json=body, headers=h)
    assert again.status_code == 201 and again.get_json()["data"]["idempotent_replay"] is True
    other = client.post("/api/v1/cards/generate", json={**body, "count": 1}, headers=h)
    assert other.status_code == 422
    err = other.get_json()["error"]
    assert err["code"] == "idempotency_key_reused" and _is_arabic(err["message"])


def test_validity_days_plan_counts_from_first_login(client, auth, app):
    batch, cards = _gen(client, auth, _plan(validity_days=30))
    row = _db().execute("SELECT expire_at FROM cards WHERE id=?", (cards[0]["id"],)).fetchone()
    assert row["expire_at"] is None                  # was generation + 30 d
    assert batch["time_value"] == 30 and batch["time_unit"] == "days"
    with app.app_context():
        from app.radius.services.card_checker import check_card
        info = check_card(1, cards[0]["username"])
    assert info["remaining_seconds"] == 30 * 86400
    assert _authorize(app, cards[0]).ok
    row = _db().execute("SELECT first_used_at, expire_at FROM cards WHERE id=?",
                        (cards[0]["id"],)).fetchone()
    first = datetime.fromisoformat(row["first_used_at"].replace("Z", ""))
    end = datetime.fromisoformat(row["expire_at"].replace("Z", ""))
    assert abs((end - first).total_seconds() - 30 * 86400) < 5


def _render(**kw):
    from app.radius.services import card_renderer as cr
    layout = {"font_size_unit": "pt", "background_style": "preset",
              "render_engine": "ar_horizontal", "card_width_mm": 85.6,
              "card_height_mm": 54, "show_qr": True, "show_price": True}
    layout.update(kw)
    tpl = {"id": 1, "layout_json": layout}
    for k in ("qr_x", "qr_y"):
        if k in kw:
            tpl[k] = kw[k]
    return cr.build_card_render_model(tpl, {"username": "0123456789012",
                                            "password": "123456", "id": 5})


@pytest.mark.parametrize("pos", [(62.5, 29.7), (84, 53)])
def test_qr_avoidance_never_covers_the_headings(pos):
    model = _render(qr_x=pos[0], qr_y=pos[1], footer_text="احتفظ بالبطاقة")
    qr = next(e for e in model["elements"] if e["kind"] == "qr")
    for e in model["elements"]:
        if e["kind"] == "text" and e["id"] in ("brand", "title"):
            ox = max(0, min(qr["x"] + qr["size"], e["x"] + e["max_width"]) - max(qr["x"], e["x"]))
            oy = max(0, min(qr["y"] + qr["size"], e["y"] + e["size"] * 1.2) - max(qr["y"], e["y"]))
            assert ox * oy <= 0.05 * qr["size"] ** 2, (e["id"], qr)
        if e["kind"] == "pill":
            assert (qr["x"] + qr["size"] <= e["x"] or qr["x"] >= e["x"] + e["width"]
                    or qr["y"] + qr["size"] <= e["y"] or qr["y"] >= e["y"] + e["height"])


def test_big_font_pills_keep_an_edge_margin():
    model = _render(qr_size_pct=30, username_font_size=36, password_font_size=36)
    cw = model["canvas"]["width"]
    for p in (e for e in model["elements"] if e["kind"] == "pill"):
        assert p["x"] >= cw * 0.02 - 0.01 and p["x"] + p["width"] <= cw * 0.98 + 0.01, p


def test_generator_preview_shares_the_server_rule(client, auth, app):
    with app.test_client() as c:
        _web_login(c)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
    from app.radius.services.cards import USERNAME_LENGTH_MAX
    assert f'max="{USERNAME_LENGTH_MAX}"' in html
    assert "فبقي رقمٌ عشوائيّ واحد فقط" not in html      # the false promise is gone
    import json
    phrase = "لا تترك خانةً للأرقام العشوائيّة"
    assert phrase in html or json.dumps(phrase)[1:-1] in html   # same wording as the 422
    # the server refuses exactly what the preview flags
    res = client.post("/api/v1/cards/generate", json={
        "plan_id": _plan(), "count": 1, "username_prefix": "abcdef",
        "username_length": 6}, headers=auth)
    assert res.status_code == 422
    assert "لا تترك خانةً للأرقام العشوائيّة" in res.get_json()["error"]["message"]


def test_card_reply_message_has_real_units(client, auth, app):
    _, cards = _gen(client, auth, _plan(), time_value=2, time_unit="hours")
    card = cards[0]
    assert _authorize(app, card).ok                     # stamps 2 h
    dec = _authorize(app, card)
    msg = dec.reply_attrs["Reply-Message"]
    assert "0 يوم" not in msg and "ساع" in msg, msg
    from app.radius.core.duration_fmt import fmt_remaining_ar
    assert fmt_remaining_ar(5400) == "1 ساعة و30 دقيقة"
    assert fmt_remaining_ar(90000) == "1 يوم و1 ساعة"
    assert fmt_remaining_ar(30) == "أقل من دقيقة"
    assert fmt_remaining_ar(7200) == "ساعتان"


def test_batch_list_and_summary_carry_currency(client, auth):
    batch, _ = _gen(client, auth, _plan(currency="USD"), price_per_card=3)
    items = client.get("/api/v1/cards/batches", headers=auth).get_json()["data"]["items"]
    item = next(i for i in items if i["id"] == batch["id"])
    assert item["currency"] == "USD"
    summ = client.get(f"/api/v1/cards/batches/{batch['id']}/summary",
                      headers=auth).get_json()["data"]["summary"]
    assert summ["currency"] == "USD" and summ["price_per_card"] == 3


def test_font_size_text_is_422(client, auth):
    res = client.post("/api/v1/print-templates/quick-save", json={"form": {
        "name": "f3-font", "font_size_unit": "pt", "render_engine": "ar_horizontal",
        "background_style": "preset", "username_font_size": "abc"}}, headers=auth)
    assert res.status_code == 422
    assert _is_arabic(res.get_json()["error"]["message"])
    ok = client.post("/api/v1/print-templates/quick-save", json={"form": {
        "name": "f3-font2", "font_size_unit": "pt", "render_engine": "ar_horizontal",
        "background_style": "preset", "username_font_size": ""}}, headers=auth)
    assert ok.status_code in (200, 201), ok.get_json()
