"""EXHAUSTIVE coverage — manager locked offer summary + offer-use page wiring.

  * the sub-manager generate view's locked summary exposes ALL offer attributes
    (per-card sell + wholesale, duration, speed, quota, per-card margin, total
    margin, wholesale cost) with the correct server-derived data attributes;
  * the owner still gets the full batch form (unchanged);
  * the redesigned offer-use page renders with EVERY field/name/hidden input
    and the correct form action intact (layout-only redesign).
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
    db_file = os.path.join(tmp_path, "mgr_summary_exhaustive.db")
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
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


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

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name=f"M {username}", is_super_admin=False)
    return int(adm.id)


def _offer(plan_id: int, mgr: int):
    from app.radius.services.card_offers import CardOffersService

    return CardOffersService(tenant_id=1).create_offer(
        name="عرض", duration_minutes=480, wholesale="2.00", selling="5.00",
        plan_id=plan_id, visible_admin_ids=[mgr])


def _login(client, *, admin_id: int, is_super: bool,
           perms=("cards.view", "cards.generate")):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = list(perms)


# ═══ manager generate view — locked summary exposes ALL offer attributes ════
def test_manager_summary_labels_present(app):
    with app.app_context():
        plan = _plan_id(); mgr = _sub_admin("ms1"); _offer(plan, mgr)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
    for label in ["سعر البيع / بطاقة", "سعر الجملة / بطاقة", "الصلاحية / المدّة",
                  "السرعة", "الكوتا", "هامش الربح / بطاقة", "إجمالي هامش الربح",
                  "تكلفة الجملة"]:
        assert label in html, f"missing locked-summary label: {label}"


def test_manager_summary_data_attributes_from_offer(app):
    with app.app_context():
        plan = _plan_id(); mgr = _sub_admin("ms2"); _offer(plan, mgr)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
    # the option carries the offer's real values, server-derived.
    assert 'data-sell="5.00"' in html
    assert 'data-bulk="2.00"' in html
    assert 'data-dur="480"' in html
    # speed + quota come from the offer's plan (4096/2048 kbps, 1024 MB).
    assert "data-speed=" in html and "ميجا/ث" in html
    assert "data-quota=" in html and "جيجا" in html


def test_owner_still_sees_full_batch_form(app):
    with app.app_context():
        _plan_id()
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        html = c.get("/admin/radius/cards/generate").get_data(as_text=True)
    assert "نوع الحزمة" in html            # owner full form (not the offer picker)
    assert "كيف تُولّد البطاقات" not in html


# ═══ offer-use page (redesign) — every field/wiring intact ══════════════════
def _use_html(app, mgr, offer_id):
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        res = c.get(f"/admin/radius/cards/offers/{offer_id}/use")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_offer_use_manager_all_fields_present(app):
    with app.app_context():
        plan = _plan_id(); mgr = _sub_admin("ou1"); off = _offer(plan, mgr)
        oid = int(off["id"])
    html = _use_html(app, mgr, oid)
    # editable fields the manager keeps.
    for name in ['name="count"', 'name="distributor_id"', 'name="package_name"',
                 'name="username_length"', 'name="password_length"',
                 'name="password_charset"', 'name="username_prefix"']:
        assert name in html, f"missing field: {name}"
    # plan locked to the offer (hidden input carries it).
    assert 'name="plan_id"' in html
    assert f'/admin/radius/cards/offers/{oid}/use' in html   # correct form action
    # locked summary values rendered (selling 5.00, wholesale 2.00).
    assert "5.00" in html and "2.00" in html


def test_offer_use_super_has_override_fields(app):
    with app.app_context():
        plan = _plan_id(); mgr = _sub_admin("ou2"); off = _offer(plan, mgr)
        oid = int(off["id"])
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        html = c.get(f"/admin/radius/cards/offers/{oid}/use").get_data(as_text=True)
    # super override fields are wired.
    for name in ['name="price_per_card"', 'name="price_bulk"', 'name="time_value"',
                 'name="time_unit"']:
        assert name in html, f"missing super-override field: {name}"


def test_offer_use_renders_no_template_error(app):
    # smoke: the redesigned template compiles + renders with full context.
    with app.app_context():
        plan = _plan_id(); mgr = _sub_admin("ou3"); off = _offer(plan, mgr)
        oid = int(off["id"])
    html = _use_html(app, mgr, oid)
    assert "<form" in html and "توليد الحزمة" in html
