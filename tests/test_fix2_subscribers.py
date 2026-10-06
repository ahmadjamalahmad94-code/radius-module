"""Fix wave 2 — `subscribers` stream (re-test R01 / R08 / R10 / R12, 2026-09-29).

One test (or a few) per item: lost updates on edit (API, web, service, with
threads on separate connections), the web edit form keeping a renewal and the
seconds, extreme expiry dates, the one-year expiry cap, archived names, rename
case-duplicates, «بدون انتهاء», blank web passwords, input validation, the
manual-balance ledger row, the R08 guards, the full-list export and the
performance changes (no CoA on non-rate edits, side effects off the request).

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix2-subs-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fix2subs.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix2-subs-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_f2")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-f2")
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

def _db():
    from app.radius.db.connection import db
    return db()


def _plan(*, price=30.0, days=30) -> int:
    now = datetime.utcnow().isoformat()
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        ("p_" + uuid4().hex[:6], days * 1440, days, price, "ILS", now, now))
    return int(cur.lastrowid)


def _sub(username=None, *, expire_at=None, balance=0.0, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "u_" + uuid4().hex[:8]
    exp = expire_at if expire_at is not None else datetime.utcnow() + timedelta(days=10)
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret1",
        status="enabled", expire_at=exp, full_name="Test User", **kw))
    if balance:
        _db().execute("UPDATE subscribers SET balance=? WHERE tenant_id=1 AND username=?",
                      (float(balance), username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username, include_deleted=False):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username, include_deleted=include_deleted)


def _svc():
    from app.radius.services.users import get_users_service
    return get_users_service()


def _err(res, status):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, body)
    assert body["ok"] is False
    return body["error"]


def _web_login(client, user="owner_f2", pw="owner-pass-f2"):
    res = client.post("/admin/radius/login", data={"username": user, "password": pw})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _edit_form(client, username):
    """The edit page's form fields as the browser would post them back."""
    html = client.get(f"/admin/radius/users/{username}/edit").get_data(as_text=True)
    fields = {}
    for name in ("expire_year", "expire_month", "expire_day", "expire_orig",
                 "full_name", "father_name", "mobile", "email", "remark", "status"):
        m = re.search(r'name="%s"[^>]*value="([^"]*)"' % name, html)
        if m:
            fields[name] = m.group(1)
    m = re.search(r'name="expire_time"[^>]*\n?[^>]*value="([^"]*)"', html)
    if m:
        fields["expire_time"] = m.group(1)
    fields.setdefault("status", "enabled")
    fields["service_type"] = "hotspot"
    fields["_csrf_token"] = _csrf(client)
    return html, fields


# ═══════════ 1. lost updates on edit ═══════════

def test_update_with_base_keeps_a_concurrent_renewal_and_topup(app):
    s = _sub(balance=10)
    base = _get(s.username)
    # another request renews and tops up AFTER this caller loaded the row
    _svc().extend_time(actor="t", username=s.username, minutes=60)
    _svc().add_cash_balance(actor="t", username=s.username, amount=5)
    after_other = _get(s.username)
    _svc().update(actor="t", sub=replace(base, remark="note"), base=base)
    final = _get(s.username)
    assert final.remark == "note"
    assert final.expire_at == after_other.expire_at          # +60 min kept
    assert float(final.balance) == pytest.approx(15.0)       # +5 kept


def test_upsert_only_fields_writes_only_those_columns(app):
    from app.radius.db.repos import subscribers_repo
    s = _sub(balance=3)
    _db().execute("UPDATE subscribers SET balance=99 WHERE username=?", (s.username,))
    subscribers_repo.upsert_subscriber(replace(s, remark="x"), only_fields={"remark"})
    row = _get(s.username)
    assert row.remark == "x" and float(row.balance) == 99.0


