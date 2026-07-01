"""High-value approval queue — manager actions above the owner-set threshold
queue for owner approval instead of executing immediately.

  • below threshold → executes normally (no queue).
  • above threshold → queued (NOT executed); manager sees «بانتظار موافقة المالك».
  • owner approve → executes (the loan is created); reject → discarded.
Owner/super bypass the threshold. The owner queue page is owner-only.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_approvals.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _mgr(username) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", is_super_admin=False)
    return int(adm.id)


def _plan():
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,price,currency,"
        "created_at,updated_at) VALUES(1,'P',43200,30,100.0,'JOD',datetime('now'),datetime('now'))")
    return int(cur.lastrowid)


def _sub(username, mgr):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="p1234567",
        status="enabled", manager_id=mgr, plan_id=_plan(), custom_price=100.0))


def _policy(mgr, *, threshold=None, permissions=None):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr,
        permissions=permissions or {},
        require_approval_above=(threshold if threshold is not None else 0))


def _login(client, *, admin_id, is_super, perms=("users.view", "users.loans")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


def _pending_count():
    return db().execute(
        "SELECT COUNT(*) AS n FROM manager_pending_approvals WHERE status='pending'").fetchone()["n"]


def _loan_count():
    return db().execute("SELECT COUNT(*) AS n FROM loan_entries").fetchone()["n"]


# ═══ migration + unit ═══════════════════════════════════════════════════════
def test_migration_and_threshold(app):
    from app.radius.services import manager_approvals as ap
    with app.app_context():
        cols = [r[1] for r in db().execute(
            "PRAGMA table_info(manager_pending_approvals)").fetchall()]
        assert "status" in cols and "payload_json" in cols
        m = _mgr("m_th"); _policy(m, threshold=50)
        assert ap.needs_approval(m, 10000, tenant_id=1) is True   # 100.00 > 50
        assert ap.needs_approval(m, 3000, tenant_id=1) is False    # 30.00 < 50


def test_enqueue_approve_executes(app):
    from app.radius.services import manager_approvals as ap
    with app.app_context():
        m = _mgr("m_exec"); _sub("ls", m); _policy(m, threshold=50)
        before = _loan_count()
        rec = ap.enqueue(m, "subscriber.loan", amount_minor=10000,
                         payload={"username": "ls", "amount": 100, "days": 1},
                         tenant_id=1)
        assert rec["status"] == "pending"
        out = ap.approve(int(rec["id"]), decided_by=1, tenant_id=1)
        assert out["approval"]["status"] == "approved"
        assert _loan_count() == before + 1        # the loan was executed on approve


def test_reject_discards(app):
    from app.radius.services import manager_approvals as ap
    with app.app_context():
        m = _mgr("m_rej"); _sub("lr", m)
        before = _loan_count()
        rec = ap.enqueue(m, "subscriber.loan", amount_minor=10000,
                         payload={"username": "lr", "amount": 100, "days": 1}, tenant_id=1)
        ap.reject(int(rec["id"]), decided_by=1, tenant_id=1)
        assert ap.get(int(rec["id"]), tenant_id=1)["status"] == "rejected"
        assert _loan_count() == before            # NOT executed


# ═══ route: above-threshold queues (not executed) ═══════════════════════════
def test_above_threshold_queues(app):
    with app.app_context():
        m = _mgr("m_q"); _sub("qs", m)
        _policy(m, threshold=50, permissions={"can_give_loan": True})
        loans_before = _loan_count()
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/users/qs/loans",
                   data={"_csrf_token": "off-csrf", "amount": "100", "days": "1"})
    assert r.status_code in (302, 303)
    with app.app_context():
        assert _pending_count() == 1              # queued
        assert _loan_count() == loans_before      # NOT executed yet


def test_below_threshold_not_queued(app):
    with app.app_context():
        m = _mgr("m_bt"); _sub("bs", m)
        _policy(m, threshold=500, permissions={"can_give_loan": True})
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        c.post("/admin/radius/users/bs/loans",
               data={"_csrf_token": "off-csrf", "amount": "30", "days": "1"})
    with app.app_context():
        assert _pending_count() == 0              # below threshold → not queued


# ═══ route: owner approve/reject ════════════════════════════════════════════
def test_owner_approve_route_executes(app):
    from app.radius.services import manager_approvals as ap
    with app.app_context():
        m = _mgr("m_oa"); _sub("os", m); _policy(m, threshold=50)
        rec = ap.enqueue(m, "subscriber.loan", amount_minor=10000,
                         payload={"username": "os", "amount": 100, "days": 1}, tenant_id=1)
        before = _loan_count()
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/approvals/{rec['id']}/approve", data={"_csrf_token": "off-csrf"})
        assert r.status_code in (302, 303)
    with app.app_context():
        assert ap.get(int(rec["id"]), tenant_id=1)["status"] == "approved"
        assert _loan_count() == before + 1


def test_approvals_page_owner_only(app):
    with app.app_context():
        m = _mgr("m_plain")
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False, perms=("users.view",))
        assert c.get("/admin/radius/approvals").status_code == 403
