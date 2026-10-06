"""FIELDS (team plan, owner 2026-10-06) — the three plan flags WIRED.

1. «مدفوع مسبقًا» (prepaid) = a reporting label: badge in the plans list, a
   «باقات مدفوعة مسبقًا» filter on the subscribers list and the subscriber-
   payments finance report. No enforcement.
2. «تجديد تلقائي» = per-plan mode off / debt / balance / free. At expiry the
   minute sweep renews by the plan period FROM the expiry moment through the
   manual extend service (same ledger/audit/alert), never twice for one period;
   «balance» renews only when the wallet covers the price (else: no renewal +
   admin alert). Admin alert «تجديد تلقائي» with the result.
3. «استخدام مرة وحدة» = a TEMPORARY account: disabled (and kicked) when its
   time ends; extend / set-expiry / paid time / auto-renew refuse with
   «هاد حساب مؤقت للاستخدام مرة وحدة»; «مؤقت» badge in the lists.

All fail on 825c7b45 (the flags were stored and never read). Run alone.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fields-flags-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
SINGLE_USE_AR = "هاد حساب مؤقت للاستخدام مرة وحدة"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fields_flags.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fields-flags-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_ff")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-ff")
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
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _kicks(monkeypatch):
    calls = []
    import app.radius.services.policy_reconciler as pr
    monkeypatch.setattr(pr, "reconcile_active_sessions_against_policy",
                        lambda tid, **kw: calls.append(kw) or None)
    return calls


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    from app.radius.services import admin_alerts
    monkeypatch.setattr(admin_alerts, "dispatch",
                        lambda tid, key, ctx=None, **kw: sent.append((key, dict(ctx or {}))))
    return sent


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(**cols) -> int:
    now = datetime.utcnow().isoformat()
    fields = {"tenant_id": 1, "name": "ff_" + uuid4().hex[:6], "duration_minutes": 30 * 1440,
              "price": 30.0, "currency": "ILS", "speed_down_kbps": 4096,
              "speed_up_kbps": 1024, "enabled": 1, "created_at": now, "updated_at": now}
    fields.update(cols)
    cur = _db().execute(
        f"INSERT INTO access_plans({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
        tuple(fields.values()))
    return int(cur.lastrowid)


def _sub(plan_id: int, *, expire_in=timedelta(minutes=1), balance=0.0):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = "ff_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Flags", mobile="0599000000", status="enabled", balance=balance,
        expire_at=datetime.utcnow() + expire_in))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _ledger(username):
    return [dict(r) for r in _db().execute(
        "SELECT entry_type, amount, currency FROM accounting_ledger_entries "
        "WHERE username = ? ORDER BY id", (username,)).fetchall()]


def _web_login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "owner_ff", "password": "owner-pass-ff"})
    assert res.status_code in {302, 303}, res.status_code


# ───────────────────────────── 2. auto-renew ─────────────────────────────

def test_auto_renew_debt_extends_from_expiry_once_and_records_debt(app, alerts):
    from app.radius.services import plan_lifecycle
    s = _sub(_plan(auto_renew_mode="debt", auto_renew=1))
    old = s.expire_at
    stats = plan_lifecycle.sweep(1)
    assert stats["renewed"] == 1
    after = _get(s.username)
    assert abs((after.expire_at - (old + timedelta(days=30))).total_seconds()) < 2
    assert float(after.balance) == -30.0
    assert [(e["entry_type"], e["amount"], e["currency"]) for e in _ledger(s.username)] == \
        [("debt", 30.0, "ILS")]
    audit = _db().execute("SELECT actor, action FROM audit_log WHERE target_id = ? "
                          "AND action = 'extend_time'", (s.username,)).fetchall()
    assert [(a["actor"], a["action"]) for a in audit] == [("system:auto-renew", "extend_time")]
    renew = [c for k, c in alerts if k == "auto_renew"]
    assert len(renew) == 1 and renew[0]["result"] == "تمّ التجديد"
    # never twice for the same period (the claim survives a second sweep)
    _db().execute("UPDATE subscribers SET expire_at = ? WHERE username = ?",
                  (old.isoformat(), s.username))
    assert plan_lifecycle.sweep(1)["renewed"] == 0


def test_auto_renew_balance_insufficient_does_not_renew_and_alerts(app, alerts):
    from app.radius.services import plan_lifecycle
    s = _sub(_plan(auto_renew_mode="balance"), balance=10.0)
    plan_lifecycle.sweep(1)
    after = _get(s.username)
    assert after.expire_at == s.expire_at and float(after.balance) == 10.0
    assert _ledger(s.username) == []
    row = _db().execute("SELECT result FROM plan_auto_renewals WHERE username=?",
                        (s.username,)).fetchone()
    assert row["result"] == "insufficient"
    renew = [c for k, c in alerts if k == "auto_renew"]
    assert renew and "الرصيد لا يكفي" in renew[0]["result"]


def test_auto_renew_balance_sufficient_deducts(app, alerts):
    from app.radius.services import plan_lifecycle
    s = _sub(_plan(auto_renew_mode="balance"), balance=50.0)
    assert plan_lifecycle.sweep(1)["renewed"] == 1
    after = _get(s.username)
    assert float(after.balance) == 20.0
    assert [e["entry_type"] for e in _ledger(s.username)] == ["time_extension"]


def test_auto_renew_free_and_off(app, alerts):
    from app.radius.services import plan_lifecycle
    free = _sub(_plan(auto_renew_mode="free"))
    off = _sub(_plan())
    far = _sub(_plan(auto_renew_mode="free"), expire_in=timedelta(days=3))
    assert plan_lifecycle.sweep(1)["renewed"] == 1
    assert _get(free.username).expire_at > free.expire_at + timedelta(days=29)
    assert float(_get(free.username).balance) == 0.0 and _ledger(free.username) == []
    assert _get(off.username).expire_at == off.expire_at
    assert _get(far.username).expire_at == far.expire_at


def test_auto_renew_mode_api_round_trip_and_validation(client):
    body = {"name": "ff-api-" + uuid4().hex[:5], "speed_down_kbps": 2048,
            "speed_up_kbps": 512, "auto_renew_mode": "balance"}
    r = client.post("/api/v1/profiles", json=body, headers=AUTH)
    assert r.status_code == 201, r.get_json()
    d = r.get_json()["data"]
    assert d["auto_renew_mode"] == "balance" and d["auto_renew"] is True
    pid = d["id"]
    # legacy key from old app builds: ignored (doesn't flip the mode)
    r = client.patch(f"/api/v1/profiles/{pid}", json={"auto_renew": False}, headers=AUTH)
    assert r.status_code == 200 and r.get_json()["data"]["auto_renew_mode"] == "balance"
    r = client.patch(f"/api/v1/profiles/{pid}", json={"auto_renew_mode": "weekly"}, headers=AUTH)
    assert r.status_code == 422


# ───────────────────────────── 3. single use ─────────────────────────────

def test_single_use_account_is_disabled_and_kicked_at_expiry(app, _kicks, alerts):
    from app.radius.services import plan_lifecycle
    s = _sub(_plan(single_use_once=1, duration_minutes=60, auto_renew_mode="free"),
             expire_in=timedelta(minutes=-1))
    live = _sub(_plan(single_use_once=1, duration_minutes=60), expire_in=timedelta(hours=1))
    stats = plan_lifecycle.sweep(1)
    assert stats["single_use_disabled"] == 1 and stats["renewed"] == 0
    assert _get(s.username).status == "disabled"
    assert _get(live.username).status == "enabled"
    assert any(s.username in (c.get("usernames") or []) for c in _kicks)
    audit = _db().execute("SELECT actor FROM audit_log WHERE target_id = ? AND action = ?",
                          (s.username, "disable")).fetchall()
    assert [a["actor"] for a in audit] == ["system:single-use"]


def test_single_use_refuses_extend_set_expiry_and_paid_time(app):
    from app.radius.core.errors import RadiusValidationError
    from app.radius.services import subscriber_actions as sa
    from app.radius.services.accounting import AccountingService
    from app.radius.services.users import get_users_service
    s = _sub(_plan(single_use_once=1, duration_minutes=60), expire_in=timedelta(minutes=30))
    caller = sa.ActionCaller(tenant_id=1, admin_id=None, is_super=True, actor="t")
    with pytest.raises(RadiusValidationError) as e:
        sa.extend_subscriber(caller, s.username, minutes=60)
    assert SINGLE_USE_AR in e.value.message
    with pytest.raises(RadiusValidationError):
        get_users_service().set_expiry(actor="t", username=s.username,
                                       expire_at=datetime.utcnow() + timedelta(days=1))
    with pytest.raises(RadiusValidationError) as e:
        AccountingService(1).create_payment(
            {"username": s.username, "amount": 10, "apply_to_radius": True}, actor="t")
    assert SINGLE_USE_AR in e.value.message
    assert _ledger(s.username) == []
    assert _get(s.username).expire_at == s.expire_at


def test_single_use_extend_api_is_422_arabic(client, app):
    s = _sub(_plan(single_use_once=1, duration_minutes=60), expire_in=timedelta(minutes=30))
    other = _sub(_plan(), expire_in=timedelta(days=2))
    items = client.get("/api/v1/accounts?limit=100", headers=AUTH).get_json()["data"]["items"]
    flags = {i["username"]: i.get("temporary_account") for i in items}
    assert flags[s.username] is True and flags[other.username] is False
    r = client.post(f"/api/v1/accounts/{s.username}/extend", json={"minutes": 60},
                    headers=AUTH)
    assert r.status_code == 422, r.get_json()
    assert SINGLE_USE_AR in str(r.get_json())


# ───────────────────────────── 1. prepaid + badges ─────────────────────────────

def test_plans_list_shows_prepaid_temporary_and_auto_renew_badges(client, app):
    _web_login(client)
    pre = _plan(prepaid=1)
    tmp = _plan(prepaid=0, single_use_once=1)
    html = client.get("/admin/radius/plans").get_data(as_text=True)
    import re
    rows = {int(m.group(1)): m.group(0) for m in re.finditer(
        r'<td class="num mono">(\d+)</td>.*?</tr>', html, re.S)}
    assert 'data-pl-badge="prepaid"' in rows[pre]
    assert 'data-pl-badge="prepaid"' not in rows[tmp]
    assert 'data-pl-badge="single-use"' in rows[tmp]


def test_subscribers_list_prepaid_filter_and_temporary_badge(client, app):
    _web_login(client)
    a = _sub(_plan(prepaid=1), expire_in=timedelta(days=5))
    b = _sub(_plan(prepaid=0, single_use_once=1, duration_minutes=60),
             expire_in=timedelta(minutes=30))
    html = client.get("/admin/radius/users?prepaid=1").get_data(as_text=True)
    assert a.username in html and b.username not in html
    html = client.get("/admin/radius/users?prepaid=0").get_data(as_text=True)
    assert b.username in html and a.username not in html
    assert 'data-u-badge="single-use"' in html


def test_finance_subscriber_payments_report_prepaid_filter(app):
    from app.radius.services.accounting import AccountingService
    a = _sub(_plan(prepaid=1), expire_in=timedelta(days=5))
    b = _sub(_plan(prepaid=0), expire_in=timedelta(days=5))
    svc = AccountingService(1)
    for s in (a, b):
        svc.create_payment({"username": s.username, "amount": 10}, actor="t")
    names = lambda items: {i["username"] for i in items}  # noqa: E731
    assert {a.username, b.username} <= names(svc.reports(report_type="subscriber_payments"))
    assert names(svc.reports(report_type="subscriber_payments", prepaid=True)) == {a.username}
    assert names(svc.reports(report_type="subscriber_payments", prepaid=False)) == {b.username}


def test_web_form_saves_prepaid_and_auto_renew_mode(client, app):
    _web_login(client)
    pid = _plan(prepaid=1)
    client.get(f"/admin/radius/plans/{pid}/edit")
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token", "")
    res = client.post(f"/admin/radius/plans/{pid}", data={
        "_csrf_token": csrf, "name": "ff-web-" + uuid4().hex[:5], "plan_type": "time",
        "service_type": "Hotspot", "speed_down_kbps": "4096", "speed_up_kbps": "1024",
        "enabled": "1", "auto_renew_mode": "debt"})
    assert res.status_code in (302, 303) and "/login" not in res.headers.get("Location", "")
    row = _db().execute("SELECT prepaid, auto_renew, auto_renew_mode FROM access_plans "
                        "WHERE id=?", (pid,)).fetchone()
    assert (row["prepaid"], row["auto_renew"], row["auto_renew_mode"]) == (0, 1, "debt")
