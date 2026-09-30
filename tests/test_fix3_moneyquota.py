"""Fix wave 3 — stream «moneyquota» (F03 / F04 / F08), 2026-09-30.

One test (or more) per item, API and web where the web shares the path:
change-plan caps (1 year / 2100 / 100,000), profit-loss = collected money vs
real expenses, reset-daily without a daily cap, notifications total, dashboard
plan KPI, sweep audit dedupe, plan priority 1–10, data_value refused, bulk
extend preview cap, loans «٫» + duration_minutes, one DST rule for local
times, debt extension alert type, and the (switched-off) create 1-year rule.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix3-mq-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
FETCH = {"X-Requested-With": "fetch"}
ONE_YEAR_AR = "أقصى تمديد في المرة الواحدة سنة"
TOO_FAR_AR = "المدة الناتجة تتجاوز الحدّ المسموح"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fix3mq.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_CREATE_EXPIRY_ONE_YEAR", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix3-mq-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_f3")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()

    def _fresh_g():
        from flask import g
        for attr in ("_api_authed", "api_token", "api_token_id", "api_token_scopes",
                     "admin_id", "tenant_id", "_sa_identity", "_sa_identity_key"):
            g.__dict__.pop(attr, None)
    application.before_request_funcs.setdefault(None, []).insert(0, _fresh_g)
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _no_pod(monkeypatch):
    import app.radius.services.policy_reconciler as pr
    monkeypatch.setattr(pr, "reconcile_active_sessions_against_policy",
                        lambda tid, **kw: None)


# ─────────────── helpers ───────────────

def _db():
    from app.radius.db.connection import db
    return db()


def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_f3", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _flashes(client) -> list:
    with client.session_transaction() as sess:
        return [m for _c, m in sess.get("_flashes", [])]


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status=422):
    body = res.get_json()
    assert res.status_code == status, body
    return body["error"]


def _plan(name=None, *, price=30.0, minutes=30 * 1440, **cols) -> int:
    now = datetime.utcnow().isoformat()
    fields = {"tenant_id": 1, "name": name or "p3_" + uuid4().hex[:6],
              "duration_minutes": minutes, "price": price, "currency": "ILS",
              "speed_down_kbps": 4096, "speed_up_kbps": 1024, "enabled": 1,
              "created_at": now, "updated_at": now}
    fields.update(cols)
    cur = _db().execute(
        f"INSERT INTO access_plans({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
        tuple(fields.values()))
    return int(cur.lastrowid)


def _sub(*, plan_id, balance=0.0, expire_at=None, days=30):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = "s3_" + uuid4().hex[:8]
    exp = expire_at if expire_at is not None else datetime.utcnow() + timedelta(days=days)
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="F3 User", mobile="0599000000", status="enabled", expire_at=exp))
    _db().execute("UPDATE subscribers SET balance=? WHERE tenant_id=1 AND username=?",
                  (float(balance), username))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


# ─────────────── 2. change-plan caps (F04 M1 / F08 H2) ───────────────

def test_change_plan_compensation_over_one_year_is_refused_api(client):
    dear = _plan(price=120.0)
    cheap = _plan(price=0.01)
    s = _sub(plan_id=dear, days=30)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                           json={"plan_id": cheap, "policy": "lower_compensate"}))
    assert ONE_YEAR_AR in err["message"] and "بدون تعويض" in err["message"]
    after = _get(s.username)
    assert after.plan_id == dear and after.expire_at == s.expire_at


def test_change_plan_compensation_within_a_year_still_works(client):
    dear = _plan(price=60.0)
    cheap = _plan(price=30.0)
    s = _sub(plan_id=dear, days=30)
    d = _data(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                          json={"plan_id": cheap, "policy": "lower_compensate"}))
    assert 29 * 1440 <= d["minute_delta"] <= 30 * 1440
    assert _get(s.username).plan_id == cheap


def test_change_plan_compensation_past_2100_is_refused(client):
    dear = _plan(price=60.0)
    cheap = _plan(price=30.0)
    s = _sub(plan_id=dear, expire_at=datetime(2100, 9, 1))
    # remaining ≈ 74 years… compensation is > 1 year anyway → refused first.
    err = _err(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                           json={"plan_id": cheap, "policy": "lower_compensate"}))
    assert ONE_YEAR_AR in err["message"]
    # within a year but past 2100: service guard (add_minutes_capped).
    from app.radius.core.errors import RadiusValidationError
    from app.radius.core.numbers import add_minutes_capped
    with pytest.raises(RadiusValidationError) as ei:
        add_minutes_capped(datetime(2100, 12, 1), 60 * 1440)
    assert TOO_FAR_AR in ei.value.message


def test_change_plan_debt_over_100k_is_refused(client):
    cheap = _plan(price=1.0)
    dear = _plan(price=150000.0)
    s = _sub(plan_id=cheap, days=30, balance=0)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                           json={"plan_id": dear, "policy": "higher_debt"}))
    assert "100000" in err["message"] and "إنقاص الأيام" in err["message"]
    after = _get(s.username)
    assert after.plan_id == cheap and float(after.balance or 0) == 0.0
    assert _db().execute("SELECT COUNT(*) FROM accounting_ledger_entries WHERE username=?",
                         (s.username,)).fetchone()[0] == 0


def test_change_plan_caps_on_the_web_route(client):
    csrf = _web_login(client)
    dear = _plan(price=120.0)
    cheap = _plan(price=0.01)
    s = _sub(plan_id=dear, days=30)
    client.post(f"/admin/radius/users/{s.username}/change-plan", data={
        "_csrf_token": csrf, "plan_id": str(cheap), "policy": "lower_compensate"})
    msgs = _flashes(client)
    assert any(ONE_YEAR_AR in m for m in msgs), msgs
    assert _get(s.username).plan_id == dear


# ─────────────── 3. profit-loss (F04 M3) ───────────────

def test_profit_loss_counts_collected_money_not_wallet_movements(client):
    pid = _plan(price=30.0, quota_total_mb=1024)
    dear = _plan(price=3000.0)
    s = _sub(plan_id=pid, days=30, balance=0)
    p1 = _data(client.post("/api/v1/payments", headers=AUTH,
                           json={"username": s.username, "amount": 0.1}), 201)
    _data(client.post("/api/v1/payments", headers=AUTH,
                      json={"username": s.username, "amount": 0.2}), 201)
    p3 = _data(client.post("/api/v1/payments", headers=AUTH,
                           json={"username": s.username, "amount": 41238.01}), 201)
    _data(client.post(f"/api/v1/payments/{p3['payment']['id']}/void", headers=AUTH,
                      json={}), 201)
    del p1
    # wallet movements — none of these is revenue or an expense:
    _data(client.post(f"/api/v1/accounts/{s.username}/balance", headers=AUTH,
                      json={"amount": 200}))                                   # deposit
    _data(client.post(f"/api/v1/accounts/{s.username}/quota/topup", headers=AUTH,
                      json={"quota_mb": 100, "charge_mode": "paid", "amount": 5}))
    _data(client.post("/api/v1/loans", headers=AUTH,
                      json={"username": s.username, "days": 1, "amount": 7}), 201)
    _data(client.post(f"/api/v1/accounts/{s.username}/change-plan", headers=AUTH,
                      json={"plan_id": dear, "policy": "higher_debt"}))       # big debt
    from app.radius.db.repos import company_inventory_repo
    company_inventory_repo.add_expense(tenant_id=1, title="إيجار", amount=0.25)
    pl = _data(client.get("/api/v1/reports/profit-loss", headers=AUTH))["items"][0]
    assert pl["credits"] == 0.3            # 0.1 + 0.2, the voided 41238.01 nets out
    assert pl["payments"] == 0.3 and pl["card_sales"] == 0.0
    assert pl["debits"] == 0.25 and pl["expenses"] == 0.25
    assert pl["net"] == 0.05
    ils = {c["currency"]: c for c in pl["by_currency"]}["ILS"]
    assert ils["credits"] == 0.3 and ils["debits"] == 0.25 and ils["net"] == 0.05
    # web page: Arabic labels, no raw keys.
    _web_login(client)
    html = client.get("/admin/radius/finance/accounting?tab=reports&type=profit_loss"
                      ).get_data(as_text=True)
    assert "مصروفات الشركة" in html and "لا تُحسب إيرادًا" in html
    assert ">expenses<" not in html and ">card_sales<" not in html


def test_profit_loss_rounds_float_noise():
    from app.radius.core.numbers import round_money
    assert round_money(41238.00999999998) == 41238.01


# ─────────────── 4a. reset-daily without a daily cap (F04 N-L1) ───────────────

def test_reset_daily_is_refused_without_a_daily_cap_api(client):
    total_only = _plan(quota_total_mb=1024)
    s = _sub(plan_id=total_only, balance=50)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/quota/reset-daily", headers=AUTH,
                           json={"charge_mode": "debt", "amount": 5}))
    assert "لا توجد لهذا المشترك كوتة يوميّة" in err["message"]
    assert float(_get(s.username).balance) == 50.0
    ctx = _data(client.get(f"/api/v1/accounts/{s.username}/actions-context", headers=AUTH))
    assert ctx["quota"]["daily_reset_available"] is False
    daily = _plan(daily_combined_quota_mb=500)
    s2 = _sub(plan_id=daily, balance=50)
    _data(client.post(f"/api/v1/accounts/{s2.username}/quota/reset-daily", headers=AUTH,
                      json={"charge_mode": "debt", "amount": 5}))
    assert float(_get(s2.username).balance) == 45.0
    ctx = _data(client.get(f"/api/v1/accounts/{s2.username}/actions-context", headers=AUTH))
    assert ctx["quota"]["daily_reset_available"] is True
    # a daily TIME cap alone also makes it meaningful.
    timed = _plan(max_daily_minutes=120)
    s3 = _sub(plan_id=timed)
    _data(client.post(f"/api/v1/accounts/{s3.username}/quota/reset-daily", headers=AUTH,
                      json={"charge_mode": "free"}))


def test_reset_daily_without_cap_web_single_and_bulk(client):
    csrf = _web_login(client)
    s = _sub(plan_id=_plan(quota_total_mb=1024), balance=50)
    ok_sub = _sub(plan_id=_plan(daily_combined_quota_mb=100), balance=50)
    client.post(f"/admin/radius/users/{s.username}/quota/reset-daily", data={
        "_csrf_token": csrf, "charge_mode": "paid", "amount": "5"})
    assert any("لا توجد لهذا المشترك كوتة يوميّة" in m for m in _flashes(client))
    assert float(_get(s.username).balance) == 50.0
    client.post("/admin/radius/users/quota/reset-daily-bulk", data={
        "_csrf_token": csrf, "charge_mode": "paid", "amount": "5",
        "usernames": [s.username, ok_sub.username]})
    msgs = _flashes(client)
    assert any("تُخطّي 1" in m for m in msgs), msgs
    assert float(_get(s.username).balance) == 50.0
    assert float(_get(ok_sub.username).balance) == 45.0


# ─────────────── 4b. notifications total (F04 N-L3) ───────────────

def test_notifications_center_total_is_the_real_count(client):
    from app.radius.db.repos import notifications_repo
    for i in range(205):
        notifications_repo.create(1, title=f"n{i}", body="b")
    assert notifications_repo.total_count(1) >= 205
    _web_login(client)
    html = client.get("/admin/radius/notifications").get_data(as_text=True)
    total = notifications_repo.total_count(1)
    assert "data-nc-shown-of" in html and f"من {total}" in html


# ─────────────── 4c. dashboard plans KPI (F04 N-L6) ───────────────

def test_dashboard_plan_kpi_excludes_archived_plans(client):
    from app.radius.services.dashboard_metrics import get_plan_counts
    before = get_plan_counts(1)
    _plan()
    gone = _plan()
    _db().execute("UPDATE access_plans SET deleted_at=? WHERE id=?",
                  (datetime.utcnow().isoformat(), gone))
    after = get_plan_counts(1)
    assert after["total"] == before["total"] + 1
    profiles = _data(client.get("/api/v1/profiles", headers=AUTH))["count"]
    dash = _data(client.get("/api/v1/dashboard", headers=AUTH))
    assert dash["plans_total"] == profiles == after["total"]


# ─────────────── 4d. sweep audit dedupe (F04 N-L7) ───────────────

def test_undeliverable_pod_is_audited_once_per_hour(app, monkeypatch):
    import app.radius.services.policy_reconciler as pr
    import app.radius.services.live_session_control as lsc
    import app.radius.services.mt_action_log as mal
    pr._failed_logged.clear()
    rows = [{"username": "u1", "acctsessionid": "sess-1", "nasipaddress": "10.9.9.9"}]
    monkeypatch.setattr(pr, "_live_rows", lambda *a, **k: list(rows))
    monkeypatch.setattr(pr, "_device_limit_violations", lambda *a, **k: {})
    monkeypatch.setattr(pr, "check_session_compliance", lambda *a, **k: "quota")

    class _Out:
        ok = False
        nas_ip = "10.9.9.9"
        reply_message = "timeout"
        code_name = ""
    monkeypatch.setattr(lsc, "disconnect_live", lambda **k: _Out())
    audits = []
    monkeypatch.setattr(mal, "record_disconnect", lambda **k: audits.append(k))
    for _ in range(3):
        stats = pr._run(1, usernames=["u1"], reason="quota_sweep")
        assert stats["failed"] == 1
    assert len(audits) == 1                     # not one row per minute
    _Out.ok = True
    pr._run(1, usernames=["u1"], reason="quota_sweep")
    assert len(audits) == 2 and audits[-1]["ok"] is True
    _Out.ok = False
    pr._run(1, usernames=["u1"], reason="quota_sweep")
    assert len(audits) == 3                     # a new failure after success is logged


# ─────────────── 4e. plan priority 1–10 (F04 N-L10) ───────────────

def test_plan_priority_one_scale_api_and_web(client):
    d = _data(client.post("/api/v1/profiles", headers=AUTH,
                          json={"name": "pr_" + uuid4().hex[:5], "duration_minutes": 60,
                                "price": 1, "speed_down_kbps": 1024,
                                "speed_up_kbps": 512}), 201)
    assert d["priority"] == 5
    d100 = _data(client.post("/api/v1/profiles", headers=AUTH,
                             json={"name": "pr_" + uuid4().hex[:5], "duration_minutes": 60,
                                   "price": 1, "priority": 100, "speed_down_kbps": 1024,
                                   "speed_up_kbps": 512}), 201)
    assert d100["priority"] == 5          # the old app/API default = «not chosen»
    err = _err(client.post("/api/v1/profiles", headers=AUTH,
                           json={"name": "pr_" + uuid4().hex[:5], "priority": 11,
                                 "duration_minutes": 60, "price": 1,
                                 "speed_down_kbps": 1024, "speed_up_kbps": 512}))
    assert "الأولويّة" in err["message"]
    d7 = _data(client.patch(f"/api/v1/profiles/{d['id']}", headers=AUTH,
                            json={"priority": 7}))
    assert d7["priority"] == 7
    # legacy rows (raw 100 / 40) read inside 1–10.
    legacy = _plan(priority=100)
    high = _plan(priority=40)
    from app.radius.db.repos import plans_repo
    assert plans_repo.get_plan(1, legacy).priority == 5
    assert plans_repo.get_plan(1, high).priority == 10


def test_priority_migration_normalises_existing_rows(app):
    import importlib.resources  # noqa: F401
    from pathlib import Path
    pid_a = _plan(priority=100)
    pid_b = _plan(priority=250)
    pid_c = _plan(priority=3)
    sql = (Path(__file__).resolve().parent.parent / "app" / "radius" / "db" / "migrations"
           / "190_access_plans_priority_scale.sql").read_text(encoding="utf-8")
    _db().executescript(sql)
    got = {r["id"]: r["priority"] for r in _db().execute(
        "SELECT id, priority FROM access_plans WHERE id IN (?,?,?)",
        (pid_a, pid_b, pid_c)).fetchall()}
    assert got == {pid_a: 5, pid_b: 10, pid_c: 3}


# ─────────────── 4f. data_value refused (F04 N-L11) ───────────────

def test_data_value_is_refused_not_silently_stored(client):
    err = _err(client.post("/api/v1/profiles", headers=AUTH,
                           json={"name": "dv_" + uuid4().hex[:5], "data_value": 2,
                                 "data_unit": "GB", "duration_minutes": 60, "price": 1,
                                 "speed_down_kbps": 1024, "speed_up_kbps": 512}))
    assert "quota_total_mb" in err["message"]
    d = _data(client.post("/api/v1/profiles", headers=AUTH,
                          json={"name": "dv_" + uuid4().hex[:5], "quota_total_mb": 2048,
                                "duration_minutes": 60, "price": 1,
                                "speed_down_kbps": 1024, "speed_up_kbps": 512}), 201)
    assert d["quota_total_mb"] == 2048
    # an old row with a stored data_value can still be edited (value unchanged).
    old = _plan(data_value=3, data_unit="GB")
    _data(client.patch(f"/api/v1/profiles/{old}", headers=AUTH,
                       json={"description": "x", "data_value": 3}))


# ─────────────── 5. F03 lows ───────────────

def test_bulk_extend_preview_applies_the_one_year_cap(client):
    s = _sub(plan_id=_plan())
    err = _err(client.post("/api/v1/tools/general-adjustments", headers=AUTH,
                           json={"action": "extend", "minutes": 600000, "dry_run": True,
                                 "usernames": [s.username]}))
    assert ONE_YEAR_AR in err["message"]
    d = _data(client.post("/api/v1/tools/general-adjustments", headers=AUTH,
                          json={"action": "extend", "minutes": 525600, "dry_run": True,
                                "usernames": [s.username]}))
    assert d["would_succeed"] == 1
    far = _sub(plan_id=_plan(), expire_at=datetime(2100, 6, 1))
    d = _data(client.post("/api/v1/tools/general-adjustments", headers=AUTH,
                          json={"action": "extend", "minutes": 525600, "dry_run": True,
                                "usernames": [far.username]}))
    assert d["would_fail"] == 1 and d["refused"] == [far.username]
    assert TOO_FAR_AR in d["items"][0]["error"]


def test_loans_centre_and_settle_accept_the_arabic_decimal(client):
    s = _sub(plan_id=_plan())
    d = _data(client.post("/api/v1/loans", headers=AUTH,
                          json={"username": s.username, "days": 1, "amount": "٣٫٢٥"}), 201)
    loan = d["loan"]
    assert float(loan["amount"]) == 3.25
    st = _data(client.post(f"/api/v1/loans/{loan['id']}/settle", headers=AUTH,
                           json={"amount": "١٫٢٥"}), 201)
    assert st["settlement"]


def test_loans_duration_minutes_wins_over_the_price_derived_length(client):
    s = _sub(plan_id=_plan(price=30.0))
    d = _data(client.post("/api/v1/loans", headers=AUTH,
                          json={"username": s.username, "duration_minutes": 60,
                                "amount": 5}), 201)
    assert int(d["loan"]["duration_minutes"]) == 60
    err = _err(client.post("/api/v1/loans", headers=AUTH,
                           json={"username": s.username, "duration_minutes": 60,
                                 "days": 1, "amount": 5}))
    assert "مرّةً واحدة" in err["message"]


def test_one_dst_rule_for_ambiguous_local_times(client):
    from app.radius.core import system_config as sc
    # 2026-10-24 01:30 local happens twice (02:00+03 → 01:00+02): first one.
    assert sc.from_local("2026-10-24 01:30", tenant_id=1) == datetime(2026, 10, 23, 22, 30)
    assert sc.from_local("2026-10-24 00:30", tenant_id=1) == datetime(2026, 10, 23, 21, 30)
    assert sc.from_local("2026-10-24 02:30", tenant_id=1) == datetime(2026, 10, 24, 0, 30)
    system = _data(client.get("/api/v1/settings", headers=AUTH))["system"]
    assert system["local_time_rule"]["ambiguous"] == "earlier"
    trans = {t["at"]: t for t in sc.tz_transitions(1, at=datetime(2026, 9, 30))}
    assert trans["2026-10-23T23:00:00Z"]["offset_before_minutes"] == 180
    assert trans["2026-10-23T23:00:00Z"]["offset_after_minutes"] == 120
    assert isinstance(system["tz_transitions"], list) and system["tz_transitions"]


def test_create_one_year_rule_is_on(client, monkeypatch):
    """Owner decision 2026-09-30: the per-operation rule also binds CREATE
    (2090 was accepted). Emergency switch-off: HOBERADIUS_CREATE_EXPIRY_ONE_YEAR=0.
    The configurable variants are pinned in test_fix3_limits.py."""
    body = lambda: {"username": "c3_" + uuid4().hex[:8], "password": "secret1",  # noqa: E731
                    "plan_id": _plan(), "expire_at": "2090-01-01T00:00:00Z"}
    err = _err(client.post("/api/v1/accounts", headers=AUTH, json=body()))
    assert "عند إنشاء المشترك سنة" in err["message"]
    ok_body = body()
    ok_body["expire_at"] = (datetime.utcnow() + timedelta(days=300)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    _data(client.post("/api/v1/accounts", headers=AUTH, json=ok_body), 201)
    monkeypatch.setenv("HOBERADIUS_CREATE_EXPIRY_ONE_YEAR", "0")
    _data(client.post("/api/v1/accounts", headers=AUTH, json=body()), 201)


def test_web_money_forms_show_the_cap_reason_not_a_generic_error(client):
    """NonFiniteNumber (cap refusals) is a ValueError too — the web handlers
    caught ValueError first and showed «قيمة المبلغ غير صحيحة» instead."""
    csrf = _web_login(client)
    s = _sub(plan_id=_plan(daily_combined_quota_mb=100, quota_total_mb=1024), balance=0)
    client.post(f"/admin/radius/users/{s.username}/quota/reset-daily", data={
        "_csrf_token": csrf, "charge_mode": "debt", "amount": "100000.01"})
    client.post(f"/admin/radius/users/{s.username}/quota/topup", data={
        "_csrf_token": csrf, "quota_mb": "10", "charge_mode": "debt", "amount": "100000.01"})
    msgs = _flashes(client)
    assert sum("الحدّ الأقصى للعملية الواحدة" in m for m in msgs) == 2, msgs
    assert float(_get(s.username).balance or 0) == 0.0
