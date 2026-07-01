"""A2 — daily/monthly spend cap + per-action daily rate limits.

Server-enforced via a per-manager daily counter table (manager_activity_counters):
  • rate_daily[action] → the Nth+1 attempt of that action in a day returns 429.
  • spend_cap_daily / spend_cap_monthly → money ops that would exceed are blocked.
Counters are per-day (YYYY-MM-DD) so they reset as the date rolls over. 0 =
unlimited. Owner/super bypass.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_rate.db")
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


def _policy(mgr, *, limits=None, permissions=None):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr, limits=limits or {},
        permissions=permissions or {})


def _login(client, *, admin_id, is_super, perms=("users.view", "users.quota")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


# ═══ migration + unit ═══════════════════════════════════════════════════════
def test_migration_counters_table(app):
    with app.app_context():
        cols = [r[1] for r in db().execute(
            "PRAGMA table_info(manager_activity_counters)").fetchall()]
    for c in ("admin_id", "day", "action_key", "count", "amount_minor"):
        assert c in cols


def test_rate_gate_counts_and_blocks(app):
    from app.radius.services import manager_activity as act
    with app.app_context():
        m = _mgr("m_rate")
        _policy(m, limits={"rate_daily": {"subscriber.quota": 2}})
        # 2 allowed (records), 3rd blocked
        assert act.gate_and_record(m, "subscriber.quota", tenant_id=1) is False
        assert act.gate_and_record(m, "subscriber.quota", tenant_id=1) is False
        assert act.gate_and_record(m, "subscriber.quota", tenant_id=1) is True
        assert act.action_count_today(m, "subscriber.quota", tenant_id=1) == 2


def test_no_rate_limit_never_blocks_or_records(app):
    from app.radius.services import manager_activity as act
    with app.app_context():
        m = _mgr("m_norate")   # no rate_daily configured
        assert act.gate_and_record(m, "subscriber.quota", tenant_id=1) is False
        assert act.action_count_today(m, "subscriber.quota", tenant_id=1) == 0  # not recorded


def test_spend_cap_blocks(app):
    from app.radius.services import manager_activity as act
    with app.app_context():
        m = _mgr("m_spend")
        _policy(m, limits={"spend_cap_daily": "1.00"})   # 100 minor
        act.record(m, "cards.purchase", amount_minor=100, tenant_id=1)
        assert act.spend_today(m, tenant_id=1) == 100
        assert act.spend_block_reason(m, 1, tenant_id=1) is not None      # 100+1 > 100
    with app.app_context():
        _policy(m, limits={"spend_cap_daily": "0"})       # unlimited
    with app.app_context():
        assert act.spend_block_reason(m, 999999, tenant_id=1) is None


def test_counters_are_per_day_reset(app):
    from app.radius.services import manager_activity as act
    with app.app_context():
        m = _mgr("m_reset")
        _policy(m, limits={"rate_daily": {"subscriber.quota": 2}})
        # a counter for a PAST day must NOT count toward today
        db().execute(
            "INSERT INTO manager_activity_counters(tenant_id,admin_id,day,action_key,count,amount_minor)"
            " VALUES(1,?,?,?,?,0)", (m, "2000-01-01", "subscriber.quota", 5))
        assert act.action_count_today(m, "subscriber.quota", tenant_id=1) == 0
        assert act.gate_and_record(m, "subscriber.quota", tenant_id=1) is False  # fresh day


# ═══ route enforcement (rate) ═══════════════════════════════════════════════
def test_route_rate_limit_429(app):
    with app.app_context():
        m = _mgr("m_route")
        _policy(m, limits={"rate_daily": {"subscriber.quota": 2}})
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r1 = c.post("/admin/radius/users/x/quota/topup", data={"_csrf_token": "off-csrf", "mb": "10"})
        r2 = c.post("/admin/radius/users/x/quota/topup", data={"_csrf_token": "off-csrf", "mb": "10"})
        r3 = c.post("/admin/radius/users/x/quota/topup", data={"_csrf_token": "off-csrf", "mb": "10"})
    assert r1.status_code != 429 and r2.status_code != 429
    assert r3.status_code == 429      # daily rate exceeded


def test_super_not_rate_limited(app):
    with app.app_context():
        _policy(1, limits={"rate_daily": {"subscriber.quota": 1}})
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        for _ in range(3):
            r = c.post("/admin/radius/users/x/quota/topup", data={"_csrf_token": "off-csrf", "mb": "10"})
            assert r.status_code != 429


# ═══ config persists ════════════════════════════════════════════════════════
def test_policy_persists_caps_and_rates(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True, perms=("admins.policy",))
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf", "spend_cap_daily": "50",
                         "spend_cap_monthly": "500", "rate_subscriber.loan": "3"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services.manager_grants import _grants_row
        lims = _grants_row(m, 1).get("limits") or {}
        assert str(lims.get("spend_cap_daily")) in ("50", "50.0", "50.00")
        assert int((lims.get("rate_daily") or {}).get("subscriber.loan")) == 3
