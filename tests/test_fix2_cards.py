# -*- coding: utf-8 -*-
"""Fix wave 2 (2026-09-29) — cards stream: R05 / R13 / R11 card findings.

* R13-H1 — «إضافة/خصم وقت» reaches the enforcer (window / Session-Timeout /
  online budget / batch table), and a deduction larger than the card's time
  EXHAUSTS the card (never «0 = unlimited» while «جاهزة»).
* R05-N3 — the web batch-cards page is paged + searched on the server, and
  the web generator lands on the batch summary.
* R05-N5/N6 — import normalises/validates usernames, reports in-file
  duplicates, and never produces an «available» card without an auth row.
* R13-L1/R05-N9 — affixes longer than the username length → 422 (Arabic).
* R13-L2 — batch edit with an empty name → 422.
* R13-M1 — a stopped card shows «موقوف», not «منتهي».
* R05-N10 — an archived batch's card accounts are rejected and not `enabled`.
* R06-N5 — the batch payload carries its currency.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Fresh DB per test (web pages need the license-gate test bypass, which
    only works together with NO_SEED)."""
    import os
    db_file = os.path.join(tmp_path, "f2_cards.db")
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
                                 full_name="Owner", is_super_admin=True)
    _PLAN.clear()
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


def _px() -> str:
    return "f" + uuid.uuid4().hex[:5]


_PLAN: dict = {}


def _plan_id() -> int:
    """A plan WITHOUT its own duration/session cap, so the card window alone
    decides Session-Timeout (plan 1 of the seed caps sessions at 30 min)."""
    if "id" not in _PLAN:
        cur = _db().execute(
            "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
            " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
            " created_at, updated_at) VALUES(1,?,0,0,5.0,'ILS',2048,2048,0,"
            "datetime('now'),datetime('now'))", ("f2-plan-" + uuid.uuid4().hex[:6],))
        _db().commit()
        _PLAN["id"] = int(cur.lastrowid)
    return _PLAN["id"]


def _batch(client, auth, *, count=2, time_value=2, time_unit="hours", **extra):
    body = {"plan_id": _plan_id(), "count": count, "username_prefix": _px(),
            "username_length": 10, "time_value": time_value,
            "time_unit": time_unit}
    body.update(extra)
    res = client.post("/api/v1/cards/generate", json=body, headers=auth)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    return data["batch"], data["cards"]


def _card_row(username):
    return _db().execute(
        "SELECT * FROM cards WHERE tenant_id = 1 AND username = ?",
        (username,)).fetchone()


def _adjust(client, auth, card_id, **body):
    return client.post(f"/api/v1/cards/{card_id}/adjust-time", json=body,
                       headers=auth)