def test_api_patch_does_not_undo_concurrent_extend(client, app, monkeypatch):
    s = _sub()
    import app.api.v1.accounts as acc
    real_get = acc._svc().__class__.get
    calls = {"n": 0}

    def get_then_extend(self, username):
        row = real_get(self, username)
        calls["n"] += 1
        if calls["n"] == 1:   # the PATCH read happened; a renewal commits now
            _svc().extend_time(actor="other", username=username, minutes=60)
        return row

    monkeypatch.setattr(acc._svc().__class__, "get", get_then_extend)
    before = _get(s.username).expire_at
    res = client.patch(f"/api/v1/accounts/{s.username}", json={"remark": "r"}, headers=AUTH)
    assert res.status_code == 200, res.get_json()
    final = _get(s.username)
    assert final.remark == "r"
    assert final.expire_at == before + timedelta(minutes=60)


def test_threads_on_separate_connections_lose_nothing(app):
    """8 writers (own SQLite connections, like 2 gunicorn workers): 20
    remark PATCH-style updates with a stale base interleaved with 20 extends
    and 20 top-ups — every +60 min and every +5 lands."""
    s = _sub()
    start_exp = _get(s.username).expire_at
    errors = []

    def patcher(i):
        try:
            with app.app_context():
                base = _get(s.username)
                _svc().update(actor="p", sub=replace(base, remark=f"r{i}"), base=base)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def extender():
        try:
            with app.app_context():
                _svc().extend_time(actor="e", username=s.username, minutes=60)
                _svc().add_cash_balance(actor="e", username=s.username, amount=5)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = []
    for i in range(20):
        threads.append(threading.Thread(target=patcher, args=(i,)))
        threads.append(threading.Thread(target=extender))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    final = _get(s.username)
    assert final.expire_at == start_exp + timedelta(minutes=20 * 60)
    assert float(final.balance) == pytest.approx(100.0)


def test_disable_writes_status_only(app):
    s = _sub()
    base_exp = s.expire_at
    from app.radius.db.repos import subscribers_repo
    real = subscribers_repo.upsert_subscriber
    seen = {}

    def spy(sub, *, only_fields=None):
        seen["only"] = only_fields
        return real(sub, only_fields=only_fields)

    import app.radius.integration.sqlite_adapter as sa
    orig = sa.subscribers_repo.upsert_subscriber
    sa.subscribers_repo.upsert_subscriber = spy
    try:
        _svc().disable(actor="t", username=s.username)
    finally:
        sa.subscribers_repo.upsert_subscriber = orig
    assert seen["only"] == {"status"}
    assert _get(s.username).status == "disabled"
    assert _get(s.username).expire_at == base_exp


# ═══════════ 2. web edit form: renewal kept, seconds kept ═══════════

def test_web_edit_keeps_renewal_done_after_page_open(client, app):
    s = _sub(expire_at=datetime(2026, 11, 12, 4, 43, 27))
    _web_login(client)
    _html, form = _edit_form(client, s.username)
    assert form.get("expire_orig"), "hidden original expiry missing"
    _svc().extend_time(actor="other", username=s.username, minutes=1440)
    renewed = _get(s.username).expire_at
    form["remark"] = "only a note"
    res = client.post(f"/admin/radius/users/{s.username}", data=form)
    assert res.status_code in (302, 303), res.status_code
    final = _get(s.username)
    assert final.remark == "only a note"
    assert final.expire_at == renewed           # not reverted, seconds intact


def test_web_edit_date_change_keeps_seconds_when_time_unchanged(client, app):
    s = _sub(expire_at=datetime.utcnow().replace(microsecond=0, second=27) + timedelta(days=5))
    _web_login(client)
    _html, form = _edit_form(client, s.username)
    d = datetime(int(form["expire_year"]), int(form["expire_month"]),
                 int(form["expire_day"])) + timedelta(days=1)
    form.update(expire_year=str(d.year), expire_month=str(d.month), expire_day=str(d.day))
    res = client.post(f"/admin/radius/users/{s.username}", data=form)
    assert res.status_code in (302, 303)
    final = _get(s.username)
    assert final.expire_at.second == 27
    assert final.expire_at - s.expire_at == timedelta(days=1)


