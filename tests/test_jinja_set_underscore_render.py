"""حارس فخّ Jinja «{% set _ = ... %} يطمس gettext _» (يوليو 2026).

العلّة العامة (راجع memory jinja-set-underscore-clobbers-gettext): أي
`{% set _ = expr %}` يجعل `_` قيمة التعبير (غالبًا None لأن ‎.update/.append
تُرجع None)، فأي نداء لاحق `{{ _('...') }}` في نفس النطاق يرمي
TypeError 'NoneType' object is not callable → صفحة 500. سقطت بها
cards_checker_v2 فعليًا في الإنتاج، وبقيت كامنةً في ثلاث صفحات أخرى
(داخل ماكرو الترقيم أو حلقة for — نطاق مغلق اليوم، لكن أي تعديل مستقبلي
يضيف ‎{{ _('...') }} بعد السطر داخل النطاق نفسه يفجّرها). عولجت
بإعادة التسمية إلى `_x`.

يُغطّي (تصيير 200 + نصّ مُعرَّب يُصيَّر بعد سطر الـset، مع تفعيل السطر
نفسه فعليًا عبر ترقيم متعدّد الصفحات/صفوف حقيقية):
  1) ‎/cards/batches — ماكروا الترقيم (card_pglink + pglink).
  2) ‎/cards — ماكرو الترقيم page_link.
  3) ملف عرض السوق card_marketplace_package_file — حلقتا بناء صفوف
     «المخزون المتبقّي» و«المشتريات».

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
    db_file = os.path.join(tmp_path, "jinja_set_underscore.db")
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
        sess["admin_user"] = "jinja_admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "jinja-csrf"


def _plan_id(name="باقة الحارس") -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            created_at, updated_at
        ) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 8 * 60, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


# ═══════════════════════════════════════════════════════════════════════
# (1) ‏/cards/batches — ترقيم متعدد الصفحات يُشغّل سطرَي set داخل ماكروَي
#     الترقيم، ثم نصّ مُعرَّب بعدهما في نفس القالب (مودال «إضافة حزمة
#     سريعة») يُصيَّر سليمًا.
# ═══════════════════════════════════════════════════════════════════════
def test_cards_batches_renders_paginated_with_gettext_after_pager(app):
    _bind_test_db(app)
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        plan = _plan_id()
        svc = get_cards_service()
        # ‏11 حزمة مع per_page=10 ⇒ صفحتان ⇒ رابط صفحة 2 يُبنى فعليًا
        # (سطر {% set _x = qs.update({'page': p}) %} يُنفَّذ).
        for _i in range(11):
            svc.generate_batch(
                actor="test", plan_id=plan, count=1,
                username_length=8, password_length=6,
                time_value=1, time_unit="days",
            )
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/cards/batches?per_page=10")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # رابط الصفحة الثانية موجود ⇒ سطر الـset داخل الماكرو نُفِّذ.
    assert "page=2" in html
    # نصّ مُعرَّب يأتي بعد الترقيم في القالب — يثبت أن _() ما زالت دالة.
    assert "إضافة حزمة سريعة" in html


# ═══════════════════════════════════════════════════════════════════════
# (2) ‏/cards — نفس الفكرة لماكرو page_link في قائمة كل الكروت.
# ═══════════════════════════════════════════════════════════════════════
def test_cards_list_renders_paginated_with_gettext(app):
    _bind_test_db(app)
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        plan = _plan_id("باقة القائمة")
        # ‏26 كرتًا مع per_page=25 ⇒ صفحتان ⇒ page_link(2) يُبنى فعليًا.
        get_cards_service().generate_batch(
            actor="test", plan_id=plan, count=26,
            username_length=8, password_length=6,
            time_value=1, time_unit="days",
        )
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/cards?per_page=25")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "page=2" in html
    # نصّ مُعرَّب على مسار الترقيم نفسه (aria-label للـnav) صُيِّر سليمًا،
    # والصفحة اكتملت للنهاية (السكربت الختامي بعد كتلة الترقيم).
    assert "تنقّل بين الصفحات" in html
    assert "uds_table.js" in html


# ═══════════════════════════════════════════════════════════════════════
# (3) ملف عرض السوق — مخزون 25 بطاقة (صفحتا offer_cards ⇒ مُرقِّم مُعرَّب
#     بعد حلقة card_rows) + عملية شراء واحدة (حلقة rows تُنفَّذ) ثم نص
#     مُعرَّب بعدها (مودال «رفع ملف بطاقات»).
# ═══════════════════════════════════════════════════════════════════════
def test_marketplace_package_file_renders_rows_and_gettext(app):
    _bind_test_db(app)
    with app.app_context():
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        service = CardUsersMarketplaceService(tenant_id=1)
        package = service.create_package(
            name="عرض حارس الجينجا",
            plan_id=_plan_id("باقة السوق"),
            duration_minutes=8 * 60,
            speed_down_kbps=2048,
            speed_up_kbps=512,
            price="5.00",
            sale_mode="inventory",
        )
        service.add_inventory_stock(package_id=int(package["id"]), count=25)
        buyer = service.register_card_user(
            display_name="زبون حارس الجينجا",
            mobile="0791234567",
            password="pass1234",
        )
        service.recharge_wallet(card_user_id=int(buyer["id"]), amount="10.00")
        service.purchase_package(
            card_user_id=int(buyer["id"]), package_id=int(package["id"])
        )
        package_id = int(package["id"])
    with app.test_client() as client:
        _auth_session(client)
        res = client.get(
            f"/admin/radius/card-marketplace/packages/{package_id}/file"
        )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # جدولا المخزون والمشتريات (حلقتا الـset نُفِّذتا على صفوف حقيقية).
    assert "المخزون المتبقّي" in html
    assert "المشتركون (المباعة)" in html
    # مُرقِّم بطاقات العرض (نص مُعرَّب بعد حلقة card_rows) — يظهر لأن
    # ‏25 بطاقة > صفحة واحدة (20).
    assert "ترقيم بطاقات العرض" in html
    # نصّ مُعرَّب بعد حلقة rows (مودال رفع المخزون) صُيِّر سليمًا.
    assert "رفع ملف بطاقات" in html
