"""Fix wave 2 (re-test R04 / R12) — plans, change-plan and API 404/405.

Pins: strict booleans on /profiles, negative / oversized ints, HH:MM hours,
speed_unlimited, JSON 404 for unmatched /api/ paths, plan-name length cap,
case-insensitive uniqueness on restore, the web plans page weight, the web
plan form refusing unparsable numbers, and the change-plan rules (per-minute
direction, same / disabled plan, free current plan, no seconds shaved,
no-duration plans priced as a month, «0 يوم» flash, Arabic web messages).

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix2-plans-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "plans.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix2-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_fix2")
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

def _db():
    from app.radius.db.connection import db
    return db()


def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_fix2", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status, code=None):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, res.data[:300])
    assert body["ok"] is False, body
    if code:
        assert body["error"]["code"] == code, body
    return body["error"]


def _plan(name=None, *, price=30.0, minutes=30 * 1440, validity=0, enabled=True,
          currency="ILS", quota_mb=0) -> int:
    now = datetime.utcnow().isoformat()
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, price, "
        "currency, quota_total_mb, speed_down_kbps, speed_up_kbps, enabled, created_at, "
        "updated_at) VALUES(1,?,?,?,?,?,?,4096,1024,?,?,?)",
        (name or "p_" + uuid4().hex[:6], minutes, validity, price, currency, quota_mb,
         1 if enabled else 0, now, now))
    return int(cur.lastrowid)


def _sub(username=None, *, plan_id, expire_at=None, balance=0.0):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "u_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Plan User", mobile="0599000000", status="enabled",
        expire_at=expire_at or (datetime.utcnow() + timedelta(days=10))))
    if balance:
        _db().execute("UPDATE subscribers SET balance=? WHERE username=?", (balance, username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _create(client, **body):
    base = {"name": "api_" + uuid4().hex[:6], "speed_down_kbps": 2048, "speed_up_kbps": 512,
            "duration_minutes": 1440, "price": 5}
    base.update(body)
    return client.post("/api/v1/profiles", headers=AUTH, json=base)


# ─────────────── 1. /profiles input rules ───────────────

@pytest.mark.parametrize("raw", ["false", "0", "no", "off", 0, False])
def test_profiles_false_strings_are_stored_false(client, raw):
    d = _data(_create(client, enabled=raw, bind_mac=raw), 201)
    assert d["enabled"] is False and d["bind_mac"] is False
    d2 = _data(client.patch(f"/api/v1/profiles/{d['id']}", headers=AUTH,
                            json={"enabled": "true"}))
    assert d2["enabled"] is True


def test_profiles_bool_garbage_is_422(client):
    err = _err(_create(client, enabled="maybe"), 422, "validation_error")
    assert "true" in err["message"]


@pytest.mark.parametrize("field", [
    "allowed_devices_count", "session_timeout_sec", "idle_timeout_sec", "priority",
    "vlan_id", "burst_time_sec", "max_daily_minutes", "concurrent_sessions"])
def test_profiles_negative_ints_are_refused(client, field):
    err = _err(_create(client, **{field: -5}), 422, "validation_error")
    assert "سالب" in err["message"] or "1 على الأقل" in err["message"]


@pytest.mark.parametrize("field,value", [
    ("duration_minutes", 10 ** 20), ("vlan_id", 10 ** 20), ("quota_total_mb", 2 ** 63),
    ("vlan_id", 5000), ("priority", 10 ** 6)])
def test_profiles_huge_ints_are_422_not_500(client, field, value):
    err = _err(_create(client, **{field: value}), 422, "validation_error")
    assert "أكبر من المسموح" in err["message"]
    pid = _data(_create(client), 201)["id"]
    _err(client.patch(f"/api/v1/profiles/{pid}", headers=AUTH, json={field: value}), 422)


@pytest.mark.parametrize("value", [True, 90.7, "abc"])
def test_profiles_non_integer_ints_are_refused(client, value):
    err = _err(_create(client, duration_minutes=value), 422, "validation_error")
    assert "duration_minutes" not in err["message"]  # Arabic label, not the raw key


@pytest.mark.parametrize("field,value", [
    ("allowed_hours_from", "25:99"), ("allowed_hours_to", "abc"),
    ("offer_hours_from", "99:99"), ("offer_hours_to", "7")])
def test_profiles_bad_hours_are_refused(client, field, value):
    err = _err(_create(client, **{field: value}), 422, "validation_error")
    assert "HH:MM" in err["message"]


def test_profiles_good_hours_are_accepted(client):
    d = _data(_create(client, allowed_hours_from="08:30", allowed_hours_to="24:00",
                      offer_hours_from="7:00", offer_hours_to="23:59"), 201)
    assert d["allowed_hours_from"] == "08:30" and d["allowed_hours_to"] == "24:00"


def test_profiles_accept_speed_unlimited(client):
    _err(_create(client, speed_down_kbps=0, speed_up_kbps=0), 422)
    d = _data(_create(client, speed_down_kbps=0, speed_up_kbps=0, speed_unlimited=True), 201)
    assert d["speed_unlimited"] is True
    d = _data(client.patch(f"/api/v1/profiles/{d['id']}", headers=AUTH,
                           json={"speed_unlimited": "false", "speed_down_kbps": 1024,
                                 "speed_up_kbps": 512}))
    assert d["speed_unlimited"] is False


def test_profiles_serialise_rate_per_minute(client):
    d = _data(_create(client, price=70, duration_minutes=30 * 1440), 201)
    assert d["period_minutes"] == 43200
    assert d["rate_per_minute"] == pytest.approx(70 / 43200, rel=1e-6)
    nodur = _data(_create(client, price=10, duration_minutes=0), 201)
    assert nodur["period_minutes"] == 43200  # same 30-day basis as payments


def test_profiles_name_length_is_capped(client):
    err = _err(_create(client, name="x" * 101), 422, "validation_error")
    assert "100" in err["message"]
    _data(_create(client, name="y" * 100), 201)
    pid = _data(_create(client), 201)["id"]
    _err(client.patch(f"/api/v1/profiles/{pid}", headers=AUTH, json={"name": "z" * 3015}), 422)


def test_unmatched_api_paths_are_json_404(client):
    for path in ("/api/v1/profiles/abc", "/api/v1/reports/nope", "/api/v1/nosuch"):
        for method in ("get", "patch", "delete", "post"):
            res = getattr(client, method)(path, headers=AUTH)
            assert res.status_code == 404, (path, method, res.status_code)
            assert res.is_json and res.get_json()["error"]["code"] == "not_found"


def test_wrong_method_on_real_api_path_is_json_405(client):
    res = client.put("/api/v1/profiles", headers=AUTH, json={})
    assert res.status_code == 405 and res.is_json
    assert res.get_json()["error"]["code"] == "method_not_allowed"


def test_actions_context_debt_is_never_negative_zero(client):
    s = _sub(plan_id=_plan())
    d = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert d["debt"] == 0 and str(d["debt"]) == "0.0"


# ─────────────── 1b. web plan form ───────────────

def _web_plan_form(**over):
    form = {"name": "web_" + uuid4().hex[:6], "plan_type": "time", "service_type": "hotspot",
            "duration_minutes": "1440", "speed_down_kbps": "2048", "speed_up_kbps": "512",
            "price": "5", "enabled": "1", "concurrent_sessions": "1"}
    form.update(over)
    return form


@pytest.mark.parametrize("price", ["abc", "inf", "nan"])
def test_web_plan_form_refuses_unparsable_price(client, price):
    csrf = _web_login(client)
    form = _web_plan_form(price=price)
    res = client.post("/admin/radius/plans", data={"_csrf_token": csrf, **form})
    assert res.status_code in (302, 303, 400)
    assert _db().execute("SELECT COUNT(*) FROM access_plans WHERE name=?",
                         (form["name"],)).fetchone()[0] == 0


def test_web_plan_form_reads_arabic_decimal_price(client):
    csrf = _web_login(client)
    form = _web_plan_form(price="١٢٫٥")
    client.post("/admin/radius/plans", data={"_csrf_token": csrf, **form})
    row = _db().execute("SELECT price FROM access_plans WHERE name=?", (form["name"],)).fetchone()
    assert row and float(row["price"]) == 12.5


def test_web_plan_form_refuses_negative_and_bad_hours(client):
    csrf = _web_login(client)
    for over in ({"vlan_id": "-3"}, {"allowed_hours_from": "25:99"}, {"name": "n" * 150}):
        form = _web_plan_form(**over)
        res = client.post("/admin/radius/plans", data={"_csrf_token": csrf, **form})
        assert res.status_code == 400, over
        assert _db().execute("SELECT COUNT(*) FROM access_plans WHERE name=?",
                             (form["name"],)).fetchone()[0] == 0


# ─────────────── 4. restore uniqueness + page weight ───────────────

def test_restore_cannot_create_case_duplicate_active_plans(client):
    a = _data(_create(client, name="r04_CaseRestore"), 201)["id"]
    _data(client.delete(f"/api/v1/profiles/{a}", headers=AUTH))
    b = _data(_create(client, name="R04_caserestore"), 201)["id"]
    _data(client.post(f"/api/v1/recycle-bin/plans/{a}/restore", headers=AUTH))
    names = [r["name"] for r in _db().execute(
        "SELECT name FROM access_plans WHERE deleted_at IS NULL AND lower(name) LIKE 'r04_caserestore%'")]
    assert len(names) == 2 and len({n.lower() for n in names}) == 2
    restored = _data(client.get(f"/api/v1/profiles/{a}", headers=AUTH))
    assert restored["name"].startswith("r04_CaseRestore (مستعادة")
    # the restored plan is editable again (it used to fail «الاسم مستخدم مسبقًا»)
    _data(client.patch(f"/api/v1/profiles/{a}", headers=AUTH, json={"price": 2}))
    assert _data(client.get(f"/api/v1/profiles/{b}", headers=AUTH))["name"] == "R04_caserestore"


def test_web_restore_also_renames_case_duplicate(client):
    csrf = _web_login(client)
    a = _plan("Web_Restore")
    _db().execute("UPDATE access_plans SET deleted_at='2026-09-01T00:00:00' WHERE id=?", (a,))
    _plan("web_restore")
    client.post(f"/admin/radius/recycle-bin/plans/{a}/restore", data={"_csrf_token": csrf})
    row = _db().execute("SELECT name, deleted_at FROM access_plans WHERE id=?", (a,)).fetchone()
    assert row["deleted_at"] is None and "مستعادة" in row["name"]


def test_web_plans_page_is_slim_and_fragments_load(client):
    for i in range(20):
        _plan(f"heavy plan {i}")
    _web_login(client)
    size20 = len(client.get("/admin/radius/plans").data)
    for i in range(20, 40):
        _plan(f"heavy plan {i}")
    html = client.get("/admin/radius/plans").get_data(as_text=True)
    import re
    assert not re.search(r'id="pl-detail-\d', html)
    assert not re.search(r'data-pl-detail[ >]', html)
    assert '<article class="pl-card"' not in html
    per_plan = (len(html.encode()) - size20) / 20
    assert per_plan < 5000, per_plan  # was ~16 KB per plan (1.8 MB / 115)
    cards = client.get("/admin/radius/plans?fragment=cards")
    assert cards.status_code == 200 and cards.data.count(b'<article class="pl-card"') == 40
    pid = _plan("detail me")
    det = client.get(f"/admin/radius/plans?fragment=detail&id={pid}")
    assert det.status_code == 200 and b"data-pl-detail" in det.data and "detail me".encode() in det.data
    assert client.get("/admin/radius/plans?fragment=detail&id=999999").status_code == 404


# ─────────────── 2. change-plan rules ───────────────

def _cp(client, username, plan_id, policy):
    return client.post(f"/api/v1/accounts/{username}/change-plan", headers=AUTH,
                       json={"plan_id": plan_id, "policy": policy})


def test_neutral_change_with_a_price_difference_is_refused(client):
    daily5 = _plan(price=5, minutes=1440)
    monthly100 = _plan(price=100, minutes=30 * 1440)   # 3.33/day < 5/day → lower
    s = _sub(plan_id=daily5)
    err = _err(_cp(client, s.username, monthly100, "neutral_keep_expiry"), 422)
    assert "الأرخص" in err["message"] or "أرخص" in err["message"]
    assert _get(s.username).plan_id == daily5
    same_rate = _plan(price=150, minutes=30 * 1440)    # 5/day == 5/day → neutral
    _data(_cp(client, s.username, same_rate, "neutral_keep_expiry"))


def test_same_plan_and_disabled_plan_are_refused(client):
    pid = _plan(price=30)
    off = _plan(price=60, enabled=False)
    s = _sub(plan_id=pid)
    err = _err(_cp(client, s.username, pid, "neutral_keep_expiry"), 422)
    assert "الحاليّ" in err["message"]
    err = _err(_cp(client, s.username, off, "higher_keep_expiry"), 422)
    assert "معطّل" in err["message"]
    archived = _plan(price=60)
    _db().execute("UPDATE access_plans SET deleted_at='2026-09-01T00:00:00' WHERE id=?", (archived,))
    _err(_cp(client, s.username, archived, "higher_keep_expiry"), 404)


def test_free_current_plan_still_checks_direction(client):
    free = _plan(price=0)
    pricey = _plan(price=999999.99)
    s = _sub(plan_id=free)
    _err(_cp(client, s.username, pricey, "lower_keep_expiry"), 422)
    _err(_cp(client, s.username, pricey, "neutral_keep_expiry"), 422)
    d = _data(_cp(client, s.username, pricey, "higher_keep_expiry"))
    assert d["direction"] == "higher"


def test_direction_is_by_rate_not_total_price(client):
    """70 / 30 days → 5 / 1 day is DEARER per day (R12 N3)."""
    fam = _plan(price=70, minutes=30 * 1440)
    day = _plan(price=5, minutes=1440)
    s = _sub(plan_id=fam, expire_at=datetime.utcnow() + timedelta(days=10, seconds=30))
    _err(_cp(client, s.username, day, "lower_compensate"), 422)
    d = _data(_cp(client, s.username, day, "higher_debt"))
    assert d["direction"] == "higher"
    remaining = 10 * 1440
    expected = round((5 / 1440 - 70 / 43200) * remaining, 2)
    assert d["debt_amount"] == pytest.approx(expected, abs=0.02)


def test_zero_delta_compensation_keeps_the_seconds(client):
    old = _plan(price=5, minutes=1440)
    new = _plan(price=35, minutes=7 * 1440)   # identical per-day rate → neutral
    lower = _plan(price=20, minutes=7 * 1440)  # cheaper per day
    exp = (datetime.utcnow() + timedelta(days=3)).replace(microsecond=0) + timedelta(seconds=59)
    s = _sub(plan_id=old, expire_at=exp)
    d = _data(_cp(client, s.username, new, "neutral_keep_expiry"))
    assert _get(s.username).expire_at == exp and d["minute_delta"] == 0
    d = _data(_cp(client, s.username, lower, "lower_compensate"))
    after = _get(s.username).expire_at
    assert after.second == exp.second  # the delta is added, seconds preserved
    assert after - exp == timedelta(minutes=d["minute_delta"])


def test_plan_without_duration_is_priced_as_a_month(client):
    nodur = _plan(price=10, minutes=0)
    pricey = _plan(price=100, minutes=30 * 1440)
    s = _sub(plan_id=nodur, expire_at=datetime.utcnow() + timedelta(days=5, seconds=20))
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["plan"]["minutes"] == 43200
    assert ctx["plan"]["rate_per_minute"] == pytest.approx(10 / 43200, rel=1e-5)
    d = _data(_cp(client, s.username, pricey, "higher_debt"))
    assert d["debt_amount"] == pytest.approx(round((100 - 10) / 43200 * 5 * 1440, 2), abs=0.02)


def test_change_plan_is_idempotent(client):
    old = _plan(price=30)
    new = _plan(price=60)
    s = _sub(plan_id=old)
    hdr = {**AUTH, "Idempotency-Key": "cp-" + uuid4().hex}
    first = client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=hdr,
                        json={"plan_id": new, "policy": "higher_debt"})
    again = client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=hdr,
                        json={"plan_id": new, "policy": "higher_debt"})
    assert first.status_code == again.status_code == 200
    assert again.headers.get("Idempotent-Replay") == "true"
    n = _db().execute("SELECT COUNT(*) FROM accounting_ledger_entries WHERE username=? "
                      "AND source_type='subscriber_plan_change'", (s.username,)).fetchone()[0]
    assert n == 1


def test_web_change_plan_flash_shows_hours_and_arabic_errors(client):
    csrf = _web_login(client)
    old = _plan(price=70, minutes=30 * 1440)
    cheaper = _plan(price=30, minutes=30 * 1440)
    s = _sub(plan_id=old, expire_at=datetime.utcnow() + timedelta(hours=10, seconds=30))
    client.post(f"/admin/radius/users/{s.username}/change-plan",
                data={"_csrf_token": csrf, "plan_id": str(cheaper), "policy": "lower_compensate"})
    with client.session_transaction() as sess:
        flashes = [m for _c, m in sess.get("_flashes", [])]
    assert any("ساعة" in m and "0 يوم" not in m for m in flashes), flashes
    client.post(f"/admin/radius/users/{s.username}/change-plan",
                data={"_csrf_token": csrf, "plan_id": str(old), "policy": "lower_compensate"})
    with client.session_transaction() as sess:
        flashes = [m for _c, m in sess.get("_flashes", [])]
    assert flashes and not any("selected plan" in m or "not cheaper" in m for m in flashes), flashes


def test_web_change_plan_picker_uses_rate_and_hides_disabled(client):
    _web_login(client)
    live = _plan("picker_live", price=70, minutes=30 * 1440)
    _plan("picker_off", price=5, enabled=False)
    _sub(plan_id=live)
    html = client.get("/admin/radius/users").get_data(as_text=True)
    start = html.index('data-usq-plan-select')
    picker = html[start:html.index("</select>", start)]
    assert "picker_live" in picker and "picker_off" not in picker
    assert "data-rate=" in picker and "data-plan-rate=" in html


def test_profiles_scope_is_lowercased_and_color_is_validated(client):
    d = _data(_create(client, service_scope="HOTSPOT"), 201)
    assert d["service_scope"] == "hotspot"
    _err(_create(client, color="<script>alert(1)</script>"), 422, "validation_error")
    assert _data(_create(client, color="#2BAACC"), 201)["color"] == "#2BAACC"


def test_profiles_patch_with_a_text_body_is_422(client):
    pid = _data(_create(client), 201)["id"]
    res = client.patch(f"/api/v1/profiles/{pid}", headers={**AUTH, "Content-Type": "text/plain"},
                       data="price=0")
    _err(res, 422, "validation_error")