def test_web_save_does_not_fill_father_name(client, app):
    s = _sub()
    _web_login(client)
    html, form = _edit_form(client, s.username)
    assert "data-father-name-field" not in html
    form["full_name"] = "احمد محمد الاحمد"
    client.post(f"/admin/radius/users/{s.username}", data=form)
    assert _get(s.username).father_name == ""


# ═══════════ 3. extreme dates ═══════════

@pytest.mark.parametrize("bad", ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z",
                                 "2101-01-01T00:00:00Z", "tomorrow", "2026-13-45",
                                 "2026-02-30T10:00:00Z"])
def test_api_rejects_out_of_range_or_invalid_expiry(client, app, bad):
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "x" + uuid4().hex[:6], "password": "pass1", "expire_at": bad})
    err = _err(res, 422)
    assert re.search(r"[؀-ۿ]", err["message"])
    s = _sub()
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"expire_at": bad})
    _err(res, 422)


def test_web_list_survives_extreme_dates(client, app):
    for name, exp in (("old1", "0001-01-01T00:00:00Z"), ("far1", "9999-12-31T23:59:59Z")):
        _db().execute(
            "INSERT INTO subscribers(tenant_id,username,password,user_type,status,expire_at,created_at) "
            "VALUES(1,?,?,?,?,?,?)", (name, "pw", "subscriber", "enabled", exp, "2026-01-01T00:00:00Z"))
    _web_login(client)
    res = client.get("/admin/radius/users")
    assert res.status_code == 200
    assert "far1" in res.get_data(as_text=True)


def test_web_edit_rejects_expiry_beyond_2100(client, app):
    s = _sub()
    _web_login(client)
    _html, form = _edit_form(client, s.username)
    form.update(expire_year="2150", expire_month="1", expire_day="1")
    res = client.post(f"/admin/radius/users/{s.username}", data=form)
    assert res.status_code == 422
    assert _get(s.username).expire_at == s.expire_at


def test_one_year_cap_on_edit_and_set_expiry(client, app):
    from app.radius.core.errors import RadiusValidationError
    s = _sub()
    far = (datetime.utcnow() + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    err = _err(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                            json={"expire_at": far}), 422)
    assert "سنة" in err["message"]
    ok_date = (s.expire_at + timedelta(days=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                        json={"expire_at": ok_date}).status_code == 200
    with pytest.raises(RadiusValidationError):
        _svc().set_expiry(actor="t", username=s.username,
                          expire_at=datetime.utcnow() + timedelta(days=700))
    # shortening is always allowed
    assert client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={
        "expire_at": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    }).status_code == 200


def test_legacy_extend_time_caps_and_never_500(client, app):
    s = _sub()
    _err(client.post(f"/api/v1/accounts/{s.username}/extend_time", headers=AUTH,
                     json={"minutes": 10**12}), 422)
    _err(client.post(f"/api/v1/accounts/{s.username}/extend_time", headers=AUTH,
                     json={"minutes": 1.9}), 422)
    assert client.post(f"/api/v1/accounts/{s.username}/extend_time", headers=AUTH,
                       json={"minutes": 60}).status_code == 200


# ═══════════ 4. archived name ═══════════

def test_recreating_an_archived_name_is_refused_and_archive_untouched(client, app):
    s = _sub()
    client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH, json={"amount": 25})
    assert client.delete(f"/api/v1/accounts/{s.username}", headers=AUTH).status_code == 200
    for name in (s.username, s.username.upper()):
        err = _err(client.post("/api/v1/accounts", headers=AUTH, json={
            "username": name, "password": "pass1", "full_name": "New"}), 409)
        assert "مؤرشف" in err["message"]
    arch = _get(s.username, include_deleted=True)
    assert arch.id == s.id and arch.deleted_at is not None
    assert arch.full_name == "Test User"