def _web_login(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "owner_root"
        sess["admin_name"] = "Owner"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "f2-csrf"


# ══════════ R13-H1 — card time adjust reaches the enforcer ══════════

def test_grant_before_first_login_extends_the_stamped_window(client, auth, app):
    """+30 min on a 2-hour card that never connected: the first login stamps
    2h30m (was 2h — the grant only moved the checker's number)."""
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    res = _adjust(client, auth, card["id"], amount=30, unit="minutes", op="add")
    assert res.status_code == 200, res.get_json()
    adj = res.get_json()["data"]["adjustment"]
    assert adj["exhausted"] is False and adj["extra_seconds"] == 1800
    with app.app_context():
        from app.radius.services import policy_engine
        # Session-Timeout at first login (built before the stamp).
        assert policy_engine._card_session_window_seconds(
            1, card["batch_id"], card["username"]) == 9000
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
        assert dec.ok, dec.reason
        assert 8990 <= int(dec.reply_attrs["Session-Timeout"]) <= 9000
    row = _card_row(card["username"])
    first = datetime.fromisoformat(row["first_used_at"].replace("Z", ""))
    end = datetime.fromisoformat(row["expire_at"].replace("Z", ""))
    assert abs((end - first).total_seconds() - 9000) < 5
    sub = _db().execute("SELECT expire_at FROM subscribers WHERE tenant_id=1 AND username=?",
                        (card["username"],)).fetchone()
    assert sub["expire_at"] == row["expire_at"]


def test_deduction_before_first_login_shortens_the_window(client, auth, app):
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    assert _adjust(client, auth, card["id"], amount=45, unit="minutes",
                   op="subtract").status_code == 200
    with app.app_context():
        from app.radius.services import policy_engine
        assert policy_engine._card_session_window_seconds(
            1, card["batch_id"], card["username"]) == 7200 - 2700
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
    assert dec.ok and int(dec.reply_attrs["Session-Timeout"]) <= 4500


def test_deduction_larger_than_the_card_exhausts_it(client, auth, app):
    """🔴 R13-H1 second case: −100000 min on a 2h card used to set the budget
    to 0 = «unlimited» while the checker said «جاهزة»."""
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    res = _adjust(client, auth, card["id"], amount=100000, unit="minutes", op="subtract")
    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert payload["adjustment"]["exhausted"] is True
    assert payload["adjustment"]["remaining_seconds"] == 0
    assert payload["card"]["status"] == "expired"
    assert payload["card"]["remaining_seconds"] == 0
    with app.app_context():
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
    assert dec.ok is False
    assert dec.reason in {"expired", "card_time_exhausted"}


def test_legacy_exhausted_card_without_expiry_is_still_rejected(client, auth, app):
    """Data written by the old code: extra = −budget and NO expire_at. The
    enforcer must reject it (not treat 0 as unlimited)."""
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    _db().execute("UPDATE cards SET extra_seconds = -7200 WHERE id = ?", (card["id"],))
    _db().commit()
    with app.app_context():
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
    assert dec.ok is False and dec.reason == "card_time_exhausted"


def test_grant_after_exhaustion_revives_a_card_that_never_started(client, auth, app):
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    _adjust(client, auth, card["id"], amount=10, unit="hours", op="subtract")
    res = _adjust(client, auth, card["id"], amount=1, unit="hours", op="add")
    adj = res.get_json()["data"]["adjustment"]
    assert adj["exhausted"] is False
    assert _card_row(card["username"])["expire_at"] is None
    with app.app_context():
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
    assert dec.ok, dec.reason
    assert int(dec.reply_attrs["Session-Timeout"]) <= 3600


def test_grant_on_a_live_card_moves_the_enforced_end(client, auth, app):
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    with app.app_context():
        from app.radius.services import policy_engine
        assert policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"])).ok
    before = datetime.fromisoformat(_card_row(card["username"])["expire_at"].replace("Z", ""))
    _adjust(client, auth, card["id"], amount=1, unit="days", op="add")
    after = datetime.fromisoformat(_card_row(card["username"])["expire_at"].replace("Z", ""))
    assert abs((after - before).total_seconds() - 86400) < 5
    # exhausting a LIVE card stamps the end in the past → rejected
    _adjust(client, auth, card["id"], amount=100, unit="days", op="subtract")
    with app.app_context():
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
    assert dec.ok is False


def test_online_budget_cell_includes_the_grant(client, auth, app):
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    _adjust(client, auth, card["id"], amount=1, unit="hours", op="add")
    with app.app_context():
        from app.radius.services import online_time_budget as otb
        cells = otb._card_cells(1, [card["username"]], datetime.utcnow())
        assert cells[card["username"]]["total_sec"] == 7200 + 3600
        _adjust(client, auth, card["id"], amount=10, unit="hours", op="subtract")
        cells = otb._card_cells(1, [card["username"]], datetime.utcnow())
        cell = cells[card["username"]]
        assert cell["total_sec"] and cell["used_sec"] >= cell["total_sec"]
        assert cell["bucket"] == "red"


def test_adjust_time_validation_is_arabic_422(client, auth):
    _, cards = _batch(client, auth, count=1)
    cid = cards[0]["id"]
    for body in ({}, {"amount": 0, "unit": "hours"}, {"amount": 5, "unit": "weeks"},
                 {"amount": "abc", "unit": "hours"}, {"delta_seconds": 0},
                 {"amount": 4000, "unit": "days"}):
        res = _adjust(client, auth, cid, **body)
        assert res.status_code == 422, body
        msg = res.get_json()["error"]["message"]
        assert any("؀" <= ch <= "ۿ" for ch in msg), msg
    assert _adjust(client, auth, 99999999, amount=1, unit="hours").status_code == 404


def test_web_batch_table_shows_the_granted_time(client, auth, app):
    batch, cards = _batch(client, auth, count=2)
    _adjust(client, auth, cards[0]["id"], amount=30, unit="minutes", op="add")
    _adjust(client, auth, cards[1]["id"], amount=10, unit="hours", op="subtract")
    with app.test_client() as c:
        _web_login(c)
        html = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards").get_data(as_text=True)
    from app.radius.routes.cards import _format_card_seconds
    assert f"{_format_card_seconds(9000)} — لم تبدأ" in html
    assert "منتهي (خُصم كامل الوقت)" in html


def test_web_set_time_uses_the_same_enforced_path(client, auth, app):
    """The checker page's per-card «خصم وقت» (web) exhausts the card the same
    way as the API and says so."""
    _, cards = _batch(client, auth, count=1)
    card = cards[0]
    with app.test_client() as c:
        _web_login(c)
        r = c.post("/admin/radius/cards/checker", data={
            "_csrf_token": "f2-csrf", "_card_action": "set_time",
            "card_id": str(card["id"]), "username": card["username"],
            "time_amount": "3", "time_unit": "hours", "time_op": "subtract"},
            follow_redirects=True)
        assert "استُنفد وقت البطاقة" in r.get_data(as_text=True)
    with app.app_context():
        from app.radius.services.card_checker import check_card
        info = check_card(1, card["username"])
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
    assert info["status"] == "expired"
    assert info["remaining_seconds"] == 0
    assert dec.ok is False


# ══════════ R05-N3 — batch cards page: server paging + search ══════════

def test_batch_cards_page_is_paged_on_the_server(client, auth, app):
    batch, cards = _batch(client, auth, count=130)
    with app.test_client() as c:
        _web_login(c)
        r = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        assert html.count('data-row\n') + html.count("data-row\r\n") == 50 \
            or html.count("data-card-id=") == 50
        assert "1–50 من 130" in html
        # page 3 has the last 30
        html3 = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards?page=3").get_data(as_text=True)
        assert html3.count("data-card-id=") == 30
        assert "101–130 من 130" in html3
        # per_page is whitelisted
        html_big = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards?per_page=100000").get_data(as_text=True)
        assert html_big.count("data-card-id=") == 50
        # server search across ALL pages (the last card is on page 3)
        target = sorted(cards, key=lambda x: x["id"])[0]["username"]
        hs = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards",
                   query_string={"q": target}).get_data(as_text=True)
        assert hs.count("data-card-id=") == 1 and target in hs
        # Arabic-Indic digits in the search box
        arabic = target.translate({ord(str(i)): a for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")})
        ha = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards",
                   query_string={"q": arabic}).get_data(as_text=True)
        assert ha.count("data-card-id=") == 1
        # the KPI total still counts the whole batch
        assert "130 كرت" in hs


def test_web_generator_redirects_to_the_batch_summary(app):
    with app.test_client() as c:
        _web_login(c)
        form = {"_csrf_token": "f2-csrf", "plan_id": str(_plan_id()), "count": "3",
                "batch_type": "printed", "username_length": "10",
                "username_prefix": _px(), "package_name": "f2-redirect",
                "request_key": uuid.uuid4().hex}
        r = c.post("/admin/radius/cards/generate", data=form)
    assert r.status_code in (302, 303)
    loc = r.headers["Location"]
    path, _, query = loc.partition("?")
    assert path.endswith("/cards/batches") and "q=B-" in query


def test_progress_generator_redirect_is_the_summary(app):
    import time
    with app.test_client() as c:
        _web_login(c)
        form = {"_csrf_token": "f2-csrf", "plan_id": str(_plan_id()), "count": "2",
                "batch_type": "printed", "username_length": "10",
                "username_prefix": _px(), "package_name": "f2-progress",
                "request_key": uuid.uuid4().hex}
        r = c.post("/admin/radius/cards/generate/progress", data=form)
        job = r.get_json()["job_id"]
        for _ in range(100):
            st = c.get(f"/admin/radius/cards/generate/progress/{job}").get_json()
            if st.get("status") in ("done", "error"):
                break
            time.sleep(0.05)
    assert st["status"] == "done", st
    assert "/cards/batches?q=B-" in st["redirect_url"]


# ══════════ R13-M1 — a stopped card is «موقوف» ══════════

def test_stopped_card_shows_mawqoof_not_expired(client, auth, app):
    batch, cards = _batch(client, auth, count=2)
    assert client.post(f"/api/v1/cards/{cards[0]['id']}/disable", json={},
                       headers=auth).status_code == 200
    with app.test_client() as c:
        _web_login(c)
        html = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards").get_data(as_text=True)
        assert "موقوف" in html
        hs = c.get(f"/admin/radius/cards/batches/{batch['id']}/cards?status=revoked").get_data(as_text=True)
        assert hs.count("data-card-id=") == 1
    from app.radius.routes.cards import _card_status_meta
    meta = _card_status_meta({"revoked": 1}, datetime.utcnow())
    assert meta["label"] == "موقوف" and meta["key"] == "revoked"
    meta = _card_status_meta({"expire_at": (datetime.utcnow() - timedelta(hours=1)).isoformat()},
                             datetime.utcnow())
    assert meta["label"] == "منتهي"


# ══════════ R05-N5/N6 — import normalises, validates, reports, syncs ══════════

def _import(client, auth, rows, **extra):
    body = {"plan_id": _plan_id(), "cards": rows, "source_type": "imported"}
    body.update(extra)
    return client.post("/api/v1/cards/batches/import", json=body, headers=auth)


def test_import_normalises_like_generation(client, auth):
    px = _px()
    rows = [
        {"username": f"  {px.upper()}UP1 ", "password": " pw1 "},
        {"username": f"{px}ar٣٤", "password": "٥٦٧"},
        {"username": f"{px}ok1", "password": "x"},
    ]
    res = _import(client, auth, rows)
    assert res.status_code == 201, res.get_json()
    names = sorted(c["username"] for c in res.get_json()["data"]["cards"])
    assert names == sorted([f"{px}up1", f"{px}ar34", f"{px}ok1"])
    row = _card_row(f"{px}ar34")
    assert row["password"] == "567"


def test_import_rejects_nul_emoji_html_spaces_with_reasons(client, auth):
    px = _px()
    bad = [f"{px}\x00z", f"{px}\U0001F600", f"{px}<b>x", f"{px} sp", "x" * 70]
    rows = [{"username": u, "password": "p"} for u in bad] + [
        {"username": f"{px}good", "password": "p"}]
    res = _import(client, auth, rows)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert [c["username"] for c in data["cards"]] == [f"{px}good"]
    reasons = {s["reason"] for s in data["skipped"]}
    assert {"invalid_username", "username_too_long"} <= reasons
    assert data["skipped_count"] == 5
    for s in data["skipped"]:
        assert any("؀" <= ch <= "ۿ" for ch in s["message"])


def test_import_reports_in_file_duplicates(client, auth):
    """52 rows with 2 in-file duplicates → 50 inserted, 2 reported (was
    «inserted 50, skipped 0»); case/digit variants count as duplicates."""
    px = _px()
    rows = [{"username": f"{px}{i:03d}", "password": "p"} for i in range(50)]
    rows += [{"username": f"{px}000", "password": "p"},
             {"username": f"{px.upper()}٠٠١", "password": "p"}]
    res = _import(client, auth, rows)
    data = res.get_json()["data"]
    assert data["inserted_count"] == 50
    assert data["skipped_count"] == 2
    dups = [s for s in data["skipped"] if s["reason"] == "duplicate_in_file"]
    assert len(dups) == 2
    assert data["duplicate_in_file"]["count"] == 2


def test_import_without_sync_still_creates_auth_rows(client, auth, app):
    """🔴 R05-N6: the app's sync switch defaults OFF → «available» cards with
    no RADIUS account. The server now always creates them for «imported»."""
    px = _px()
    res = _import(client, auth, [{"username": f"{px}ns1", "password": "p1"}],
                  sync_to_radius=False)
    assert res.status_code == 201
    assert res.get_json()["data"]["radius_sync_enabled"] is True
    row = _db().execute("SELECT user_type, status FROM subscribers WHERE tenant_id=1 "
                        "AND username=?", (f"{px}ns1",)).fetchone()
    assert row is not None and row["user_type"] == "card"
    acc = client.get(f"/api/v1/accounts/{px}ns1", headers=auth)
    assert acc.status_code == 200, acc.get_json()
    with app.app_context():
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=f"{px}ns1", password="p1"))
    assert dec.ok, dec.reason


