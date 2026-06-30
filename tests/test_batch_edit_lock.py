"""Editing an existing card-batch: OWNER-ONLY + structural fields locked.

  * a sub-manager cannot edit a batch at all (403 on GET and POST);
  * the owner may change commercial/assignment fields (linked plan/offer,
    duration, price, accounting method, assigned manager);
  * structural / card-build fields (count, code length, code pattern, prefixes)
    are LOCKED even for the owner — a changed structural value is rejected
    server-side and never persisted; existing generated cards are untouched.

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
    db_file = os.path.join(tmp_path, "batch_edit_lock.db")
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
        admins_repo.create_admin(
            username="owner_root", password="x12345678", full_name="Owner",
            is_super_admin=True,
        )
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


# ── helpers ────────────────────────────────────────────────────────────────
def _plan_id(name="باقة") -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"Mgr {username}",
        is_super_admin=False,
    )
    return int(adm.id)


def _make_batch(plan_id: int, *, count: int = 3):
    from app.radius.services.cards import get_cards_service

    batch, cards = get_cards_service().generate_batch(
        actor="test", plan_id=plan_id, count=count,
        username_length=8, password_length=6, price_per_card=2.0,
        time_value=1, time_unit="days",
    )
    return batch, cards


def _batch_row(batch_id: int) -> dict:
    row = db().execute(
        "SELECT count, username_length, password_length, plan_id, price_per_card, "
        "time_value, manager_id, count_by_seconds, generated FROM card_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    return dict(row) if row else {}


def _card_usernames(batch_id: int) -> list:
    return [r["username"] for r in db().execute(
        "SELECT username FROM cards WHERE batch_id=? ORDER BY id", (batch_id,)
    ).fetchall()]


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = ["cards.view", "cards.generate", "cards.edit_batch"]


def _edit(client, batch_id, **fields):
    data = {"_csrf_token": "off-csrf"}
    data.update({k: str(v) for k, v in fields.items()})
    return client.post(f"/admin/radius/cards/batches/{batch_id}/edit",
                       data=data, follow_redirects=False)


# ═══ 1. owner-only gate ═════════════════════════════════════════════════════
def test_sub_manager_cannot_get_edit(app):
    with app.app_context():
        plan = _plan_id()
        batch, _ = _make_batch(plan)
        bid = batch.id
        mgr = _sub_admin("mgr_edit")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.get(f"/admin/radius/cards/batches/{bid}/edit")
    assert res.status_code == 403


def test_sub_manager_cannot_post_edit(app):
    with app.app_context():
        plan = _plan_id()
        batch, _ = _make_batch(plan)
        bid, cnt = batch.id, batch.count
        mgr = _sub_admin("mgr_edit2")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = _edit(client, bid, plan_id=plan, count=cnt, price_per_card="9.99")
    assert res.status_code == 403
    with app.app_context():
        assert float(_batch_row(bid)["price_per_card"]) == 2.0  # untouched


def test_owner_can_get_edit(app):
    with app.app_context():
        plan = _plan_id()
        batch, _ = _make_batch(plan)
        bid = batch.id
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.get(f"/admin/radius/cards/batches/{bid}/edit")
    assert res.status_code == 200


# ═══ 2. owner may change commercial / assignment fields ═════════════════════
def test_owner_edits_commercial_fields_succeed(app):
    with app.app_context():
        plan = _plan_id()
        plan2 = _plan_id("باقة بديلة")
        batch, _ = _make_batch(plan)
        bid, cnt = batch.id, batch.count
        mgr = _sub_admin("assigned_mgr")
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _edit(
            client, bid,
            plan_id=plan2,            # العرض / الباقة المرتبطة
            count=cnt,                # structural, unchanged → allowed
            price_per_card="7.50",    # السعر
            time_value=5, time_unit="days",  # المدة
            count_by_seconds="1", validity_after_first_login_days=1,  # طريقة المحاسبة
            manager_id=mgr,           # المدير
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        row = _batch_row(bid)
        assert int(row["plan_id"]) == plan2
        assert float(row["price_per_card"]) == 7.50
        assert int(row["time_value"]) == 5
        assert int(row["count_by_seconds"]) == 1
        assert int(row["manager_id"]) == mgr


# ═══ 3. structural fields locked even for the owner ═════════════════════════
def test_owner_change_count_rejected_cards_untouched(app):
    with app.app_context():
        plan = _plan_id()
        batch, _ = _make_batch(plan, count=3)
        bid, cnt = batch.id, batch.count
        before_cards = _card_usernames(bid)
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _edit(client, bid, plan_id=plan, count=cnt + 10, price_per_card="3.00")
    # changed structural value → rejected (form re-rendered, no redirect).
    assert res.status_code == 200
    with app.app_context():
        row = _batch_row(bid)
        assert int(row["count"]) == cnt              # count NOT changed
        assert float(row["price_per_card"]) == 2.0   # whole edit rejected
        assert _card_usernames(bid) == before_cards  # cards untouched


def test_owner_change_code_length_rejected(app):
    with app.app_context():
        plan = _plan_id()
        batch, _ = _make_batch(plan)
        bid, cnt = batch.id, batch.count
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _edit(client, bid, plan_id=plan, count=cnt,
                    username_length=16, password_length=12)
    assert res.status_code == 200
    with app.app_context():
        row = _batch_row(bid)
        assert int(row["username_length"]) == 8   # unchanged
        assert int(row["password_length"]) == 6   # unchanged


def test_owner_edit_with_unchanged_structural_succeeds(app):
    # submitting the (read-only) structural fields at their stored values must
    # NOT trip the lock — only a CHANGED structural value is rejected.
    with app.app_context():
        plan = _plan_id()
        batch, _ = _make_batch(plan)
        bid, cnt = batch.id, batch.count
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _edit(client, bid, plan_id=plan, count=cnt,
                    username_length=8, password_length=6, price_per_card="4.25")
    assert res.status_code in (302, 303)
    with app.app_context():
        assert float(_batch_row(bid)["price_per_card"]) == 4.25


# ═══ 4. structural lock is centralised in the service (any caller) ══════════
def test_service_update_batch_drops_structural_fields(app):
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id()
        batch, _ = _make_batch(plan, count=3)
        svc = get_cards_service()
        # even a direct service call cannot grow the count or change code length.
        svc.update_batch(actor="test", batch_id=batch.id, data={
            "count": 999, "username_length": 20, "price_per_card": 6.0,
        })
        row = _batch_row(batch.id)
        assert int(row["count"]) == 3            # structural dropped
        assert int(row["username_length"]) == 8  # structural dropped
        assert float(row["price_per_card"]) == 6.0  # commercial applied