def test_rename_onto_archived_name_is_409_not_500(client, app):
    a = _sub()
    b = _sub()
    client.delete(f"/api/v1/accounts/{a.username}", headers=AUTH)
    res = client.post(f"/api/v1/accounts/{b.username}/rename", headers=AUTH,
                      json={"new_username": a.username})
    _err(res, 409)


# ═══════════ 5. rename case-duplicates ═══════════

def test_rename_case_duplicate_refused_on_api_and_web(client, app):
    a = _sub("r10_001")
    b = _sub("r10_016")
    _err(client.post(f"/api/v1/accounts/{b.username}/rename", headers=AUTH,
                     json={"new_username": "R10_001"}), 422)
    assert _get("r10_016") is not None
    _web_login(client)
    _html, form = _edit_form(client, b.username)
    form["username"] = "R10_001"
    client.post(f"/admin/radius/users/{b.username}", data=form)
    assert _get("R10_001") is None and _get("r10_016") is not None
    # a case-only rename of its OWN name is allowed
    res = client.post(f"/api/v1/accounts/{a.username}/rename", headers=AUTH,
                      json={"new_username": "R10_001"})
    assert res.status_code == 200, res.get_json()


def test_new_usernames_need_three_chars_but_legacy_short_names_stay_editable(client, app):
    _err(client.post("/api/v1/accounts", headers=AUTH,
                     json={"username": "ab", "password": "pass1"}), 422)
    legacy = _sub("q8")
    res = client.patch("/api/v1/accounts/q8", headers=AUTH, json={"remark": "ok"})
    assert res.status_code == 200
    assert _get(legacy.username).remark == "ok"


# ═══════════ 6. «بدون انتهاء» ═══════════

def test_explicit_null_clears_expiry_missing_key_keeps_it(client, app):
    s = _sub()
    assert client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                        json={"remark": "no expiry key"}).status_code == 200
    assert _get(s.username).expire_at == s.expire_at
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={"expire_at": None})
    assert res.status_code == 200
    assert res.get_json()["data"]["expire_at"] is None
    assert _get(s.username).expire_at is None


def test_web_no_expiry_checkbox_clears_and_blank_create_is_born_expired(client, app):
    s = _sub()
    _web_login(client)
    _html, form = _edit_form(client, s.username)
    form["no_expiry"] = "1"
    client.post(f"/admin/radius/users/{s.username}", data=form)
    assert _get(s.username).expire_at is None
    pid = _plan()
    base = {"_csrf_token": _csrf(client), "password": "pass1", "plan_id": str(pid),
            "service_type": "hotspot", "status": "enabled"}
    client.post("/admin/radius/users", data={**base, "username": "webnoexp"})
    assert _get("webnoexp").expire_at <= datetime.utcnow()      # fail-closed kept
    client.post("/admin/radius/users", data={**base, "username": "webunlim", "no_expiry": "1"})
    assert _get("webunlim").expire_at is None


# ═══════════ 7. web create password of spaces ═══════════

def test_web_create_space_password_is_422(client, app):
    _web_login(client)
    res = client.post("/admin/radius/users", data={
        "_csrf_token": _csrf(client), "username": "wpw2", "password": "    ",
        "service_type": "hotspot", "status": "enabled"})
    assert res.status_code == 422
    assert _get("wpw2") is None


# ═══════════ 8. validation ═══════════

@pytest.mark.parametrize("body", [
    {"status": "weird"}, {"service_type": "XYZ"}, {"static_ip": "999.1.1.1"},
    {"email": "not-an-email"}, {"mobile": "abc"}, {"mac_lock": "zz:zz"},
    {"download_speed_kbps": -5}, {"device_count": -3},
    {"download_speed_kbps": 1e30}, {"plan_id": 99999},
    {"plan_id": -1}, {"manager_id": -1}, {"manager_id": 99999},
    {"full_name": {"a": 1}}, {"full_name": [1]}, {"remark": {"a": 1}},
    {"auto_renewal": "maybe"},
])
def test_bad_inputs_are_422_on_create_and_patch(client, app, body):
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "v" + uuid4().hex[:6], "password": "pass1", **body})
    _err(res, 422)
    s = _sub()
    _err(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json=body), 422)


