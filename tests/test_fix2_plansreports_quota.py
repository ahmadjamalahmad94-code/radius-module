"""Fix wave 2 (re-test R04 N6/N7, R12 #19/N5) — quota periods, windows, enforcement.

Pins: a top-up is scoped to the current period (removed on plan change and
renewal; an admin-edited override survives), renewal restarts the usage count,
daily / monthly / per-direction plan quotas are enforced in RADIUS authorize,
the interim hook and the live sweep disconnect exhausted sessions, reset-daily
really restores the daily allowance and is idempotent, monthly/daily plans are
«has quota» (web profile + app context + top-up), the web quota forms always
record the system currency, and the unit picker keeps the typed number.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix2-quota-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
MB = 1_048_576


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "quota.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix2-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_fix2q")
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


@pytest.fixture(autouse=True)
def _no_pod(monkeypatch):
    """Never send a real PoD; record reconcile calls instead."""
    calls = []
    import app.radius.services.policy_reconciler as pr
    monkeypatch.setattr(pr, "reconcile_active_sessions_against_policy",
                        lambda tid, **kw: calls.append((tid, kw)) or None)
    return calls


# ─────────────── helpers ───────────────

def _db():
    from app.radius.db.connection import db
    return db()


def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_fix2q", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status):
    body = res.get_json()
    assert res.status_code == status, body
    return body["error"]


def _plan(name=None, *, price=30.0, minutes=30 * 1440, **cols) -> int:
    now = datetime.utcnow().isoformat()
    fields = {"tenant_id": 1, "name": name or "q_" + uuid4().hex[:6],
              "duration_minutes": minutes, "price": price, "currency": "ILS",
              "speed_down_kbps": 4096, "speed_up_kbps": 1024, "enabled": 1,
              "created_at": now, "updated_at": now}
    fields.update(cols)
    cur = _db().execute(
        f"INSERT INTO access_plans({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
        tuple(fields.values()))
    return int(cur.lastrowid)


def _sub(username=None, *, plan_id, days=10, balance=50.0, expire_at=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "q_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Quota User", mobile="0599000000", status="enabled",
        expire_at=expire_at or (datetime.utcnow() + timedelta(days=days))))
    _db().execute("UPDATE subscribers SET balance=? WHERE username=?", (balance, username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _acct(username, *, up_mb=0, down_mb=0, start=None, open_=False):
    start = start or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    _db().execute(
        "INSERT INTO radacct(tenant_id, username, acctsessionid, acctuniqueid, acctstarttime, "
        "acctsessiontime, acctinputoctets, acctoutputoctets, nasipaddress, acctstoptime) "
        "VALUES(1,?,?,?,?,60,?,?,'10.0.0.1',?)",
        (username, "s-" + uuid4().hex[:10], uuid4().hex, start, int(up_mb * MB),
         int(down_mb * MB), None if open_ else start))


def _auth(username):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password="secret", tenant_id=1))


def _cap(username):
    from app.radius.db.repos import plans_repo
    from app.radius.services.policy_engine import _effective_quota_mb
    s = _get(username)
    return _effective_quota_mb(s, plans_repo.get_plan(1, s.plan_id) if s.plan_id else None)


def _topup(client, username, mb, **extra):
    return client.post(f"/api/v1/accounts/{username}/quota/topup", headers=AUTH,
                       json={"quota_mb": mb, **extra})


# ─────────────── top-up scoped to the period ───────────────

def test_topup_is_removed_on_upgrade(client):
    g1 = _plan(quota_total_mb=1024, price=30)
    g20 = _plan(quota_total_mb=20480, price=60)
    s = _sub(plan_id=g1)
    assert _data(_topup(client, s.username, 100))["quota"]["combined_quota_mb"] == 1124
    assert _cap(s.username) == 1124
    _data(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                      json={"plan_id": g20, "policy": "higher_keep_expiry"}))
    after = _get(s.username)
    assert int(after.combined_quota_mb or 0) == 0 and not after.quota_limit_enabled
    assert _cap(s.username) == 20480   # the subscriber pays for 20 GB and gets 20 GB


def test_topup_is_removed_on_move_to_a_no_quota_plan(client):
    g1 = _plan(quota_total_mb=1024, price=30)
    unlimited = _plan(price=30, quota_total_mb=0)
    s = _sub(plan_id=g1)
    _data(_topup(client, s.username, 100))
    _data(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                      json={"plan_id": unlimited, "policy": "neutral_keep_expiry"}))
    assert _cap(s.username) == 0


def test_topup_is_removed_on_renewal_and_usage_restarts(client):
    g1 = _plan(quota_total_mb=1024, price=30, minutes=30 * 1440)
    s = _sub(plan_id=g1, days=2)
    _data(_topup(client, s.username, 100))
    _acct(s.username, down_mb=1500)            # more than 1124 → exhausted
    assert not _auth(s.username).ok
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                      json={"minutes": 30 * 1440}))   # a full period = renewal
    assert _cap(s.username) == 1024           # the top-up belonged to the old period
    assert _auth(s.username).ok               # usage counts from the renewal


def test_partial_extend_is_not_a_renewal(client):
    g1 = _plan(quota_total_mb=1024, price=30, minutes=30 * 1440)
    s = _sub(plan_id=g1, days=5)
    _data(_topup(client, s.username, 100))
    _data(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH,
                      json={"minutes": 60}))
    assert _cap(s.username) == 1124


def test_payment_renewal_restarts_the_period(client):
    g1 = _plan(quota_total_mb=100, price=30, minutes=30 * 1440)
    s = _sub(plan_id=g1, expire_at=datetime.utcnow() - timedelta(hours=1))
    _acct(s.username, down_mb=500)
    _data(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH,
                      json={"amount": 30}), 201)
    assert _auth(s.username).ok


def test_admin_edited_override_survives_the_period_reset(client):
    g1 = _plan(quota_total_mb=1024, price=30)
    g2 = _plan(quota_total_mb=2048, price=60)
    s = _sub(plan_id=g1)
    _data(_topup(client, s.username, 100))
    _db().execute("UPDATE subscribers SET combined_quota_mb=5000 WHERE username=?", (s.username,))
    _data(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                      json={"plan_id": g2, "policy": "higher_keep_expiry"}))
    assert _get(s.username).combined_quota_mb == 5000


# ─────────────── daily / monthly / per-direction plans ───────────────

def test_monthly_plan_is_a_quota_plan_and_is_enforced(client):
    m = _plan(quota_monthly_mb=100)
    s = _sub(plan_id=m)
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["quota"]["has_quota"] is True and ctx["quota"]["monthly"]["combined"] == 100
    _acct(s.username, down_mb=80, up_mb=30)
    dec = _auth(s.username)
    assert not dec.ok and dec.reason == "quota_exhausted" and "الشهريّة" in dec.message
    d = _data(_topup(client, s.username, 50))   # adds to THIS month only
    assert d["quota"]["monthly"]["combined"] == 150
    assert int(_get(s.username).combined_quota_mb or 0) == 0  # no permanent override
    assert _auth(s.username).ok


def test_last_month_usage_does_not_count(client):
    m = _plan(quota_monthly_mb=100)
    s = _sub(plan_id=m)
    old = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    _acct(s.username, down_mb=500, start=old)
    assert _auth(s.username).ok


def test_daily_plan_topup_and_enforcement(client):
    d_plan = _plan(quota_daily_mb=200)
    s = _sub(plan_id=d_plan)
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["quota"]["has_quota"] is True and ctx["quota"]["daily_quota_mb"] == 200
    _acct(s.username, down_mb=210)
    dec = _auth(s.username)
    assert not dec.ok and "اليوميّة" in dec.message
    d = _data(_topup(client, s.username, 100))      # used to say «unlimited»
    assert d["quota"]["daily"]["combined"] == 300
    assert _auth(s.username).ok


def test_daily_download_cap_is_enforced_per_direction(client):
    p = _plan(daily_download_quota_mb=100)
    s = _sub(plan_id=p)
    _acct(s.username, up_mb=500, down_mb=50)
    assert _auth(s.username).ok                    # upload is not capped
    _acct(s.username, down_mb=60)
    assert not _auth(s.username).ok
    err = _err(_topup(client, s.username, 10), 422)  # combined target on a direction cap
    assert "تنزيل" in err["message"]
    _data(_topup(client, s.username, 50, quota_target="download"))
    assert _auth(s.username).ok


def test_monthly_upload_cap_is_enforced(client):
    p = _plan(monthly_upload_quota_mb=10)
    s = _sub(plan_id=p)
    _acct(s.username, up_mb=11)
    assert not _auth(s.username).ok


def test_reset_daily_restores_the_daily_allowance_and_is_idempotent(client):
    d_plan = _plan(quota_daily_mb=100, concurrent_sessions=5, allowed_devices_count=5)
    s = _sub(plan_id=d_plan, balance=10)
    _db().execute("UPDATE subscribers SET device_count=5 WHERE username=?", (s.username,))
    _acct(s.username, down_mb=150, open_=True)
    assert not _auth(s.username).ok
    hdr = {**AUTH, "Idempotency-Key": "rd-" + uuid4().hex}
    body = {"charge_mode": "debt", "amount": 3}
    r1 = client.post(f"/api/v1/accounts/{s.username}/quota/reset-daily", headers=hdr, json=body)
    r2 = client.post(f"/api/v1/accounts/{s.username}/quota/reset-daily", headers=hdr, json=body)
    assert r1.status_code == r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert float(_get(s.username).balance) == 7.0    # charged once
    assert _auth(s.username).ok
    # growth of the open session after the reset counts again
    _db().execute("UPDATE radacct SET acctoutputoctets = acctoutputoctets + ? WHERE username=?",
                  (120 * MB, s.username))
    assert not _auth(s.username).ok


# ─────────────── live enforcement ───────────────

def test_interim_update_disconnects_an_exhausted_session(client, _no_pod):
    p = _plan(quota_daily_mb=50)
    s = _sub(plan_id=p)
    base = {"username": s.username, "acct_session_id": "sess-1",
            "nas_ip_address": "10.0.0.9"}
    client.post("/api/v1/accounting/events", headers=AUTH, json={**base, "status_type": "Start"})
    client.post("/api/v1/accounting/events", headers=AUTH,
                json={**base, "status_type": "Interim-Update", "output_octets": 10 * MB})
    quota_calls = lambda: [kw for _t, kw in _no_pod if kw.get("reason") == "quota_interim"]  # noqa: E731
    assert not quota_calls()
    client.post("/api/v1/accounting/events", headers=AUTH,
                json={**base, "status_type": "Interim-Update", "output_octets": 60 * MB})
    assert quota_calls() and quota_calls()[-1]["usernames"] == [s.username]


def test_live_sweep_finds_exhausted_sessions(client, _no_pod):
    from app.radius.services import quota_period
    p = _plan(quota_monthly_mb=10)
    ok_plan = _plan(quota_monthly_mb=1000)
    a = _sub(plan_id=p)
    b = _sub(plan_id=ok_plan)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    _acct(a.username, down_mb=20, start=now, open_=True)
    _acct(b.username, down_mb=20, start=now, open_=True)
    _db().execute("UPDATE radacct SET acctupdatetime=? WHERE acctstoptime IS NULL", (now,))
    stats = quota_period.enforce_live_quota(1)
    assert stats["exhausted"] == 1
    sweep = [kw for _t, kw in _no_pod if kw.get("reason") == "quota_sweep"]
    assert sweep and sweep[-1]["usernames"] == [a.username]


# ─────────────── web ───────────────

def test_web_quota_forms_always_use_the_system_currency(client):
    csrf = _web_login(client)
    g1 = _plan(quota_total_mb=1024)
    s = _sub(plan_id=g1, balance=20)
    client.post(f"/admin/radius/users/{s.username}/quota/topup", data={
        "_csrf_token": csrf, "quota_mb": "10", "charge_mode": "paid", "amount": "1",
        "currency": "USD"})
    client.post(f"/admin/radius/users/{s.username}/quota/reset-daily", data={
        "_csrf_token": csrf, "charge_mode": "paid", "amount": "1", "currency": "USD"})
    rows = _db().execute("SELECT currency FROM accounting_ledger_entries WHERE username=?",
                         (s.username,)).fetchall()
    from app.radius.core.system_config import default_currency
    assert len(rows) == 2 and {r["currency"] for r in rows} == {default_currency()}


def test_web_profile_shows_monthly_quota_not_zero(client):
    _web_login(client)
    m = _plan(quota_monthly_mb=102400)
    s = _sub(plan_id=m)
    html = client.get(f"/admin/radius/users/{s.username}/profile").get_data(as_text=True)
    assert "الكوتا الشهريّة" in html


def test_web_quota_topup_on_monthly_plan_is_accepted(client):
    csrf = _web_login(client)
    m = _plan(quota_monthly_mb=100)
    s = _sub(plan_id=m)
    client.post(f"/admin/radius/users/{s.username}/quota/topup", data={
        "_csrf_token": csrf, "quota_mb": "10", "charge_mode": "free"})
    with client.session_transaction() as sess:
        flashes = [msg for _c, msg in sess.get("_flashes", [])]
    assert any("تمت إضافة" in f for f in flashes), flashes


def test_unit_picker_keeps_the_typed_number():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "templates", "_partials",
                            "unit_input.html"), encoding="utf-8").read()
    body = src[src.index("function changeUnit(){"):]
    body = body[:body.index("}") + 1]
    assert "baseBefore" not in body and "sync();" in body
    assert "u066B" in src  # the Arabic decimal separator is read as «.»
