"""End-to-end verification: EVERY card-store customer action emits a store
event into audit_log (the log that feeds the ``card_store_events`` report),
tagged with the correct Arabic type + result, and never leaks a password.

The report at ``/admin/radius/reports/card_store_events`` used to record only a
subset (login + card issuance). This suite locks in the full taxonomy that the
API store handlers now emit and the report decorates:

    registration · login · logout · purchase · card-login (redeem) · deposit ·
    withdrawal  — plus the *_failed variants.

Tests are isolated per file: each builds its own temp DB with the real
migrations (test_isolation_per_file pattern), so no state leaks between tests.

Run this file on its own.
"""
from __future__ import annotations

import json
import os

import pytest


# ── app / client fixtures (real DB + real migrations) ────────────────────────

@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_store_events.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)

    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()

    # In-process throttle state is module-global — clear it so a fresh test is
    # never rate-limited by a sibling test's register/login attempts.
    import app.api.v1.store as store_mod
    store_mod._register_attempts.clear()
    store_mod._login_failures.clear()

    yield application
    reset_for_tests(None)


@pytest.fixture
def client(app):
    return app.test_client()


# ── low-level helpers (call inside an app context) ───────────────────────────

def _db():
    from app.radius.db.connection import db
    return db()


def _market():
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService,
    )
    return CardUsersMarketplaceService(tenant_id=1)


def _audit(action=None):
    sql = "SELECT * FROM audit_log"
    params: list = []
    if action:
        sql += " WHERE action = ?"
        params.append(action)
    sql += " ORDER BY id"
    return [dict(r) for r in _db().execute(sql, params).fetchall()]


def _token(card_user_id):
    from app.radius.services.store_token import issue_store_token
    return issue_store_token(card_user_id=int(card_user_id), tenant_id=1)


