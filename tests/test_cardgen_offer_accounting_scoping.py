"""Card-batch generate role-split + offer-driven charged generation + list
scoping + recharge minimal projection.

Covers the owner's reports:
  * a sub-manager could generate a free batch via the full form (no charge) —
    now the full form is OWNER-ONLY (403) and managers generate from offers
    which DEBIT their wallet within the bounded-debt cap;
  * managers must only see/scope their own subscribers & card-batches unless
    granted can_view_all_*; the recharge/activation lookup reaches everyone but
    returns only a minimal projection for out-of-scope subscribers.

Auth/fixture pattern mirrors test_card_offers_plan_owner.py.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "cardgen_offer_accounting.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        # Root owner (smallest admin id) — so sub-managers created later are NOT
        # the primary-owner-by-min-id and therefore get ZERO credit trust
        # (capped) rather than uncapped owner treatment.
        admins_repo.create_admin(
            username="owner_root", password="x12345678", full_name="Owner",
            is_super_admin=True,
        )
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


# ── helpers ────────────────────────────────────────────────────────────────
def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "باقة", 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"Mgr {username}",
        is_super_admin=False,
    )
    return int(adm.id)


def _distributor(name: str, admin_id) -> int:
    from app.radius.db.repos import operations_repo

    d = operations_repo.create_distributor(
        1, {"name": name, "admin_id": admin_id, "status": "active"}, actor="test",
    )
    return int(d["id"])


def _batch(code: str, *, plan_id: int, manager_id=0, distributor_id=None) -> int:
    cur = db().execute(
        """
        INSERT INTO card_batches(tenant_id, batch_code, plan_id, manager_id,
                                 distributor_id, created_at)
        VALUES(1, ?, ?, ?, ?, datetime('now'))
        """,
        (code, plan_id, manager_id or 0, distributor_id),
    )
    return int(cur.lastrowid)


def _subscriber(username: str, *, manager_id=None, card_batch_id=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="pw12345678",
        status="enabled", manager_id=manager_id, card_batch_id=card_batch_id,
    ))


def _offer(plan_id: int, *, visible=(), wholesale="2.00", selling="5.00"):
    from app.radius.services.card_offers import CardOffersService

    return CardOffersService(tenant_id=1).create_offer(
        name="عرض اختبار", duration_minutes=8 * 60, wholesale=wholesale,
        selling=selling, plan_id=plan_id, visible_admin_ids=list(visible),
    )


def _grant(manager_id: int, **perms) -> None:
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService

    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=manager_id, permissions=perms,
    )


def _fund_wallet(manager_id: int, amount_minor: int) -> None:
    from app.radius.services.business_os_finance import WalletService, minor_to_money
    from app.radius.services.manager_credit import ManagerCreditService

    w = ManagerCreditService(tenant_id=1).wallet(manager_id)
    WalletService().credit(
        tenant_id=1, wallet_id=int(w["id"]), amount=minor_to_money(amount_minor),
        actor_type="admin", reference_type="test_fund",
    )


def _wallet_balance_minor(manager_id: int) -> int:
    from app.radius.services.manager_credit import ManagerCreditService

    return int(ManagerCreditService(tenant_id=1).wallet(manager_id).get("balance_minor") or 0)


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        # broad nav perms so the PRE-EXISTING RBAC guard passes and our finer
        # role/scope gates are what the assertions actually exercise.
        sess["permissions"] = [
            "cards.view", "cards.generate", "users.view", "users.payments",
            "reports.finance",
        ]


def _latest_batch():
    row = db().execute(
        "SELECT manager_id, distributor_id, total_price FROM card_batches ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ═══ 1. new permissions present + labelled ══════════════════════════════════
def test_new_view_all_permissions_default_off(app):
    from app.radius.services.manager_distributor_ops import DEFAULT_PERMISSIONS

    assert DEFAULT_PERMISSIONS.get("can_view_all_subscribers") is False
    assert DEFAULT_PERMISSIONS.get("can_view_all_card_batches") is False


def test_view_all_permission_labels_arabic(app):
    from app.radius.services.permission_labels import permission_label

    assert permission_label("can_view_all_subscribers") == "عرض كل المشتركين"
    assert permission_label("can_view_all_card_batches") == "عرض كل حزم البطاقات"


# ═══ 2. role gate on /cards/generate ════════════════════════════════════════
def test_manager_cannot_post_full_generate(app):
    with app.app_context():
        plan = _plan_id()
        mgr = _sub_admin("mgr_gate")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/cards/generate", data={
            "_csrf_token": "off-csrf", "plan_id": str(plan), "count": "5",
            "price_per_card": "2", "batch_type": "printed",
        })
    assert res.status_code == 403
    with app.app_context():
        assert _latest_batch() is None


def test_manager_progress_start_forbidden(app):
    with app.app_context():
        plan = _plan_id()
        mgr = _sub_admin("mgr_prog")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/cards/generate/progress", data={
            "_csrf_token": "off-csrf", "plan_id": str(plan), "count": "5",
        })
    assert res.status_code == 403


def test_manager_generate_get_shows_offer_picker_not_full_form(app):
    with app.app_context():
        plan = _plan_id()
        mgr = _sub_admin("mgr_pick")
        _offer(plan, visible=[mgr])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        page = client.get("/admin/radius/cards/generate")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "توليد من عرض" in html          # offer-picker present
    assert "نوع الحزمة" not in html         # owner-only full-form field absent


def test_super_generate_get_shows_full_form(app):
    with app.app_context():
        _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        page = client.get("/admin/radius/cards/generate")
    html = page.get_data(as_text=True)
    assert "نوع الحزمة" in html


# ═══ 3. owner pricing auto-compute (total = count × sell, server-side) ═══════
def test_owner_generate_computes_total_price_serverside(app):
    with app.app_context():
        plan = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post("/admin/radius/cards/generate", data={
            "_csrf_token": "off-csrf", "plan_id": str(plan), "count": "5",
            "price_per_card": "2.00", "price_bulk": "1.00", "batch_type": "printed",
        }, follow_redirects=False)
    assert res.status_code in (302, 303)
    with app.app_context():
        batch = _latest_batch()
        assert batch is not None
        assert abs(float(batch["total_price"]) - 10.0) < 0.001  # 5 × 2.00


# ═══ 4. offer-driven generation charges the manager (bounded debt) ══════════
def test_zero_balance_manager_without_debt_blocked(app):
    with app.app_context():
        plan = _plan_id()
        mgr = _sub_admin("mgr_zero")
        offer = _offer(plan, visible=[mgr], wholesale="2.00")
        oid = int(offer["id"])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(f"/admin/radius/cards/offers/{oid}/use", data={
            "_csrf_token": "off-csrf", "count": "3",
        })
    # blocked → form re-rendered (200), NO batch created.
    assert res.status_code == 200
    with app.app_context():
        assert _latest_batch() is None


def test_sufficient_balance_manager_is_debited(app):
    with app.app_context():
        plan = _plan_id()
        mgr = _sub_admin("mgr_pay")
        offer = _offer(plan, visible=[mgr], wholesale="2.00")
        oid = int(offer["id"])
        _fund_wallet(mgr, 1000)            # 10.00 JOD
        before = _wallet_balance_minor(mgr)
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(f"/admin/radius/cards/offers/{oid}/use", data={
            "_csrf_token": "off-csrf", "count": "3",
        }, follow_redirects=False)
    assert res.status_code in (302, 303)
    with app.app_context():
        after = _wallet_balance_minor(mgr)
        assert before - after == 600        # 3 × 2.00 wholesale
        batch = _latest_batch()
        assert batch is not None and int(batch["manager_id"]) == mgr


# ═══ 5. subscriber list scoping ═════════════════════════════════════════════
def test_subscriber_repo_scope_own_and_distributor(app):
    from app.radius.db.repos import subscribers_repo

    with app.app_context():
        plan = _plan_id()
        a = _sub_admin("subs_a")
        b = _sub_admin("subs_b")
        a_dist = _distributor("a_dist", a)
        a_dist_batch = _batch("AB1", plan_id=plan, manager_id=b, distributor_id=a_dist)
        _subscriber("a_own", manager_id=a)
        _subscriber("a_viadist", card_batch_id=a_dist_batch)   # via A's distributor
        _subscriber("b_own", manager_id=b)

        scoped = {s.username for s in subscribers_repo.list_subscribers(1, owner_admin_id=a)}
        assert "a_own" in scoped
        assert "a_viadist" in scoped
        assert "b_own" not in scoped
        # unscoped (super / can_view_all) sees everyone
        all_ = {s.username for s in subscribers_repo.list_subscribers(1)}
        assert {"a_own", "a_viadist", "b_own"} <= all_


def test_subscriber_status_counts_respect_scope(app):
    from app.radius.db.repos import subscribers_repo

    with app.app_context():
        a = _sub_admin("cnt_a")
        b = _sub_admin("cnt_b")
        _subscriber("ca1", manager_id=a)
        _subscriber("cb1", manager_id=b)
        _subscriber("cb2", manager_id=b)
        sc_a = subscribers_repo.subscribers_status_counts(1, owner_admin_id=a)
        sc_all = subscribers_repo.subscribers_status_counts(1)
        assert sc_a["total"] == 1
        assert sc_all["total"] == 3


def test_subscriber_route_scoping_and_view_all(app):
    with app.app_context():
        a = _sub_admin("route_a")
        b = _sub_admin("route_b")
        _subscriber("route_a_sub", manager_id=a)
        _subscriber("route_b_sub", manager_id=b)
    with app.test_client() as client:
        _login(client, admin_id=a, is_super=False)
        scoped = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert "route_a_sub" in scoped
    assert "route_b_sub" not in scoped
    with app.app_context():
        _grant(a, can_view_all_subscribers=True)
    with app.test_client() as client:
        _login(client, admin_id=a, is_super=False)
        full = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert "route_b_sub" in full


# ═══ 6. card-batch list scoping ═════════════════════════════════════════════
def test_batch_repo_scope_own_and_distributor(app):
    from app.radius.db.repos import cards_repo

    with app.app_context():
        plan = _plan_id()
        a = _sub_admin("bat_a")
        b = _sub_admin("bat_b")
        a_dist = _distributor("bat_a_dist", a)
        _batch("OWN-A", plan_id=plan, manager_id=a)
        _batch("DIST-A", plan_id=plan, manager_id=b, distributor_id=a_dist)
        _batch("OWN-B", plan_id=plan, manager_id=b)

        scoped = {r["batch_code"] for r in cards_repo.list_batch_operations(1, owner_admin_id=a, status="all")}
        assert "OWN-A" in scoped and "DIST-A" in scoped
        assert "OWN-B" not in scoped
        assert cards_repo.count_batch_operations(1, owner_admin_id=a, status="all") == 2
        assert cards_repo.count_batch_operations(1, status="all") == 3


# ═══ 7. recharge minimal projection (global reach, scoped fields) ═══════════
def test_recharge_lookup_minimal_for_out_of_scope(app):
    with app.app_context():
        a = _sub_admin("rc_a")
        b = _sub_admin("rc_b")
        _subscriber("rc_other", manager_id=b)   # belongs to B, not A
    with app.test_client() as client:
        _login(client, admin_id=a, is_super=False)
        res = client.get("/admin/radius/recharge/subscriber/rc_other.json")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data.get("scoped") is True
    sub = data["subscriber"]
    # minimal projection: identity + activation fields only.
    assert sub["username"] == "rc_other"
    assert "status" in sub and "plan_name" in sub and "expire_at" in sub
    # full record fields MUST NOT leak for an out-of-scope subscriber.
    assert "full_name" not in sub
    assert "mobile" not in sub
    assert "balance" not in sub
    assert "price" not in sub


def test_recharge_lookup_full_for_super(app):
    with app.app_context():
        b = _sub_admin("rc_b2")
        _subscriber("rc_full", manager_id=b)
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.get("/admin/radius/recharge/subscriber/rc_full.json")
    data = res.get_json()
    sub = data["subscriber"]
    assert "full_name" in sub and "balance" in sub  # full record for super
