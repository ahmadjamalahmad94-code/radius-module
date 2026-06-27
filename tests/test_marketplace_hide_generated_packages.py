"""إخفاء كتلة «الحزم الإلكترونية المتولّدة» من صفحة سوق البطاقات (يونيو 2026).

طلب المالك: «منع ظهور فقط» — تقليل ازدحام صفحة السوق بإخفاء شبكة «الحزم
الإلكترونية المتولّدة» (سجل فني)، دون حذف البيانات/المسارات. الكروت تُعرَض
الآن داخل كل عرض (ملف العرض → «المخزون المتبقّي» أعلى الصفحة).

يُغطّي:
  1) صفحة /card-marketplace لم تَعُد تَعرض كتلة «الحزم الإلكترونية المتولّدة»
     (لا العنوان ولا شبكة batch-grid).
  2) باقي الصفحة سليم: قائمة العروض «المتوفرة» تبقى (package-grid + اسم العرض).
  3) ملف العرض (card_marketplace_package_file) ما يزال يَعرض جدول البطاقات
     «المخزون المتبقّي» أعلى جدول «المشتريات / المشتركون».

شغّل وحده (عزل لكل ملف) — راجع memory test-isolation-per-file.
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
    db_file = os.path.join(tmp_path, "marketplace_hide.db")
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


def _bind_test_db(app):
    os.environ["HOBERADIUS_DB_PATH"] = app.config["_HOBERADIUS_TEST_DB_FILE"]
    _reset_for_tests(app.config["_HOBERADIUS_TEST_DB_FILE"])


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "card_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "card-csrf"


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "Marketplace 8h", 8 * 60, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


_OFFER_NAME = "8 hours / 2 Mbps"


def _seed_offer(app):
    """Create one marketplace offer (package) and return it."""
    _bind_test_db(app)
    with app.app_context():
        from app.radius.db.repos import tenants_repo, admins_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        service = CardUsersMarketplaceService(tenant_id=1)
        return service.create_package(
            name=_OFFER_NAME,
            plan_id=_plan_id(),
            duration_minutes=8 * 60,
            speed_down_kbps=2048,
            speed_up_kbps=512,
            price="5.00",
            sale_mode="inventory",
        )


# ═══════════════════════════════════════════════════════════════════════
# (1) صفحة السوق — كتلة «الحزم الإلكترونية المتولّدة» مخفيّة
# ═══════════════════════════════════════════════════════════════════════
def test_marketplace_hides_generated_packages_block(app):
    package = _seed_offer(app)
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/card-marketplace")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # الكتلة المخفيّة: لا عنوانها ولا وسمها الفنّي ولا فقرة وصفها تُصيَّر.
    # (نَعتمد على نصّ المحتوى لا على أسماء صفوف CSS — فـbatch-grid يَبقى
    #  كقاعدة style مشتركة مع package-grid حتى لو لم تُصيَّر الكتلة.)
    assert "الحزم الإلكترونية المتولدة" not in html
    assert "(سجل فني)" not in html
    assert "كل عملية شراء تولّد كرتاً" not in html


# ═══════════════════════════════════════════════════════════════════════
# (2) باقي صفحة السوق سليم — قائمة العروض «المتوفرة» تبقى
# ═══════════════════════════════════════════════════════════════════════
def test_marketplace_offers_list_still_renders(app):
    package = _seed_offer(app)
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/card-marketplace")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # قائمة العروض (package-grid) واسم العرض ما يزالان ظاهرَين.
    assert "package-grid" in html
    assert package["name"] in html
    # وزرّ «فتح العرض» إلى ملف العرض (حيث تُعرَض الكروت) موجود.
    assert f"/card-marketplace/packages/{package['id']}/file" in html


# ═══════════════════════════════════════════════════════════════════════
# (3) ملف العرض — جدول البطاقات «المخزون المتبقّي» أعلى «المشتريات»
# ═══════════════════════════════════════════════════════════════════════
def test_offer_file_shows_cards_table_at_top(app):
    package = _seed_offer(app)
    with app.test_client() as client:
        _auth_session(client)
        res = client.get(
            f"/admin/radius/card-marketplace/packages/{package['id']}/file"
        )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    remaining_h = "المخزون المتبقّي"
    purchases_h = "المشتركون (المباعة)"
    assert remaining_h in html, "جدول كروت العرض «المخزون المتبقّي» مفقود"
    assert purchases_h in html
    # «المخزون المتبقّي» (الكروت) يَسبق جدول «المشتريات» في الصفحة (أعلى).
    assert html.index(remaining_h) < html.index(purchases_h)
