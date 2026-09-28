"""Stress-campaign «core» fixes (2026-09-28) — systemic robustness.

1. SQLite «database is locked» under concurrency: ``transaction()`` is now
   ``BEGIN IMMEDIATE`` (the read→write upgrade of a deferred txn failed in ~3 ms
   without waiting for busy_timeout), nestable (SAVEPOINT) and runs side effects
   only after COMMIT (``after_commit``).
2. Multi-row money actions are ONE transaction (extend / payment / loan): a
   failure leaves nothing half-recorded.
3. Infinity / NaN / 1e400 rejected (422 Arabic) — and never written to the DB,
   never emitted in JSON.
4. Unhandled exceptions under /api/ → JSON envelope (500), lock → 503 retryable.
5. Datetimes with an offset are stored naive UTC; legacy aware values no longer
   break the dashboard.
6. Money rounded to 2 decimals at write.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

TOKEN = "core-robust-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
FETCH = {"X-Requested-With": "fetch"}
EXPIRE = datetime(2030, 1, 1, 12, 0, 0)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "core.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "core-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_core")
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
    res = client.post("/admin/radius/login", data={"username": "owner_core", "password": "owner-pass"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _plan(*, price=30.0, days=30) -> int:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat()
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, "
        "price, currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        ("p_" + uuid4().hex[:6], days * 1440, days, price, "ILS", now, now))
    return int(cur.lastrowid)


def _sub(username=None, *, plan_id=None, balance=0.0, expire_at=EXPIRE):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo
    username = username or "cr_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Core User", mobile="0599000000", status="enabled", expire_at=expire_at))
    db().execute("UPDATE subscribers SET balance=? WHERE tenant_id=1 AND username=?",
                 (float(balance), username))
    return subscribers_repo.get_subscriber(1, username)


def _snapshot(username) -> dict:
    from app.radius.db.connection import db
    sub = dict(db().execute(
        "SELECT balance, expire_at, status FROM subscribers WHERE tenant_id=1 AND username=?",
        (username,)).fetchone())
    count = lambda sql: db().execute(sql, (username,)).fetchone()[0]  # noqa: E731
    return {
        "sub": sub,
        "ledger": count("SELECT COUNT(*) FROM accounting_ledger_entries WHERE username=?"),
        "payments": count("SELECT COUNT(*) FROM payment_transactions WHERE username=?"),
        "loans": count("SELECT COUNT(*) FROM loan_entries WHERE username=?"),
    }


def _err(res, status, code=None):
    body = res.get_json()
    assert res.status_code == status, (res.status_code, res.data[:300])
    assert body is not None and body["ok"] is False, body
    if code:
        assert body["error"]["code"] == code, body
    return body["error"]


# ═══════════════ 1. transaction mechanics ═══════════════

@pytest.fixture
def raw_db(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tx.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    from app.radius.db.connection import db, reset_for_tests
    reset_for_tests(db_file)
    db().execute("CREATE TABLE counter(id INTEGER PRIMARY KEY, n INTEGER NOT NULL)")
    db().execute("INSERT INTO counter(id, n) VALUES(1, 0)")
    db().execute("CREATE TABLE t(v TEXT)")
    yield db_file
    reset_for_tests(None)


def test_concurrent_read_then_write_no_locked_no_lost_update(raw_db):
    """The a04 pattern: SELECT then UPDATE inside one transaction, many threads.
    Deferred BEGIN failed in ms with «database is locked» (and lost updates);
    BEGIN IMMEDIATE queues on busy_timeout and serialises the read-modify-write."""
    from app.radius.db.connection import close_thread_conn, transaction

    threads_n, per_thread = 8, 25
    errors: list[BaseException] = []
    start = threading.Barrier(threads_n)

    def worker():
        try:
            start.wait()
            for _ in range(per_thread):
                with transaction() as conn:
                    n = conn.execute("SELECT n FROM counter WHERE id=1").fetchone()[0]
                    conn.execute("INSERT INTO t(v) VALUES('x')")  # widen the window
                    conn.execute("UPDATE counter SET n=? WHERE id=1", (n + 1,))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            close_thread_conn()

    ts = [threading.Thread(target=worker) for _ in range(threads_n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(120)
    assert not errors, errors[:3]
    from app.radius.db.connection import db
    assert db().execute("SELECT n FROM counter WHERE id=1").fetchone()[0] == threads_n * per_thread


def test_busy_timeout_applied_on_every_connection(raw_db):
    from app.radius.db.connection import BUSY_TIMEOUT_MS, db
    assert BUSY_TIMEOUT_MS >= 15000
    assert db().execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    assert db().execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_nested_transaction_is_a_savepoint(raw_db):
    from app.radius.db.connection import db, transaction

    with transaction() as conn:
        conn.execute("INSERT INTO t(v) VALUES('outer')")
        with pytest.raises(RuntimeError):
            with transaction() as inner:
                inner.execute("INSERT INTO t(v) VALUES('inner-rolled-back')")
                raise RuntimeError("inner fails")
        with transaction() as inner:
            inner.execute("INSERT INTO t(v) VALUES('inner-kept')")
    rows = [r[0] for r in db().execute("SELECT v FROM t ORDER BY rowid")]
    assert rows == ["outer", "inner-kept"]

    with pytest.raises(ValueError):
        with transaction() as conn:
            with transaction() as inner:
                inner.execute("INSERT INTO t(v) VALUES('all-or-nothing')")
            raise ValueError("outer fails after the inner committed its savepoint")
    assert db().execute("SELECT COUNT(*) FROM t WHERE v='all-or-nothing'").fetchone()[0] == 0
    assert not db().in_transaction


def test_after_commit_runs_only_after_commit(raw_db):
    from app.radius.db.connection import after_commit, transaction

    seen: list[str] = []
    with transaction():
        after_commit(lambda: seen.append("a"))
        with transaction():
            after_commit(lambda: seen.append("b"))
        assert seen == []          # nothing before COMMIT
    assert seen == ["a", "b"]

    seen.clear()
    with pytest.raises(RuntimeError):
        with transaction():
            after_commit(lambda: seen.append("dropped"))
            raise RuntimeError
    assert seen == []              # rolled back → side effect dropped

    seen.clear()
    with transaction():
        with pytest.raises(RuntimeError):
            with transaction():
                after_commit(lambda: seen.append("inner-dropped"))
                raise RuntimeError
        after_commit(lambda: seen.append("outer-kept"))
    assert seen == ["outer-kept"]

    after_commit(lambda: seen.append("now"))  # no transaction → immediate
    assert seen[-1] == "now"


def test_leaked_transaction_is_released(raw_db):
    from app.radius.db.connection import db, release_leaked_transaction
    db().execute("BEGIN IMMEDIATE")
    db().execute("INSERT INTO t(v) VALUES('leak')")
    assert release_leaked_transaction() is True
    assert not db().in_transaction
    assert db().execute("SELECT COUNT(*) FROM t WHERE v='leak'").fetchone()[0] == 0
    assert release_leaked_transaction() is False


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_float_never_reaches_the_db(raw_db, bad):
    from app.radius.core.numbers import NonFiniteNumber
    from app.radius.db.connection import db
    db().execute("CREATE TABLE IF NOT EXISTS m(x REAL)")
    with pytest.raises(NonFiniteNumber):
        db().execute("INSERT INTO m(x) VALUES(?)", (bad,))
    db().execute("INSERT INTO m(x) VALUES(?)", (1.25,))
    assert db().execute("SELECT x FROM m").fetchall()[0][0] == 1.25


# ═══════════════ 3. numbers / JSON ═══════════════

@pytest.mark.parametrize("raw", ["inf", "Infinity", "-inf", "nan", "NaN", "1e400",
                                 float("inf"), float("nan"), True, "abc"])
def test_finite_float_rejects(raw):
    from app.radius.core.errors import RadiusValidationError
    from app.radius.core.numbers import finite_float
    with pytest.raises(RadiusValidationError) as ei:
        finite_float(raw, field="amount")
    assert "المبلغ" in ei.value.message
    with pytest.raises(ValueError):   # old `except (TypeError, ValueError)` still catches it
        finite_float(raw, field="amount")


def test_finite_float_accepts_and_rounds():
    from app.radius.core.numbers import finite_float, money_float, round_money
    assert finite_float("12.5", field="amount") == 12.5
    assert finite_float("", field="amount", default=0.0) == 0.0
    assert money_float("10.005", field="amount") == 10.01 or money_float("10.005") == 10.0
    assert round_money(-42.89999999999999) == -42.9
    assert round_money(-0.0) == 0.0 and math.copysign(1, round_money(-0.0)) == 1


def test_jsonify_never_emits_infinity_or_nan(app):
    from flask import jsonify
    with app.test_request_context():
        body = jsonify({"a": float("inf"), "b": [1.5, float("nan")], "c": {"d": float("-inf")}})
        text = body.get_data(as_text=True)
    assert "Infinity" not in text and "NaN" not in text
    assert json.loads(text) == {"a": None, "b": [1.5, None], "c": {"d": None}}


def test_list_endpoint_survives_a_legacy_infinite_row(client):
    """A row already holding Infinity (written before the fix) must not break
    the whole list JSON for the app."""
    from app.radius.db.connection import db
    s = _sub(balance=1.0)
    conn = sqlite3.connect(os.environ["HOBERADIUS_DB_PATH"])
    conn.execute("UPDATE subscribers SET balance=9e999 WHERE username=?", (s.username,))
    conn.commit()
    conn.close()
    assert db().execute("SELECT balance FROM subscribers WHERE username=?",
                        (s.username,)).fetchone()[0] == float("inf")
    res = client.get("/api/v1/accounts?limit=50", headers=AUTH)
    assert res.status_code == 200
    json.loads(res.get_data(as_text=True))          # strict JSON parse
    assert "Infinity" not in res.get_data(as_text=True)


# ═══════════════ 3b. NaN / Infinity via API and web → 422, nothing written ═══════════════

@pytest.mark.parametrize("amount", ["nan", "NaN", "Infinity", "inf", "1e400"])
def test_api_extend_rejects_non_finite_amount(client, amount):
    pid = _plan()
    s = _sub(plan_id=pid, balance=-100.0)
    before = _snapshot(s.username)
    err = _err(client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH, json={
        "mode": "duration", "minutes": 1440, "charge_mode": "debt", "amount": amount}), 422)
    assert err["code"] == "validation_error"
    assert _snapshot(s.username) == before


def test_api_extend_rejects_json_nan_literal(client):
    pid = _plan()
    s = _sub(plan_id=pid, balance=-62831.0)
    before = _snapshot(s.username)
    res = client.post(f"/api/v1/accounts/{s.username}/extend", headers={**AUTH,
                      "Content-Type": "application/json"},
                      data='{"mode":"duration","minutes":1440,"charge_mode":"paid","amount":NaN}')
    _err(res, 422, "validation_error")
    assert _snapshot(s.username) == before          # debt NOT wiped to 0


@pytest.mark.parametrize("path,body", [
    ("payment", {"amount": "Infinity", "method": "cash"}),
    ("payment", {"amount": "nan", "method": "cash"}),
    ("balance", {"amount": "1e400"}),
    ("quota/topup", {"quota_mb": 100, "charge_mode": "paid", "amount": "inf"}),
    ("quota/reset-daily", {"charge_mode": "paid", "amount": "nan"}),
])
def test_api_money_actions_reject_non_finite(client, path, body):
    pid = _plan()
    s = _sub(plan_id=pid, balance=5.0)
    before = _snapshot(s.username)
    res = client.post(f"/api/v1/accounts/{s.username}/{path}", headers=AUTH, json=body)
    if res.status_code == 404:
        pytest.skip(f"/{path} not routed in this build")
    _err(res, 422)
    assert _snapshot(s.username) == before


@pytest.mark.parametrize("balance", ["inf", "-Infinity", "nan", "1e400"])
def test_api_patch_balance_rejects_non_finite(client, balance):
    s = _sub(balance=3.0)
    _err(client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                      json={"balance": balance}), 422, "validation_error")
    from app.radius.db.repos import subscribers_repo
    assert subscribers_repo.get_subscriber(1, s.username).balance == 3.0


def test_web_extend_rejects_nan_amount(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid, balance=-50.0)
    before = _snapshot(s.username)
    res = client.post(f"/admin/radius/users/{s.username}/extend", data={
        "_csrf_token": csrf, "minutes": "1440", "charge_mode": "debt", "amount": "nan",
        "currency": "ILS"})
    assert res.status_code in {302, 303}
    assert _snapshot(s.username) == before


def test_web_payment_rejects_infinite_amount(client):
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid)
    before = _snapshot(s.username)
    res = client.post(f"/admin/radius/users/{s.username}/payments", headers=FETCH, data={
        "_csrf_token": csrf, "amount": "Infinity", "currency": "ILS", "method": "cash",
        "apply_to_radius": "1"})
    assert res.status_code in {302, 303, 400}
    assert _snapshot(s.username) == before


# ═══════════════ 2. atomic multi-row actions ═══════════════

def test_api_extend_is_atomic_when_ledger_insert_fails(client, monkeypatch):
    """a02 CRIT: the subscriber (time + balance) was saved, THEN the ledger
    insert failed → HTML 500 with half the action committed."""
    from app.radius.db.repos import accounting_repo
    pid = _plan()
    s = _sub(plan_id=pid, balance=10.0)
    before = _snapshot(s.username)

    def boom(*a, **kw):
        raise RuntimeError("simulated ledger failure")
    monkeypatch.setattr(accounting_repo, "create_ledger_entry", boom)
    res = client.post(f"/api/v1/accounts/{s.username}/extend", headers=AUTH, json={
        "mode": "duration", "minutes": 1440, "charge_mode": "paid", "amount": 3})
    err = _err(res, 500, "server_error")
    assert "خطأ" in err["message"]
    assert _snapshot(s.username) == before          # time NOT added, balance NOT touched


def test_web_extend_is_atomic_when_ledger_insert_fails(client, app, monkeypatch):
    from app.radius.db.repos import accounting_repo
    csrf = _web_login(client)
    pid = _plan()
    s = _sub(plan_id=pid, balance=10.0)
    before = _snapshot(s.username)
    monkeypatch.setattr(accounting_repo, "create_ledger_entry",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    app.config["PROPAGATE_EXCEPTIONS"] = False
    res = client.post(f"/admin/radius/users/{s.username}/extend", data={
        "_csrf_token": csrf, "minutes": "1440", "charge_mode": "paid", "amount": "3",
        "currency": "ILS"})
    assert res.status_code in {302, 303, 500}
    assert _snapshot(s.username) == before


def test_api_payment_is_atomic_when_time_apply_hits_a_lock(client, monkeypatch):
    """a10 F1: payment + ledger were kept while the time apply failed (locked)."""
    from app.radius.services import accounting as acc
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    before = _snapshot(s.username)

    def locked(**kw):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(acc, "apply_activation_minutes", locked)
    res = client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH,
                      json={"amount": 30, "method": "cash"})
    err = _err(res, 503, "server_busy")
    assert "أعد المحاولة" in err["message"] and err["details"]["retryable"] is True
    assert res.headers.get("Retry-After")
    assert _snapshot(s.username) == before          # no payment, no ledger credit


def test_api_payment_is_atomic_when_loan_settlement_fails(client, monkeypatch):
    from app.radius.db.repos import accounting_repo
    from app.radius.services.accounting import AccountingService
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    loan = AccountingService(1).create_loan({
        "username": s.username, "days": "2", "hours": "0", "price_from_days": True,
        "amount": 0, "currency": "ILS", "reason": "seed", "apply_to_radius": False},
        actor="seed")
    before = _snapshot(s.username)
    monkeypatch.setattr(accounting_repo, "settle_loan",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("settle boom")))
    res = client.post(f"/api/v1/accounts/{s.username}/payment", headers=AUTH, json={
        "amount": 30, "method": "cash",
        "loan_actions": [{"loan_id": loan["id"], "action": "settle"}]})
    _err(res, 500, "server_error")
    assert _snapshot(s.username) == before
    assert accounting_repo.get_loan(1, int(loan["id"]))["status"] == "open"


def test_api_loan_is_atomic_when_apply_fails(client, monkeypatch):
    """a04 CRIT: 500 (locked) during apply still recorded the loan + debit."""
    from app.radius.services import accounting as acc
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    before = _snapshot(s.username)
    monkeypatch.setattr(acc, "apply_activation_minutes",
                        lambda **kw: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    res = client.post(f"/api/v1/accounts/{s.username}/loan", headers=AUTH,
                      json={"loan_type": "debt", "days": 2})
    _err(res, 503, "server_busy")
    assert _snapshot(s.username) == before


def test_parallel_extends_on_one_subscriber_lose_nothing(app):
    """a02 HIGH: 8 parallel «+1 day» on the same subscriber lost updates."""
    from app.radius.db.connection import close_thread_conn
    from app.radius.services import subscriber_actions as sa
    pid = _plan()
    s = _sub(plan_id=pid, balance=100.0)
    caller = sa.ActionCaller(tenant_id=1, admin_id=None, is_super=True, actor="t")
    n = 8
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def worker():
        try:
            with app.app_context():
                barrier.wait()
                sa.extend_subscriber(caller, s.username, minutes=1440,
                                     charge_mode="paid", amount=2.5, currency="ILS")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            close_thread_conn()

    ts = [threading.Thread(target=worker) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(120)
    assert not errors, errors[:3]
    from app.radius.db.repos import subscribers_repo
    after = subscribers_repo.get_subscriber(1, s.username)
    assert after.expire_at == EXPIRE + timedelta(days=n)
    assert after.balance == 100.0 - 2.5 * n
    assert _snapshot(s.username)["ledger"] == n


# ═══════════════ 4. JSON 500 envelope ═══════════════

def test_api_unhandled_exception_returns_json_envelope(client, monkeypatch):
    from app.api.v1 import accounts as accounts_api

    class Boom:
        def get(self, username):
            raise KeyError("unexpected")
    monkeypatch.setattr(accounts_api, "_svc", lambda: Boom())
    res = client.get("/api/v1/accounts/whoever", headers=AUTH)
    assert res.is_json
    err = _err(res, 500, "server_error")
    assert err["message"].startswith("حدث خطأ غير متوقع")


def test_api_lock_error_is_503_retryable(client, monkeypatch):
    from app.api.v1 import accounts as accounts_api

    class Locked:
        def get(self, username):
            raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(accounts_api, "_svc", lambda: Locked())
    res = client.get("/api/v1/accounts/whoever", headers=AUTH)
    err = _err(res, 503, "server_busy")
    assert err["message"] == "الخادم مشغول الآن، أعد المحاولة بعد لحظات."
    assert res.headers["Retry-After"] == "2"


def test_api_404_stays_http_error(client):
    res = client.get("/api/v1/no-such-endpoint-xyz", headers=AUTH)
    assert res.status_code in {404, 405}     # HTTP errors keep their own status


# ═══════════════ 5. datetimes ═══════════════

def test_parse_iso_utc_normalises_offsets():
    from app.radius.core.timeparse import parse_iso_utc
    want = datetime(2026, 10, 1, 7, 0, 0)
    assert parse_iso_utc("2026-10-01T10:00:00+03:00") == want
    assert parse_iso_utc("2026-10-01T07:00:00Z") == want
    assert parse_iso_utc("2026-10-01T07:00:00") == want
    assert parse_iso_utc("2026-10-01T10:00:00+03:00Z") == want      # legacy stored form
    assert parse_iso_utc("garbage") is None
    with pytest.raises(ValueError):
        parse_iso_utc("garbage", strict=True)


def test_db_helpers_never_return_aware():
    from datetime import timezone
    from app.radius.db.helpers import dt_to_iso, parse_dt
    assert parse_dt("2026-10-01T10:00:00+03:00Z") == datetime(2026, 10, 1, 7, 0)
    aware = datetime(2026, 10, 1, 10, 0, tzinfo=timezone(timedelta(hours=3)))
    assert dt_to_iso(aware) == "2026-10-01T07:00:00Z"


def test_api_patch_offset_expiry_stored_naive_utc_and_dashboard_renders(client, app):
    from app.radius.db.connection import db
    s = _sub()
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"expire_at": "2026-10-01T10:00:00+03:00"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["expire_at"] == "2026-10-01T07:00:00Z"
    raw = db().execute("SELECT expire_at FROM subscribers WHERE username=?",
                       (s.username,)).fetchone()[0]
    assert raw == "2026-10-01T07:00:00Z"
    _web_login(client)
    from flask import url_for
    with app.test_request_context():
        dash = url_for("radius.dashboard")
    assert client.get(dash).status_code == 200


def test_dashboard_tolerates_legacy_aware_rows(client, app):
    from app.radius.db.connection import db
    s = _sub()
    db().execute("UPDATE subscribers SET expire_at=? WHERE username=?",
                 ("2020-01-01T10:00:00+03:00Z", s.username))
    from app.radius.db.repos import subscribers_repo
    assert subscribers_repo.get_subscriber(1, s.username).expire_at.tzinfo is None
    from app.radius.services.dashboard import get_dashboard_service
    snap = get_dashboard_service().snapshot()
    assert snap.expired_subscribers >= 1
    _web_login(client)
    from flask import url_for
    with app.test_request_context():
        dash = url_for("radius.dashboard")
    assert client.get(dash).status_code == 200


# ═══════════════ 6. money rounding ═══════════════

def test_balance_float_noise_rounded_at_write(client):
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo
    s = _sub()
    from dataclasses import replace
    subscribers_repo.upsert_subscriber(replace(s, balance=-42.89999999999999))
    raw = db().execute("SELECT balance FROM subscribers WHERE username=?",
                       (s.username,)).fetchone()[0]
    assert raw == -42.9


def test_ledger_amount_rounded_at_write(client):
    from app.radius.db.connection import db, transaction
    from app.radius.db.repos import accounting_repo
    with transaction() as conn:
        eid = accounting_repo.create_ledger_entry(
            conn, tenant_id=1, entry_type="cash_balance", amount=0.1 + 0.2,
            direction="credit", currency="ILS", username="x")
    assert db().execute("SELECT amount FROM accounting_ledger_entries WHERE id=?",
                        (eid,)).fetchone()[0] == 0.3


def test_migration_173_normalises_stored_offset_datetimes():
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent / "app" / "radius" / "db" / "migrations"
           / "173_subscribers_offset_datetimes_to_utc.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE subscribers(expire_at TEXT, first_login_at TEXT, "
                 "last_login_at TEXT, last_seen_at TEXT)")
    for v in ("2026-10-01T10:00:00+03:00Z", "2026-10-01T07:00:00Z",
              "2026-10-01T07:00:00.5Z", "2026-10-01T10:00:00-02:30", None):
        conn.execute("INSERT INTO subscribers(expire_at) VALUES(?)", (v,))
    conn.executescript(sql)
    conn.executescript(sql)   # idempotent
    assert [r[0] for r in conn.execute("SELECT expire_at FROM subscribers")] == [
        "2026-10-01T07:00:00Z", "2026-10-01T07:00:00Z", "2026-10-01T07:00:00.5Z",
        "2026-10-01T12:30:00Z", None]


def test_web_payment_and_loan_are_atomic_when_apply_fails(client, monkeypatch):
    """Web parity: the panel runs the same one-transaction helpers."""
    from app.radius.services import accounting as acc
    csrf = _web_login(client)
    pid = _plan(price=30.0, days=30)
    s = _sub(plan_id=pid)
    before = _snapshot(s.username)
    monkeypatch.setattr(acc, "apply_activation_minutes",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("apply boom")))
    r = client.post(f"/admin/radius/users/{s.username}/payments", headers=FETCH, data={
        "_csrf_token": csrf, "amount": "30", "currency": "ILS", "method": "cash",
        "apply_to_radius": "1"})
    assert r.status_code == 500 and r.get_json()["ok"] is False
    r = client.post(f"/admin/radius/users/{s.username}/loans", headers=FETCH, data={
        "_csrf_token": csrf, "days": "2", "hours": "0", "price_from_days": "1",
        "currency": "ILS", "apply_to_radius": "1"})
    assert r.status_code == 500 and r.get_json()["ok"] is False
    assert _snapshot(s.username) == before