def test_imported_upper_case_card_still_logs_in_as_printed(client, auth, app):
    px = _px()
    _import(client, auth, [{"username": f"{px.upper()}CAP", "password": "pp"}])
    with app.app_context():
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=f"{px.upper()}CAP", password="pp"))
    assert dec.ok, dec.reason
    assert _card_row(f"{px}cap")["first_used_at"]


def test_web_import_uses_the_same_rules(app):
    px = _px()
    with app.test_client() as c:
        _web_login(c)
        text = "\n".join([f"{px.upper()}W1,p", f"{px}w1,p", f"{px} bad,p", f"{px}w٢,p"])
        r = c.post("/admin/radius/cards/batches/import", data={
            "_csrf_token": "f2-csrf", "plan_id": str(_plan_id()),
            "source_type": "imported", "csv_text": text}, follow_redirects=True)
        html = r.get_data(as_text=True)
    assert "مكرّر داخل الملف" in html
    names = {r["username"] for r in _db().execute(
        "SELECT username FROM cards WHERE username LIKE ?", (px + "%",)).fetchall()}
    assert names == {f"{px}w1", f"{px}w2"}


# ══════════ R13-L1 / R05-N9 — username length ══════════

def test_affixes_longer_than_the_length_are_422(client, auth):
    cases = [
        dict(username_prefix="r05y", username_suffix="z", username_length=4,
             include_batch_number=True),
        dict(username_prefix="111111111111", username_suffix="9",
             username_length=12, include_batch_number=True),
        dict(username_prefix="r05short", username_length=3),
        dict(username_prefix="abcd", username_suffix="@x.y", username_length=8),
    ]
    for body in cases:
        body.update({"plan_id": _plan_id(), "count": 1})
        res = client.post("/api/v1/cards/generate", json=body, headers=auth)
        assert res.status_code == 422, (body, res.get_json())
        msg = res.get_json()["error"]["message"]
        assert "طول اسم المستخدم" in msg and "لا يتّسع" in msg, msg


