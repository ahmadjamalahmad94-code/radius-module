"""Card OFFERS — visibility allow-list, server-side locked price/time, wallet
billing, and super-admin override.

Mirrors the fixture/auth pattern of test_card_users_marketplace.py.
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


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


def _repos():
    from app.radius.db.repos import admins_repo, tenants_repo

    return admins_repo, tenants_repo


def _offers_service():
    from app.radius.services.card_offers import CardOffersService

    return CardOffersService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_offers.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        _run_pending_migrations()
        admins_repo, tenants_repo = _repos()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Offers 8h", 8 * 60, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"Manager {username}",
        is_super_admin=False,
    )
    return int(adm.id)


def _make_offer(app, *, share_with=None, wholesale="2.00", selling="5.00", duration_minutes=8 * 60):
    """Create an 8h offer owned by super, optionally shared with given admin ids."""
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(
            name="بطاقة 8 ساعات",
            duration_minutes=duration_minutes,
            wholesale=wholesale,
            selling=selling,
            plan_id=_plan_id(),
            created_by="super",
            visible_admin_ids=list(share_with or []),
        )
        return offer


def _credit_manager(app, admin_id: int, amount: str) -> None:
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        wallet = svc.manager_wallet(admin_id)
        svc.wallets.credit(tenant_id=1, wallet_id=int(wallet["id"]), amount=amount,
                           reference_type="test_topup", actor_type="system")


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"


def _latest_batch():
    row = db().execute(
        "SELECT * FROM card_batches ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ── 1. Service-level visibility enforcement ────────────────────────────────
def test_visibility_allow_list_enforced(app):
    mgr_a = None
    mgr_b = None
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
        mgr_b = _sub_admin("mgr_b")
    offer = _make_offer(app, share_with=[mgr_a])
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        # Shared manager sees it; the other does not; super always does.
        assert svc.is_visible_to(offer["id"], admin_id=mgr_a, is_super=False) is True
        assert svc.is_visible_to(offer["id"], admin_id=mgr_b, is_super=False) is False
        assert svc.is_visible_to(offer["id"], admin_id=None, is_super=True) is True
        assert len(svc.list_offers(admin_id=mgr_a, is_super=False)) == 1
        assert svc.list_offers(admin_id=mgr_b, is_super=False) == []
        assert len(svc.list_offers(admin_id=None, is_super=True)) == 1


def test_default_visibility_is_not_shared(app):
    """SAFE default: an offer with no allow-list is invisible to every sub-admin."""
    offer = _make_offer(app, share_with=[])
    with app.app_context():
        mgr = _sub_admin("lonely_mgr")
        svc = _offers_service()(tenant_id=1)
        assert svc.is_visible_to(offer["id"], admin_id=mgr, is_super=False) is False
        assert svc.list_offers(admin_id=mgr, is_super=False) == []


def test_offers_list_page_renders_for_both_roles(app):
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
        mgr_b = _sub_admin("mgr_b")
    _make_offer(app, share_with=[mgr_a])
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        sup = client.get("/admin/radius/cards/offers")
        _login(client, admin_id=mgr_a, is_super=False)
        shared = client.get("/admin/radius/cards/offers")
        _login(client, admin_id=mgr_b, is_super=False)
        unshared = client.get("/admin/radius/cards/offers")
    assert sup.status_code == 200
    assert shared.status_code == 200 and "بطاقة 8 ساعات" in shared.get_data(as_text=True)
    # The unshared manager's list page renders but shows no offer.
    assert unshared.status_code == 200 and "بطاقة 8 ساعات" not in unshared.get_data(as_text=True)


# ── 2. Route: visibility 403 for a non-shared sub-admin ────────────────────
def test_use_route_403_for_unshared_sub_admin(app):
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
        mgr_b = _sub_admin("mgr_b")
    offer = _make_offer(app, share_with=[mgr_a])
    with app.test_client() as client:
        _login(client, admin_id=mgr_b, is_super=False)
        get_res = client.get(f"/admin/radius/cards/offers/{offer['id']}/use")
        post_res = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={"_csrf_token": "off-csrf", "count": "5", "username_length": "8"},
        )
    assert get_res.status_code == 403
    assert post_res.status_code == 403


# ── 3. Route: sub-admin sees locked price/time; tampered POST is ignored ────
def test_sub_admin_tampered_price_time_ignored(app):
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
    offer = _make_offer(app, share_with=[mgr_a], wholesale="2.00", selling="5.00")
    _credit_manager(app, mgr_a, "100.00")
    with app.test_client() as client:
        _login(client, admin_id=mgr_a, is_super=False)
        # GET shows the form (locked terms visible)
        page = client.get(f"/admin/radius/cards/offers/{offer['id']}/use")
        assert page.status_code == 200
        # POST with TAMPERED price + time — must be discarded server-side.
        res = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={
                "_csrf_token": "off-csrf",
                "count": "10",
                "username_length": "9",
                "password_charset": "mixed",
                "price_per_card": "999.00",   # tampered selling
                "price_bulk": "0.01",          # tampered wholesale
                "time_value": "999",            # tampered time
                "time_unit": "days",
            },
        )
    assert res.status_code in (302, 303)  # redirect to the new batch
    with app.app_context():
        batch = _latest_batch()
    assert batch is not None
    # Authoritative offer values won, not the tampered ones.
    assert abs(float(batch["price_bulk"]) - 2.00) < 1e-6
    assert abs(float(batch["price_per_card"]) - 5.00) < 1e-6
    assert int(batch["time_value"]) == 8 and batch["time_unit"] == "hours"
    # Editable generation params were respected.
    assert int(batch["username_length"]) == 9
    assert int(batch["count"]) == 10


def test_sub_admin_wholesale_charged_to_balance(app):
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
    offer = _make_offer(app, share_with=[mgr_a], wholesale="2.00", selling="5.00")
    _credit_manager(app, mgr_a, "100.00")
    with app.test_client() as client:
        _login(client, admin_id=mgr_a, is_super=False)
        client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={"_csrf_token": "off-csrf", "count": "10", "username_length": "8"},
        )
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        wallet = svc.manager_wallet(mgr_a)
    # 100.00 - (2.00 * 10) = 80.00 → 8000 minor
    assert int(wallet["balance_minor"]) == 8000


# ── 4. Route: no balance blocks generation (fail-closed) ───────────────────
def test_insufficient_balance_blocks_generation(app):
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
    offer = _make_offer(app, share_with=[mgr_a], wholesale="2.00", selling="5.00")
    _credit_manager(app, mgr_a, "5.00")  # only enough for 2 cards
    with app.test_client() as client:
        _login(client, admin_id=mgr_a, is_super=False)
        res = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={"_csrf_token": "off-csrf", "count": "10", "username_length": "8"},
            follow_redirects=False,
        )
    # Rendered the form again (200) with a flashed error — no batch created.
    assert res.status_code == 200
    with app.app_context():
        assert _latest_batch() is None
        svc = _offers_service()(tenant_id=1)
        assert int(svc.manager_wallet(mgr_a)["balance_minor"]) == 500  # untouched


# ── 5. Route: super-admin full control ─────────────────────────────────────
def test_super_admin_create_and_override(app):
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        with app.app_context():
            plan = _plan_id()
        # Super creates an offer with visibility + commercial terms.
        create = client.post(
            "/admin/radius/cards/offers",
            data={
                "_csrf_token": "off-csrf",
                "name": "عرض السوبر",
                "duration_minutes": str(8 * 60),
                "wholesale": "3.00",
                "selling": "7.00",
                "plan_id": str(plan),
                "visible_admin_ids": str(mgr_a),
            },
            follow_redirects=False,
        )
        assert create.status_code in (302, 303)
        with app.app_context():
            svc = _offers_service()(tenant_id=1)
            offer = svc.list_offers(admin_id=None, is_super=True)[0]
            assert mgr_a in offer["visible_admin_ids"]
        # Super edits price/time freely.
        edit = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/edit",
            data={"_csrf_token": "off-csrf", "name": "عرض السوبر",
                  "duration_minutes": str(2 * 60), "wholesale": "1.00", "selling": "9.00"},
            follow_redirects=False,
        )
        assert edit.status_code in (302, 303)
        with app.app_context():
            svc = _offers_service()(tenant_id=1)
            updated = svc.get_offer(offer["id"])
            assert updated["duration_minutes"] == 120
            assert updated["selling_minor"] == 900
        # Super generates with an overridden price — full control, no wallet block.
        use = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/use",
            data={"_csrf_token": "off-csrf", "count": "3", "username_length": "8",
                  "plan_id": str(plan), "price_per_card": "12.50", "price_bulk": "4.00",
                  "time_value": "30", "time_unit": "days"},
            follow_redirects=False,
        )
        assert use.status_code in (302, 303)
        with app.app_context():
            batch = _latest_batch()
        assert abs(float(batch["price_per_card"]) - 12.50) < 1e-6  # override honoured
        assert int(batch["time_value"]) == 30 and batch["time_unit"] == "days"


def test_sub_admin_cannot_create_offer(app):
    """Super-only capability gate is real (not just a hidden template)."""
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
        plan = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=mgr_a, is_super=False)
        res = client.post(
            "/admin/radius/cards/offers",
            data={"_csrf_token": "off-csrf", "name": "محاولة", "duration_minutes": "60",
                  "wholesale": "1.00", "selling": "2.00", "plan_id": str(plan)},
        )
    assert res.status_code == 403
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        assert svc.list_offers(admin_id=None, is_super=True) == []


def test_sub_admin_cannot_edit_visibility(app):
    with app.app_context():
        mgr_a = _sub_admin("mgr_a")
        mgr_b = _sub_admin("mgr_b")
    offer = _make_offer(app, share_with=[mgr_a])
    with app.test_client() as client:
        _login(client, admin_id=mgr_a, is_super=False)
        res = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/visibility",
            data={"_csrf_token": "off-csrf", "visible_admin_ids": str(mgr_b)},
        )
    assert res.status_code == 403
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        # allow-list unchanged — still only mgr_a.
        assert svc.visibility_admin_ids(offer["id"]) == [mgr_a]
