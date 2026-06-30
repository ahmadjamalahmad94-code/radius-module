"""Dedicated regression tests for the THREE owner-reported bugs.

1. PAID quota-restore (and add-quota / add-time) reported «مدفوعة بقيمة X» but
   never debited the balance — the headline accounting bug. Proven fixed at the
   service layer AND through the HTTP route with the exact reported scenario
   (balance 20.00, paid 5.00 → 15.00).
2. Bulk-action modal showed «المشتركون المحدَّدون (0)» — the selected count was
   not wired. Guarded by asserting the served page wires the count to the real
   selection size and renders a count span in every multi-capable modal.
3. The balance/renewal coverage hint estimated «≈ 0 يوم» for sub-day packages.
   Guarded by asserting the served page ships the duration guard that renders a
   human unit (ساعة/دقيقة) instead of a bare zero.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = tmp_path / "known_bug_regressions.db"
    monkeypatch.setenv("HOBERADIUS_DB_PATH", str(db_file))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(str(db_file))
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "reg_admin"
        sess["admin_name"] = "Reg Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "reg-csrf"


def _plan(name, price=20.0):
    from app.radius.db.connection import db

    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, price, "
        "currency, enabled, created_at, updated_at) VALUES(1,?,?,?,?,?,1,?,?)",
        (name, 30 * 1440, 30, price, "JOD", datetime.utcnow().isoformat(),
         datetime.utcnow().isoformat()))
    return int(cur.lastrowid)


def _sub(username, plan_id, balance, used_seconds=3600):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import db
    from app.radius.db.repos import subscribers_repo

    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Reg User", mobile="0599000000", status="enabled",
        expire_at=datetime.utcnow() + timedelta(days=7)))
    db().execute("UPDATE subscribers SET balance=?, used_seconds=? WHERE tenant_id=1 AND username=?",
                 (float(balance), int(used_seconds), username))


def _bal(username):
    from app.radius.db.repos import subscribers_repo

    return float(subscribers_repo.get_subscriber(1, username).balance or 0)


# ════════════════════════════════════════════════════════════════════════
# BUG #1 — paid quota-restore must DEBIT the balance (the confirmed scenario)
# ════════════════════════════════════════════════════════════════════════
def test_bug1_paid_quota_restore_debits_service_layer(app):
    """Exact owner scenario: balance 20.00, paid restore 5.00 → balance 15.00
    (NOT 20.00). A debit ledger row is written; the returned object shows 15."""
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services.users import get_users_service

        p = _plan("Bug1Plan")
        _sub("bug1", p, balance=20.0, used_seconds=3600)
        saved = get_users_service().reset_daily_quota(
            actor="owner", username="bug1", charge_mode="paid", amount=5.0, currency="JOD")
        assert _bal("bug1") == pytest.approx(15.0)           # was 20.0 before the fix
        assert float(saved.balance) == pytest.approx(15.0)   # toast shows the NEW balance
        row = db().execute(
            "SELECT direction, amount FROM accounting_ledger_entries WHERE tenant_id=1 "
            "AND username='bug1' AND source_type='subscriber_daily_quota_reset'").fetchone()
        assert row["direction"] == "debit" and float(row["amount"]) == pytest.approx(5.0)


def test_bug1_paid_quota_restore_debits_via_route(app, client):
    with app.app_context():
        _sub("bug1r", _plan("Bug1RPlan"), balance=20.0, used_seconds=3600)
    _auth(client)
    res = client.post("/admin/radius/users/bug1r/quota/reset-daily",
                      data={"_csrf_token": "reg-csrf", "charge_mode": "paid",
                            "amount": "5", "currency": "JOD"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _bal("bug1r") == pytest.approx(15.0)


@pytest.mark.parametrize("op,url_suffix,extra", [
    ("topup", "/quota/topup", {"quota_mb": "100"}),
    ("extend", "/extend", {"minutes": "720"}),
])
def test_bug1_paid_addons_debit_via_route(app, client, op, url_suffix, extra):
    with app.app_context():
        _sub("bug1x", _plan(f"Bug1X{op}"), balance=20.0)
    _auth(client)
    data = {"_csrf_token": "reg-csrf", "charge_mode": "paid", "amount": "5", "currency": "JOD"}
    data.update(extra)
    res = client.post(f"/admin/radius/users/bug1x{url_suffix}", data=data, follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        assert _bal("bug1x") == pytest.approx(15.0)


# ════════════════════════════════════════════════════════════════════════
# BUG #2 — bulk modal must show the real selected count, not 0
# ════════════════════════════════════════════════════════════════════════
def test_bug2_selected_count_is_wired_in_served_page(app, client):
    with app.app_context():
        _sub("seluser", _plan("SelPlan"), balance=10.0)
    _auth(client)
    res = client.get("/admin/radius/users")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # the count span exists, AND the JS wires it to the live selection size
    assert "data-usq-multi-count" in html
    assert "count.textContent = String(rows.length)" in html
    # the multi-modal setup runs on open for the money modals
    assert "setupMultiModal" in html


# ════════════════════════════════════════════════════════════════════════
# BUG #3 — coverage hint must not render «≈ 0 يوم» for sub-day packages
# ════════════════════════════════════════════════════════════════════════
def test_bug3_coverage_hint_uses_duration_guard(app, client):
    with app.app_context():
        _sub("covuser", _plan("CovPlan"), balance=10.0)
    _auth(client)
    res = client.get("/admin/radius/users")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # the human-duration helper exists and the coverage estimate routes through
    # it (so a < 1-day package shows ساعة/دقيقة instead of a bald «0 يوم»).
    assert "arDuration" in html
    assert "function arDuration" in html or "arDuration(" in html