def test_generated_names_have_exactly_the_chosen_length(client, auth):
    res = client.post("/api/v1/cards/generate", json={
        "plan_id": _plan_id(), "count": 5, "username_prefix": "ab",
        "username_suffix": "z", "username_length": 9,
        "include_batch_number": True}, headers=auth)
    assert res.status_code == 201, res.get_json()
    for c in res.get_json()["data"]["cards"]:
        assert len(c["username"]) == 9


# ══════════ R13-L2 — batch edit: empty name ══════════

def test_batch_edit_empty_name_is_422(client, auth):
    batch, _ = _batch(client, auth, count=1, package_name="named")
    res = client.patch(f"/api/v1/cards/batches/{batch['id']}",
                       json={"package_name": "   "}, headers=auth)
    assert res.status_code == 422
    assert "اسم الحزمة مطلوب" in res.get_json()["error"]["message"]
    row = _db().execute("SELECT package_name FROM card_batches WHERE id=?",
                        (batch["id"],)).fetchone()
    assert row["package_name"] == "named"


def test_web_batch_edit_empty_name_is_rejected(client, auth, app):
    batch, _ = _batch(client, auth, count=1, package_name="named2")
    with app.test_client() as c:
        _web_login(c)
        page = c.get(f"/admin/radius/cards/batches/{batch['id']}/edit").get_data(as_text=True)
        assert 'name="package_name"' in page
        r = c.post(f"/admin/radius/cards/batches/{batch['id']}/edit", data={
            "_csrf_token": "f2-csrf", "package_name": "", "plan_id": str(_plan_id()),
            "status": "active", "price_per_card": "1"})
    # rejected: the form is re-rendered (no redirect) with the Arabic error
    assert r.status_code == 200
    assert "اسم الحزمة مطلوب" in r.get_data(as_text=True)
    row = _db().execute("SELECT package_name FROM card_batches WHERE id=?",
                        (batch["id"],)).fetchone()
    assert row["package_name"] == "named2"


