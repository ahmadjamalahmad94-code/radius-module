"""Fix wave 2 (stream plansreports) — dashboard parity, service-request state,
payment discount, report snapshots, event amounts, notifications confirm.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "fix2-misc-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
EXPIRE = datetime(2030, 1, 1, 12, 0, 0)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fix2_misc.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fix2-misc-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_fix2m")
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


def _web_login(client) -> str:
    res = client.post("/admin/radius/login",
                      data={"username": "owner_fix2m", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


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


def _sub(*, plan_id=None, status="enabled", expire_at=EXPIRE, username=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "u_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Fix2 User", mobile="0599000000", status=status, expire_at=expire_at))
    return subscribers_repo.get_subscriber(1, username)


def _data(res, status=200):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is True, body
    return body["data"]


def _err(res, status):
    body = res.get_json()
    assert res.status_code == status, body
    assert body["ok"] is False, body
    return body["error"]


def _count(sql, *args) -> int:
    return int(_db().execute(sql, args).fetchone()[0] or 0)


# ─────────────── item 7: dashboard parity ───────────────

def _batch(code, n, plan_id) -> int:
    now = datetime.utcnow().isoformat() + "Z"
    bid = int(_db().execute(
        "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, generated, created_at) "
        "VALUES(1,?,?,?,?,?)", (code, plan_id, n, n, now)).lastrowid)
    for i in range(n):
        _db().execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, created_at) "
            "VALUES(1,?,?,?,?,0,?)", (bid, f"{code}_{i}", "x", plan_id, now))
    return bid


def test_dashboard_card_counts_are_one_source_without_archived_batches(client):
    from app.radius.db.repos import cards_repo
    pid = _plan()
    _batch("live", 3, pid)
    dead = _batch("dead", 5, pid)
    assert cards_repo.archive_batch(1, dead, actor="t")
    # a legacy archived batch whose cards were never cascaded must not count either
    legacy = _batch("legacy", 4, pid)
    _db().execute("UPDATE card_batches SET deleted_at=? WHERE id=?",
                  (datetime.utcnow().isoformat() + "Z", legacy))

    api = _data(client.get("/api/v1/dashboard", headers=AUTH))
    assert api["cards"]["available"] == 3 and api["cards"]["total"] == 3
    assert api["cards"]["batches"] == 1
    assert api["available_cards"] == 3 and api["total_batches"] == 1

    from app.radius.routes.dashboard import _card_batch_dashboard_summary
    web = _card_batch_dashboard_summary(1)
    assert web["printed"]["available"] + web["electronic"]["available"] == 3
    assert web["printed"]["batches"] + web["electronic"]["batches"] == 1
    _web_login(client)
    assert client.get("/admin/radius/").status_code == 200


def test_dashboard_status_groups_partition_the_total(client):
    pid = _plan()
    _sub(plan_id=pid)                                   # active
    _sub(plan_id=pid, expire_at=datetime(2020, 1, 1))   # expired (derived)
    _sub(plan_id=pid, status="disabled")
    _sub(plan_id=pid, status="pending")                 # falls through the 5 groups
    _sub(plan_id=pid, status="weird")                   # unknown status
    subs = _data(client.get("/api/v1/dashboard", headers=AUTH))["subscribers"]
    groups = ("active", "expired", "disabled", "suspended", "banned", "other")
    assert sum(subs[k] for k in groups) == subs["total"] == 5
    assert subs["other"] == 2


def test_dashboard_online_counts_only_known_subscribers_and_cards(client):
    pid = _plan()
    s = _sub(plan_id=pid)
    _batch("onl", 1, pid)
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for name in (s.username, "onl_0", "T-AA:BB:CC:DD:EE:FF", "ghost_user"):
        _db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
            "nasipaddress, acctstarttime, acctupdatetime) VALUES(1,?,?,?,?,?,?)",
            (uuid4().hex, uuid4().hex, name, "10.0.0.1", now, now))
    api = _data(client.get("/api/v1/dashboard", headers=AUTH))
    assert api["subscribers"]["online"] == 2 and api["online_now"] == 2
    from app.radius.services.dashboard_reports import DashboardReportsService
    assert DashboardReportsService(tenant_id=1).executive_summary()["subscribers"]["online"] == 2


# ─────────────── item 8: service-request state machine ───────────────

def _service_request(client) -> int:
    s = _sub(plan_id=_plan())
    res = client.post("/api/v1/service-requests", headers=AUTH, json={
        "subscriber_id": s.id, "service_key": "customer_portal", "request_type": "activation"})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["ticket"]["id"]


def test_rejected_service_request_cannot_be_reopened_and_approved(client):
    tid = _service_request(client)
    _data(client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                      json={"decision": "reject"}))
    err = _err(client.patch(f"/api/v1/tickets/{tid}", headers=AUTH, json={"status": "open"}), 409)
    assert "تم البتّ" in err["message"]
    _err(client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                     json={"decision": "approve", "expected_status": "open"}), 409)
    assert _data(client.get(f"/api/v1/tickets/{tid}", headers=AUTH))["ticket"]["status"] == "closed"
    # recategorising is not a way around the state machine either
    _err(client.patch(f"/api/v1/tickets/{tid}", headers=AUTH, json={"category": "general"}), 409)
    # other fields stay editable
    assert _data(client.patch(f"/api/v1/tickets/{tid}", headers=AUTH,
                              json={"priority": "high"}))["priority"] == "high"

    # an OPEN request moves only via decisions; a manual close is allowed and final
    tid2 = _service_request(client)
    _err(client.patch(f"/api/v1/tickets/{tid2}", headers=AUTH, json={"status": "in_progress"}), 409)
    assert _data(client.patch(f"/api/v1/tickets/{tid2}", headers=AUTH,
                              json={"status": "closed"}))["status"] == "closed"

    # web «حفظ الحالة» shares the rule
    csrf = _web_login(client)
    res = client.post(f"/admin/radius/tickets/{tid}/status",
                      data={"status": "open", "_csrf_token": csrf}, follow_redirects=True)
    assert "تم البتّ" in res.get_data(as_text=True)
    assert _data(client.get(f"/api/v1/tickets/{tid}", headers=AUTH))["ticket"]["status"] == "closed"

    # ordinary tickets keep the free status change
    s = _sub(plan_id=_plan())
    plain = _data(client.post("/api/v1/tickets", headers=AUTH,
                              json={"subscriber_id": s.id, "subject": "plain"}), 201)["id"]
    _data(client.patch(f"/api/v1/tickets/{plain}", headers=AUTH, json={"status": "closed"}))
    assert _data(client.patch(f"/api/v1/tickets/{plain}", headers=AUTH,
                              json={"status": "open"}))["status"] == "open"


# ─────────────── item 8: notifications confirm ───────────────

def test_web_mark_all_read_asks_for_confirmation(client):
    _web_login(client)
    html = client.get("/admin/radius/notifications").get_data(as_text=True)
    form_at = html.index("/notifications/read-all")
    snippet = html[form_at:form_at + 1500]
    assert "data-confirm=" in snippet and "كلّ إشعارات الشبكة" in snippet


# ─────────────── item 8: discount larger than the price ───────────────

def test_discount_larger_than_the_price_is_refused_everywhere(client):
    pid = _plan(price=5.0, days=1)
    s = _sub(plan_id=pid)
    err = _err(client.post("/api/v1/payments", headers=AUTH, json={
        "username": s.username, "amount": 5, "discount_amount": 50}), 422)
    assert "الخصم" in err["message"]
    err = _err(client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH, json={
        "amount": 5, "discount_amount": 50}), 422)
    assert "الخصم" in err["message"]
    csrf = _web_login(client)
    res = client.post(f"/admin/radius/users/{s.username}/payments", data={
        "_csrf_token": csrf, "amount": "5", "discount_amount": "50", "method": "cash"},
        follow_redirects=True)
    assert "الخصم" in res.get_data(as_text=True)
    assert _count("SELECT COUNT(*) FROM payment_transactions WHERE username=?", s.username) == 0
    # a discount within the price still works
    ok_pay = _data(client.post("/api/v1/payments", headers=AUTH, json={
        "username": s.username, "amount": 3, "discount_amount": 2}), 201)["payment"]
    assert ok_pay["effective_price"] == 3.0


# ─────────────── item 8: report snapshots API ───────────────

def test_report_snapshot_api_validation_is_arabic_and_422(client):
    err = _err(client.post("/api/v1/reports/snapshots", headers=AUTH, json=[1, 2]), 422)
    assert "كائن JSON" in err["message"]
    for body in ({"report_type": "daily", "date_from": "2026-09-30", "date_to": "2026-09-01"},
                 {"report_type": "daily", "date_from": "garbage"},
                 {"report_type": "daily", "date_to": "2026-02-31"}):
        err = _err(client.post("/api/v1/reports/snapshots", headers=AUTH, json=body), 422)
        assert "التاريخ" in err["message"] or "يسبق" in err["message"]
    err = _err(client.post("/api/v1/reports/snapshots", headers=AUTH,
                           json={"report_type": "nope"}), 422)
    assert err["message"] == "نوع التقرير غير مدعوم."
    err = _err(client.get("/api/v1/reports/snapshots/999999", headers=AUTH), 404)
    assert err["message"] == "اللقطة غير موجودة."
    ok = _data(client.post("/api/v1/reports/snapshots", headers=AUTH, json={
        "report_type": "daily", "date_from": "2026-09-01", "date_to": "2026-09-30"}), 201)
    assert ok["snapshot"]["date_from"] == "2026-09-01"


# ─────────────── item 8: event money formatting ───────────────

def test_ledger_event_amounts_are_formatted_not_float_repr(client):
    from app.radius.db.connection import transaction
    from app.radius.db.repos import accounting_repo
    with transaction() as conn:
        accounting_repo.create_ledger_entry(
            conn, tenant_id=1, entry_type="void", amount=-1000000000.0, direction="debit",
            currency="ILS", username="r03_u020", operator="t")
    msg = _db().execute("SELECT message FROM business_events WHERE event_key='ledger.void' "
                        "ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "-1,000,000,000.00 ILS" in msg and "e+" not in msg
    # an old row stored with the float repr is shown formatted too
    _db().execute("UPDATE business_events SET message='إلغاء قيد: -1e+09 ILS — r03_u020' "
                  "WHERE event_key='ledger.void'")
    events = _data(client.get("/api/v1/events?category=financial", headers=AUTH))["items"]
    shown = next(e for e in events if e["event_key"] == "ledger.void")
    assert "-1,000,000,000.00 ILS" in shown["message"]
    from app.radius.services.events_risk_center import EventsRiskCenterService
    center = EventsRiskCenterService(tenant_id=1).list_events(category="financial")
    assert all("e+" not in str(e.get("message")) for e in center)
