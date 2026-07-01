"""Stage A — owner-set numeric caps, server-enforced by live count.

  • max_subscribers  → blocked at subscriber create when the manager's count
                       reaches the cap.
  • max_cards_total  → blocked at offer-use when generating would exceed the
    / max_cards_daily   total / today's cards.

0 = unlimited (non-regressive; the owner opts into a limit). Owner/super never
capped. Each proven: over-cap → blocked; under-cap / unlimited → allowed.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mg_caps.db")
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
                                 full_name="Owner", is_super_admin=True)  # min-id owner
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _mgr(username="m1") -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name="M", is_super_admin=False)
    return int(adm.id)


def _policy(mgr, *, permissions=None, limits=None):
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=mgr,
        permissions=permissions or {}, limits=limits or {})


def _seed_subs(mgr, n):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    for i in range(n):
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username=f"s_{mgr}_{i}", password="p1234567",
            status="enabled", manager_id=mgr))


_plan_seq = [0]


def _plan():
    _plan_seq[0] += 1
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, created_at) VALUES(1,?,datetime('now'))",
        (f"P{_plan_seq[0]}",))
    return int(cur.lastrowid)


def _seed_batch(mgr, count, *, today=True):
    when = "datetime('now')" if today else "'2000-01-01T00:00:00Z'"
    db().execute(
        f"""INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, manager_id, created_at)
            VALUES(1, ?, ?, ?, ?, {when})""",
        (f"b{mgr}_{count}", _plan(), count, mgr))


def _login(client, *, admin_id, is_super, perms=("users.view", "users.create")):
    with client.session_transaction() as s:
        s["admin_id"] = admin_id
        s["admin_user"] = f"a{admin_id}"; s["admin_name"] = "A"
        s["is_super_admin"] = is_super; s["tenant_id"] = 1
        s["_csrf_token"] = "off-csrf"; s["permissions"] = list(perms)


def _sub_count(mgr):
    return db().execute(
        "SELECT COUNT(*) AS n FROM subscribers WHERE manager_id=? AND deleted_at IS NULL",
        (mgr,)).fetchone()["n"]


# ═══ unit: helpers ══════════════════════════════════════════════════════════
def test_default_limits_unlimited(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_def")
        assert mg.limit_value(m, "max_subscribers", tenant_id=1) == 0
        assert mg.subscriber_cap_blocked(m, tenant_id=1) is False


def test_subscriber_cap_blocked_logic(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_sub")
        _seed_subs(m, 2)
        _policy(m, limits={"max_subscribers": 2})
        assert mg.manager_subscriber_count(m, tenant_id=1) == 2
        assert mg.subscriber_cap_blocked(m, tenant_id=1) is True
        _policy(m, limits={"max_subscribers": 5})
    with app.app_context():
        assert mg.subscriber_cap_blocked(m, tenant_id=1) is False


def test_card_cap_reason_logic(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_card")
        _seed_batch(m, 8)                       # 8 cards total (today)
        _policy(m, limits={"max_cards_total": 10, "max_cards_daily": 10})
        assert mg.manager_card_count(m, tenant_id=1) == 8
        assert mg.card_cap_block_reason(m, 5, tenant_id=1) is not None   # 8+5 > 10
        assert mg.card_cap_block_reason(m, 2, tenant_id=1) is None       # 8+2 = 10 ok


def test_card_daily_cap_independent_of_total(app):
    from app.radius.services import manager_grants as mg
    with app.app_context():
        m = _mgr("m_card2")
        _seed_batch(m, 5, today=False)          # old, not counted for daily
        _seed_batch(m, 3, today=True)           # 3 today
        _policy(m, limits={"max_cards_total": 0, "max_cards_daily": 4})
        assert mg.manager_card_count(m, tenant_id=1, today_only=True) == 3
        assert mg.card_cap_block_reason(m, 2, tenant_id=1) is not None   # 3+2 > 4 daily
        assert mg.card_cap_block_reason(m, 1, tenant_id=1) is None       # 3+1 = 4 ok


# ═══ route: subscriber create cap ═══════════════════════════════════════════
def test_create_blocked_at_cap(app):
    with app.app_context():
        m = _mgr("m_create")
        _seed_subs(m, 1)
        _policy(m, permissions={"can_create_subscriber": True},
                limits={"max_subscribers": 1})
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "over_cap",
                         "password": "p1234567", "manager_id": str(m)})
    assert r.status_code == 400
    with app.app_context():
        assert _sub_count(m) == 1               # no new subscriber created


def test_create_allowed_under_cap(app):
    with app.app_context():
        m = _mgr("m_create2")
        _seed_subs(m, 1)
        _policy(m, permissions={"can_create_subscriber": True},
                limits={"max_subscribers": 5})
    with app.test_client() as c:
        _login(c, admin_id=m, is_super=False)
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "under_cap",
                         "password": "p1234567", "manager_id": str(m)})
    # not blocked by the cap (create proceeds → redirect)
    assert r.status_code in (302, 303)
    with app.app_context():
        assert _sub_count(m) == 2


def test_super_not_capped(app):
    with app.app_context():
        # cap the owner id too — super must still bypass
        _policy(1, permissions={"can_create_subscriber": True},
                limits={"max_subscribers": 1})
        _seed_subs(1, 1)
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        r = c.post("/admin/radius/users",
                   data={"_csrf_token": "off-csrf", "username": "super_new",
                         "password": "p1234567", "manager_id": "1"})
    assert r.status_code in (302, 303)


# ═══ config route persists the caps ═════════════════════════════════════════
def test_policy_route_persists_caps(app):
    with app.app_context():
        m = _mgr("m_cfg")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        r = c.post(f"/admin/radius/business-operators/manager/{m}/policy",
                   data={"_csrf_token": "off-csrf",
                         "max_subscribers": "7", "max_cards_total": "100",
                         "max_cards_daily": "20"})
        assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg
        assert mg.limit_value(m, "max_subscribers", tenant_id=1) == 7
        assert mg.limit_value(m, "max_cards_total", tenant_id=1) == 100
        assert mg.limit_value(m, "max_cards_daily", tenant_id=1) == 20