# ══════════ R05-N10 — archived batch: rejected and not `enabled` ══════════

def test_archived_batch_cards_are_rejected_and_disabled_in_accounts(client, auth, app):
    batch, cards = _batch(client, auth, count=2)
    card = cards[0]
    res = client.post("/api/v1/cards/batches/bulk",
                      json={"action": "archive", "batch_ids": [batch["id"]]}, headers=auth)
    assert res.status_code == 200, res.get_json()
    with app.app_context():
        from app.radius.services import policy_engine
        dec = policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"]))
    assert dec.ok is False and dec.reason == "disabled"
    st = _db().execute("SELECT status FROM subscribers WHERE tenant_id=1 AND username=?",
                       (card["username"],)).fetchone()["status"]
    assert st == "disabled"
    # restore brings the accounts back
    res = client.post("/api/v1/cards/batches/bulk",
                      json={"action": "restore", "batch_ids": [batch["id"]]}, headers=auth)
    assert res.status_code == 200, res.get_json()
    st = _db().execute("SELECT status FROM subscribers WHERE tenant_id=1 AND username=?",
                       (card["username"],)).fetchone()["status"]
    assert st == "enabled"
    with app.app_context():
        from app.radius.services import policy_engine
        assert policy_engine.authorize(policy_engine.AuthRequest(
            tenant_id=1, username=card["username"], password=card["password"])).ok


# ══════════ R13-L3 — web generator: back button + confirmation ══════════

def test_web_generator_form_resets_on_back_and_confirms(app):
    with app.test_client() as c:
        _web_login(c)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
    assert "data-confirm-generate" in html
    assert 'autocomplete="off"' in html.split("data-card-generate-form")[1][:200]
    assert "data-generate-back-notice" in html
    import pathlib
    js = pathlib.Path("app/static/js/cards_generate_progress.js").read_text(encoding="utf-8")
    assert "pageshow" in js and "back_forward" in js and "form.reset()" in js
    assert "confirmGenerate" in js


# ══════════ R06-N5 — batch payload carries its currency ══════════

def test_batch_payload_has_currency(client, auth):
    batch, _ = _batch(client, auth, count=1, price_per_card=5)
    assert batch["currency"] == "ILS"
    res = client.get(f"/api/v1/cards/batches/{batch['id']}", headers=auth)
    assert res.get_json()["data"]["currency"] == "ILS"