def test_manager_can_be_unassigned_and_plan_cleared(client, app):
    s = _sub()
    for body in ({"manager_id": None}, {"manager_id": 0}, {"plan_id": None}):
        res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json=body)
        assert res.status_code == 200, (body, res.get_json())


def test_legacy_invalid_values_do_not_block_other_edits(client, app):
    s = _sub(email="old-bad-email")
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={"remark": "x"})
    assert res.status_code == 200


def test_arabic_digit_search_and_web_plan_id_abc(client, app):
    _sub(mobile="0599000123")
    res = client.get("/api/v1/accounts?q=٠٥٩٩٠٠٠١٢٣", headers=AUTH)
    assert res.get_json()["data"]["total"] == 1
    _web_login(client)
    assert client.get("/admin/radius/users?plan_id=abc").status_code == 200
    body = client.get("/admin/radius/users?q=٠٥٩٩٠٠٠١٢٣").get_data(as_text=True)
    assert "0599000123" in body


def test_patch_rejects_username_change_and_non_object_body(client, app):
    s = _sub()
    _err(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                      json={"username": "other_name"}), 422)
    _err(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json=[1]), 422)


# ═══════════ 9. manual balance ledger ═══════════

def test_owner_balance_patch_writes_ledger_row(client, app):
    s = _sub(balance=25)
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={"balance": 475})
    assert res.status_code == 200
    row = _db().execute(
        "SELECT direction, amount, notes FROM accounting_ledger_entries "
        "WHERE username=? AND source_type='subscriber_manual_balance'", (s.username,)).fetchone()
    assert row is not None
    assert row["direction"] == "credit" and float(row["amount"]) == 450.0
    assert row["notes"] == "تعديل رصيد يدوي"
    client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={"balance": 400})
    n = _db().execute("SELECT COUNT(*) FROM accounting_ledger_entries WHERE username=? "
                      "AND source_type='subscriber_manual_balance' AND direction='debit'",
                      (s.username,)).fetchone()[0]
    assert n == 1


def test_create_with_opening_balance_writes_ledger_row(client, app):
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "openbal", "password": "pass1", "balance": -50})
    assert res.status_code == 201
    row = _db().execute("SELECT direction, amount FROM accounting_ledger_entries "
                        "WHERE username='openbal'").fetchone()
    assert row["direction"] == "debit" and float(row["amount"]) == 50.0


# ═══════════ 10. R08 guards ═══════════

def test_dashboard_only_viewer_cannot_open_users_list(client, app):
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(name="dash_only_" + uuid4().hex[:4],
                                   permissions=("dashboard.view",))
    admins_repo.create_admin(username="viewer_f2", password="viewer-pass",
                             role_id=role.id)
    _web_login(client, "viewer_f2", "viewer-pass")
    for path in ("/admin/radius/users", "/admin/radius/subscribers",
                 "/admin/radius/users/export?fmt=csv"):
        assert client.get(path).status_code == 403, path


def _admin_token(client):
    res = client.post("/api/admin/login",
                      json={"username": "owner_f2", "password": "owner-pass-f2"})
    return {"Authorization": "Bearer " + res.get_json()["data"]["token"]}


def test_admin_patch_role_id_validated(client, app):
    from app.radius.db.repos import admins_repo
    target = admins_repo.create_admin(username="mgr_" + uuid4().hex[:4], password="xpass-123",
                                      role_id=admins_repo.least_privileged_role_id())
    h = _admin_token(client)
    for bad in (9999, 0, None, ""):
        res = client.patch(f"/api/v1/admins/{target.id}", headers=h, json={"role_id": bad})
        assert res.status_code == 422, (bad, res.status_code, res.get_json())
    assert admins_repo.get_admin(target.id).role_id == target.role_id