def _bearer(token, *, json_body=True):
    h = {"Authorization": f"Bearer {token}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _decorate(app, rows):
    """Run the report's row decorator inside a tenant-1 request context so the
    identity/label resolution matches exactly what the report renders."""
    from flask import g
    from app.radius.routes.reports import _decorate_card_store_rows
    with app.test_request_context("/"):
        g.tenant_id = 1
        return _decorate_card_store_rows(rows)


def _seed_user(*, name="عميل تجريبي", mobile="0599000001", password="", funded=0.0):
    svc = _market()
    kwargs = {"display_name": name, "mobile": mobile}
    if password:
        kwargs["password"] = password
    u = svc.create_card_user(**kwargs)
    if funded > 0:
        svc.recharge_wallet(card_user_id=int(u["id"]),
                            amount=f"{funded:.2f}", actor="seed")
    return int(u["id"])


def _seed_plan():
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes,"
        " validity_days, price, currency, created_at, updated_at)"
        " VALUES(1,'Store plan',480,1,5.0,'ILS',datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def _seed_package():
    return _market().create_package(
        name="8h / 2Mbps", plan_id=_seed_plan(),
        duration_minutes=480, speed_down_kbps=2048, speed_up_kbps=512,
        price="5.00")


def _seed_recharge_card(*, number="RC-001", pin="1234", value=10.0):
    conn = _db()
    plan_id = _seed_plan()
    b = conn.execute(
        "INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id,"
        " count, price_per_card, recharge_only, created_at)"
        " VALUES(1,'RB-001','Recharge',?,1,?,1,datetime('now'))",
        (plan_id, value))
    batch_id = int(b.lastrowid)
    conn.execute(
        "INSERT INTO cards(tenant_id, batch_id, plan_id, username, password,"
        " used, revoked, recharge_only, wallet_value, created_at)"
        " VALUES(1,?,?,?,?,0,0,1,?,datetime('now'))",
        (batch_id, plan_id, number, pin, value))
    conn.commit()
    return number, pin


# ── the emit helper never raises, even outside a Flask context ───────────────

class TestEmitStoreEventSilent:
    def test_no_exception_without_app_context(self):
        from app.api.v1.store import _emit_store_event
        # Must be a silent no-op — the audit write must never break the flow.
        _emit_store_event("store.test", card_user_id=1, mobile="0599000001")


# ── each REAL handler emits the right event with the right Arabic label ──────

class TestRealHandlersEmit:
    """Drive the actual /api/v1/store/* endpoints and assert the audit row +
    the report's decorated Arabic type/result for every customer action."""

    def test_registration_emits_event(self, client, app):
        resp = client.post("/api/v1/store/register",
                           json={"display_name": "أحمد علي محمد",
                                 "mobile": "0599123456",
                                 "password": "secret123"})
        assert resp.status_code == 201, resp.get_data(as_text=True)
        with app.app_context():
            rows = _audit("store.register")
        assert len(rows) == 1
        dec = _decorate(app, rows)
        assert dec[0]["action_label"] == "تسجيل"
        assert dec[0]["result_label"] == "تسجيل"
        assert dec[0]["login_ok"] is True
        # Never persist the plaintext password / a "password" key.
        blob = (rows[0]["payload_json"] or "").lower()
        assert "secret123" not in blob
        assert "password" not in blob

    def test_login_emits_event(self, client, app):
        with app.app_context():
            _seed_user(mobile="0599123457", password="pw123456")
        resp = client.post("/api/v1/store/login",
                           json={"mobile": "0599123457",
                                 "password": "pw123456"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        with app.app_context():
            rows = _audit("auth_login")
        assert len(rows) >= 1
        dec = _decorate(app, rows)
        assert dec[-1]["result_label"] == "دخول ناجح"
        assert dec[-1]["login_ok"] is True

    def test_login_failed_emits_event(self, client, app):
        with app.app_context():
            _seed_user(mobile="0599123458", password="right-pw")
        resp = client.post("/api/v1/store/login",
                           json={"mobile": "0599123458",
                                 "password": "wrong-pw"})
        assert resp.status_code == 401, resp.get_data(as_text=True)
        with app.app_context():
            rows = _audit("auth_login_failed")
        assert len(rows) == 1
        dec = _decorate(app, rows)
        assert dec[0]["result_label"] == "دخول فاشل"
        assert dec[0]["login_ok"] is False
        assert dec[0]["result_reason"]  # a human Arabic reason, not blank
        # The attempted password must not land in audit_log.
        assert "wrong-pw" not in (rows[0]["payload_json"] or "")

    def test_logout_emits_event(self, client, app):
        with app.app_context():
            tok = _token(_seed_user(mobile="0599123459"))
        resp = client.post("/api/v1/store/logout", headers=_bearer(tok))
        assert resp.status_code == 200, resp.get_data(as_text=True)
        with app.app_context():
            rows = _audit("store.logout")
        assert len(rows) == 1
        dec = _decorate(app, rows)
        assert dec[0]["action_label"] == "خروج"
        assert dec[0]["result_label"] == "خروج"
        assert dec[0]["login_ok"] is True

    def test_deposit_emits_event(self, client, app):
        with app.app_context():
            tok = _token(_seed_user(mobile="0599123460"))
        resp = client.post("/api/v1/store/deposits",
                           headers=_bearer(tok, json_body=False),
                           data={"amount": "50.00", "method": "bank"})
        assert resp.status_code == 201, resp.get_data(as_text=True)
        with app.app_context():
            rows = _audit("store.deposit")
        assert len(rows) == 1
        dec = _decorate(app, rows)
        assert dec[0]["action_label"] == "إيداع"
        assert dec[0]["result_label"] == "إيداع"
        assert dec[0]["login_ok"] is True
        assert "المبلغ: 50" in dec[0]["detail_display"]

    def test_withdrawal_emits_event(self, client, app):
        with app.app_context():
            tok = _token(_seed_user(mobile="0599123461", funded=100.0))
        resp = client.post("/api/v1/store/withdrawals",
                           headers=_bearer(tok),
                           json={"amount": "20.00",
                                 "payee_name": "محمد خالد",
                                 "payee_account": "12345678"})
        assert resp.status_code == 201, resp.get_data(as_text=True)
        with app.app_context():
            rows = _audit("store.withdrawal")
        assert len(rows) == 1
        dec = _decorate(app, rows)
        assert dec[0]["action_label"] == "سحب"
        assert dec[0]["result_label"] == "سحب"
        assert "المستفيد: محمد خالد" in dec[0]["detail_display"]

    def test_card_login_redeem_emits_event(self, client, app):
        with app.app_context():
            tok = _token(_seed_user(mobile="0599123462"))
            number, pin = _seed_recharge_card(number="RC-777", pin="1234",
                                              value=15.0)
        resp = client.post("/api/v1/store/redeem",
                           headers=_bearer(tok),
                           json={"card_number": number, "card_password": pin})
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        with app.app_context():
            rows = _audit("store.card_redeem")
        assert len(rows) == 1
        dec = _decorate(app, rows)
        assert dec[0]["action_label"] == "شحن بطاقة"
        assert dec[0]["result_label"] == "شحن بطاقة"
        assert dec[0]["login_ok"] is True

    def test_purchase_emits_event(self, client, app):
        with app.app_context():
            tok = _token(_seed_user(mobile="0599123463", funded=50.0))
            pkg_id = int(_seed_package()["id"])
        resp = client.post("/api/v1/store/purchase",
                           headers=_bearer(tok),
                           json={"package_id": pkg_id})
        assert resp.status_code in (200, 201), resp.get_data(as_text=True)
        with app.app_context():
            # A successful purchase emits BOTH card_issued (marketplace service)
            # and store.purchase (API route) — the report surfaces both.
            issued = _audit("card_issued")
            sale = _audit("store.purchase")
        assert len(issued) == 1
        assert len(sale) == 1
        dec = {r["action"]: r for r in _decorate(app, issued + sale)}
        assert dec["card_issued"]["result_label"] == "إصدار بطاقة"
        assert dec["store.purchase"]["result_label"] == "شراء"
        # No credentials leak into the sale audit payload.
        assert "password" not in (sale[0]["payload_json"] or "").lower()


# ── failed variants decorate as red badges with an Arabic reason ─────────────

class TestDecorateFailedVariants:
    def _row(self, action, *, error_message="", payload=None):
        return {"id": 1, "action": action, "actor": "0599000001",
                "target_id": "1", "target_type": "card_user",
                "payload_json": json.dumps(payload or {}),
                "error_message": error_message,
                "created_at": "2026-07-09 10:00:00",
                "ip_address": "10.0.0.1"}

    def test_login_failed(self, app):
        rows = _decorate(app, [self._row("auth_login_failed",
                                         error_message="bad_password")])
        assert rows[0]["login_ok"] is False
        assert rows[0]["result_label"] == "دخول فاشل"

    def test_deposit_failed(self, app):
        rows = _decorate(app, [self._row("store.deposit_failed",
                                         error_message="أدخل مبلغًا صحيحًا")])
        assert rows[0]["login_ok"] is False
        assert rows[0]["action_label"] == "إيداع فاشل"

    def test_withdrawal_failed(self, app):
        rows = _decorate(app, [self._row("store.withdrawal_failed",
                                         error_message="المبلغ أكبر من رصيدك")])
        assert rows[0]["login_ok"] is False
        assert rows[0]["action_label"] == "سحب فاشل"

    def test_purchase_failed(self, app):
        rows = _decorate(app, [self._row("store.purchase_failed",
                                         error_message="رصيد غير كافٍ",
                                         payload={"package_name": "باقة شهرية"})])
        assert rows[0]["login_ok"] is False
        assert rows[0]["action_label"] == "شراء فاشل"

    def test_card_redeem_failed(self, app):
        rows = _decorate(app, [self._row("store.card_redeem_failed",
                                         error_message="الرقم السري غير صحيح")])
        assert rows[0]["login_ok"] is False
        assert rows[0]["action_label"] == "شحن بطاقة فاشل"


# ── the report query set covers every customer action type ───────────────────

class TestReportActionCoverage:
    def test_store_actions_include_all_types(self):
        from app.radius.routes.reports import _STORE_ACTIONS
        for a in ("auth_login", "auth_login_failed", "card_issued",
                  "store.register", "store.register_failed",
                  "store.logout",
                  "store.purchase", "store.purchase_failed",
                  "store.card_redeem", "store.card_redeem_failed",
                  "store.deposit", "store.deposit_failed",
                  "store.withdrawal", "store.withdrawal_failed"):
            assert a in _STORE_ACTIONS, f"{a} missing from report action set"

    def test_type_filter_groups(self):
        from app.radius.routes.reports import _STORE_ACTION_GROUPS
        assert set(_STORE_ACTION_GROUPS) == {
            "login", "register", "logout", "purchase",
            "redeem", "deposit", "withdrawal",
        }
        # purchase filter surfaces both the sale and the card-issue rows.
        assert "card_issued" in _STORE_ACTION_GROUPS["purchase"]
        assert "store.purchase" in _STORE_ACTION_GROUPS["purchase"]


# ── identity resolution: numeric actor/target → real name + mobile ───────────

class TestStoreIdentity:
    def test_identity_resolved_by_id(self, app):
        with app.app_context():
            _seed_user(name="أحمد", mobile="0599000009")
        row = {"id": 1, "action": "store.logout", "actor": "0599000009",
               "target_id": "1", "target_type": "card_user",
               "payload_json": "{}", "error_message": "",
               "created_at": "2026-07-09 10:00:00", "ip_address": "10.0.0.1"}
        rows = _decorate(app, [row])
        assert "أحمد" in rows[0]["store_identity"]
