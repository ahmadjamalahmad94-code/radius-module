"""Card OFFERS — scope-corrected model:

  * the PLAN (الباقة) is REQUIRED on an offer (the offer inherits the plan's
    speed/quota/duration; there is NO per-offer speed field),
  * offer create/edit is OWNER-only (is_super_admin = owner principal),
  * managers may only CHOOSE a ready-made offer as a locked bundle (they cannot
    create offers or change price/plan/terms).

Mirrors the fixture/auth pattern of test_card_offers.py.
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


def _offers_service():
    from app.radius.services.card_offers import CardOffersService

    return CardOffersService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_offers_plan_owner.db")
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
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _plan_id(*, speed_down_kbps=4096, speed_up_kbps=2048, quota_total_mb=1024) -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "باقة منزلية", 8 * 60, 1, 5.0, "JOD", speed_down_kbps, speed_up_kbps, quota_total_mb),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"Manager {username}",
        is_super_admin=False,
    )
    return int(adm.id)


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"


# ── 1. Plan is REQUIRED ────────────────────────────────────────────────────
def test_create_without_plan_rejected(app):
    from app.radius.services.card_offers import CardOfferError

    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        with pytest.raises(CardOfferError):
            svc.create_offer(name="بلا باقة", duration_minutes=60,
                             wholesale="1.00", selling="2.00", plan_id=None)
        with pytest.raises(CardOfferError):
            svc.create_offer(name="باقة وهمية", duration_minutes=60,
                             wholesale="1.00", selling="2.00", plan_id=99999)


def test_create_with_plan_persists_plan(app):
    with app.app_context():
        plan = _plan_id()
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(name="عرض بباقة", duration_minutes=8 * 60,
                                 wholesale="2.00", selling="5.00", plan_id=plan)
        assert int(offer["plan_id"]) == plan


def test_update_to_missing_plan_rejected_but_keep_works(app):
    from app.radius.services.card_offers import CardOfferError

    with app.app_context():
        plan = _plan_id()
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(name="ع", duration_minutes=60, wholesale="1.00",
                                 selling="2.00", plan_id=plan)
        # changing to a non-existent plan is rejected
        with pytest.raises(CardOfferError):
            svc.update_offer(offer["id"], plan_id=99999)
        # omitting plan keeps the existing one (sentinel default)
        kept = svc.update_offer(offer["id"], selling="9.00")
        assert int(kept["plan_id"]) == plan


def test_route_create_without_plan_flashes_no_offer(app):
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            "/admin/radius/cards/offers",
            data={"_csrf_token": "off-csrf", "name": "بلا باقة",
                  "duration_minutes": "60", "wholesale": "1.00", "selling": "2.00"},
            follow_redirects=False,
        )
    # redirect back with a flashed error; no offer persisted
    assert res.status_code in (302, 303)
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        assert svc.list_offers(admin_id=None, is_super=True) == []


# ── 2. Owner-only create/edit; managers locked out ─────────────────────────
def test_manager_cannot_create_offer_403(app):
    with app.app_context():
        mgr = _sub_admin("mgr")
        plan = _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(
            "/admin/radius/cards/offers",
            data={"_csrf_token": "off-csrf", "name": "محاولة", "duration_minutes": "60",
                  "wholesale": "1.00", "selling": "2.00", "plan_id": str(plan)},
        )
    assert res.status_code == 403
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        assert svc.list_offers(admin_id=None, is_super=True) == []


def test_manager_cannot_edit_price_403(app):
    with app.app_context():
        mgr = _sub_admin("mgr")
        plan = _plan_id()
        svc = _offers_service()(tenant_id=1)
        offer = svc.create_offer(name="عرض", duration_minutes=60, wholesale="2.00",
                                 selling="5.00", plan_id=plan, visible_admin_ids=[mgr])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post(
            f"/admin/radius/cards/offers/{offer['id']}/edit",
            data={"_csrf_token": "off-csrf", "name": "عرض", "duration_minutes": "60",
                  "wholesale": "0.01", "selling": "0.02", "plan_id": str(plan)},
        )
    assert res.status_code == 403
    with app.app_context():
        svc = _offers_service()(tenant_id=1)
        again = svc.get_offer(offer["id"])
        # price untouched by the manager's tampering attempt
        assert int(again["wholesale_minor"]) == 200
        assert int(again["selling_minor"]) == 500


def test_owner_create_succeeds(app):
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        with app.app_context():
            plan = _plan_id()
        res = client.post(
            "/admin/radius/cards/offers",
            data={"_csrf_token": "off-csrf", "name": "عرض المالك", "duration_minutes": "480",
                  "wholesale": "2.00", "selling": "5.00", "plan_id": str(plan)},
            follow_redirects=False,
        )
        assert res.status_code in (302, 303)
        with app.app_context():
            svc = _offers_service()(tenant_id=1)
            offers = svc.list_offers(admin_id=None, is_super=True)
            assert len(offers) == 1 and int(offers[0]["plan_id"]) == plan


# ── 3. Manager page shows offers as locked (no create form) ────────────────
def test_manager_page_has_no_create_form(app):
    with app.app_context():
        mgr = _sub_admin("mgr")
        plan = _plan_id()
        svc = _offers_service()(tenant_id=1)
        svc.create_offer(name="عرض مشارَك", duration_minutes=60, wholesale="2.00",
                         selling="5.00", plan_id=plan, visible_admin_ids=[mgr])
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        page = client.get("/admin/radius/cards/offers")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    # The owner-only creation form action must NOT be present for a manager.
    assert "data-offer-create" not in html
    assert "عرض مشارَك" in html  # but the shared offer IS listed
    # Locked-selection banner for managers is shown.
    assert "عروض جاهزة من الإدارة" in html


# ── 4. Plan summary is available to the page (read-only delivery line) ──────
def test_plan_summary_rendered_on_owner_page(app):
    with app.app_context():
        plan = _plan_id(speed_down_kbps=4096, speed_up_kbps=2048, quota_total_mb=1024)
        svc = _offers_service()(tenant_id=1)
        svc.create_offer(name="عرض", duration_minutes=60, wholesale="2.00",
                         selling="5.00", plan_id=plan)
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        page = client.get("/admin/radius/cards/offers")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    # 4096 kbps = 4 ميجا/ث, 2048 = 2 ميجا/ث, quota 1024 MB = 1 جيجا — shown read-only.
    assert "ما يقدّمه" in html
    assert "باقة منزلية" in html