def test_same_password_change_is_422(client, app):
    h = _admin_token(client)
    res = client.post("/api/admin/password", headers=h, json={
        "current_password": "owner-pass-f2", "new_password": "owner-pass-f2",
        "confirm_password": "owner-pass-f2"})
    assert res.status_code == 422
    assert "تختلف" in res.get_json()["error"]["message"]


@pytest.mark.parametrize("method,path", [
    ("patch", "/api/v1/settings"), ("put", "/api/v1/webhooks/config"),
    ("patch", "/api/v1/payments/settings"), ("post", "/api/v1/vouchers"),
    ("post", "/api/v1/invoices"),
])
def test_array_bodies_are_422(client, app, method, path):
    res = getattr(client, method)(path, headers=AUTH, json=[1])
    assert res.status_code == 422, (path, res.status_code, res.get_data(as_text=True)[:200])


# ═══════════ 11. export all rows ═══════════

def test_web_export_contains_all_matching_rows(client, app):
    for i in range(30):
        _sub(f"exp_{i:03d}")
    _sub("other_1")
    _web_login(client)
    res = client.get("/admin/radius/users/export?fmt=csv&q=exp_&page_size=10")
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 31          # header + 30 rows (not the 10 of a page)
    assert "other_1" not in text
    assert client.get("/admin/radius/users/export?fmt=xlsx&q=exp_").status_code == 200


def test_bulk_delete_button_says_archive(client, app):
    _web_login(client)
    html = client.get("/admin/radius/users").get_data(as_text=True)
    assert "حذف نهائي للتحديد" not in html
    assert "أرشفة المحدَّدين" in html


# ═══════════ 12. performance ═══════════

def test_no_coa_push_when_rate_fields_unchanged(app, monkeypatch):
    import app.radius.integration.sqlite_adapter as sa
    calls = []
    monkeypatch.setattr(sa, "_push_coa_rate_if_active", lambda sub: calls.append(sub.username))
    s = _sub()
    _svc().add_cash_balance(actor="t", username=s.username, amount=5)
    base = _get(s.username)
    _svc().update(actor="t", sub=replace(base, remark="n"), base=base)
    assert calls == []
    pid = _plan()
    base = _get(s.username)
    _svc().update(actor="t", sub=replace(base, plan_id=pid), base=base)
    assert calls == [s.username]


def test_side_effects_run_on_pool_outside_tests(app, monkeypatch):
    from app.radius.core import background
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HOBERADIUS_SYNC_SIDE_EFFECTS", raising=False)
    main = threading.current_thread().name
    done = threading.Event()
    seen = {}

    def fn():
        seen["thread"] = threading.current_thread().name
        done.set()

    background.run_detached(fn, name="t")
    assert done.wait(5)
    assert seen["thread"] != main and seen["thread"].startswith("side-effect")


def test_live_usernames_prefilter_keeps_semantics(app):
    from app.radius.services.live_sessions import live_usernames
    now = datetime.utcnow()
    rows = [
        ("fresh_space", (now - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"), None),
        ("fresh_iso", (now - timedelta(minutes=3)).isoformat() + "Z", None),
        ("stale", (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"), None),
        ("stale_upd", (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
         (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")),
        ("fresh_upd", (now - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),
         (now - timedelta(minutes=1)).isoformat() + "Z"),
    ]
    for i, (u, start, upd) in enumerate(rows):
        _db().execute("INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                      "acctstarttime, acctupdatetime) VALUES(1,?,?,?,?,?)",
                      (f"s{i}", u, "10.0.0.1", start, upd))
    assert live_usernames(1) == {"fresh_space", "fresh_iso", "fresh_upd"}
