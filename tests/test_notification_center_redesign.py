# -*- coding: utf-8 -*-
"""حارس إعادة تصميم مركز الإشعارات (/admin/radius/notifications).

يثبّت: (1) القائمة محتواة بعرض مقيَّد (nc-list) فلا تمتدّ على عرض الصفحة،
(2) الأوقات تُعرَض بصيغة بشريّة/نسبيّة («منذ …») أو محلّيّة مهيّأة — لا ISO
خام (`…T…Z`) في صفوف الإشعارات، (3) دالّة الوقت النسبيّ تُنتج الصيغ العربيّة
الصحيحة، (4) أزرار «تعليم كمقروء/فتح» والفلاتر والـKPI ما زالت موصولة.

شغّل الملف وحده (عزل لكل ملف)."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "notif.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.create_admin(username="owner", password="pw",
                                 full_name="owner", is_super_admin=True)
        yield application


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, is_super_admin=True, tenant_id=1, admin_name="owner")
    return c


def _seed(delta: timedelta, **kw) -> int:
    from app.radius.db.connection import transaction
    from app.radius.db.repos import notifications_repo
    nid = notifications_repo.create(1, **kw)
    when = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with transaction() as conn:
        conn.execute("UPDATE panel_notifications SET created_at=? WHERE id=?",
                     (when, nid))
    return nid


# ── (1) دالّة الوقت النسبيّ ──
class TestRelativeHelper:
    def test_arabic_relative_forms(self, app):
        with app.app_context():
            from app.radius.routes.notifications import _humanize_rel
            now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
            f = lambda d: _humanize_rel((now - d).strftime("%Y-%m-%dT%H:%M:%SZ"), now)
            assert f(timedelta(seconds=10)) == "الآن"
            assert f(timedelta(minutes=1)) == "منذ دقيقة"
            assert f(timedelta(minutes=2)) == "منذ دقيقتين"
            assert f(timedelta(minutes=6)) == "منذ 6 دقائق"
            assert f(timedelta(hours=2)) == "منذ ساعتين"
            assert f(timedelta(hours=20)) == "منذ 20 ساعة"
            assert f(timedelta(days=3)) == "منذ 3 أيام"

    def test_old_falls_back_to_local_date(self, app):
        with app.app_context():
            from app.radius.routes.notifications import _humanize_rel
            now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
            out = _humanize_rel("2026-01-01T00:00:00Z", now)
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", out)   # تاريخ محلّيّ لا «منذ»

    def test_bad_input_safe(self, app):
        with app.app_context():
            from app.radius.routes.notifications import _humanize_rel
            assert _humanize_rel("") == ""
            assert _humanize_rel(None) == ""


# ── (2) الصفحة المُعاد تصميمها ──
class TestPage:
    def test_page_renders_with_items(self, app):
        _seed(timedelta(minutes=2), type="license", severity="critical",
              title="اقترب انتهاء الترخيص", body="جدّد الآن", link="/admin/radius/license")
        _seed(timedelta(hours=2), type="system", severity="info",
              title="رسالة جسر", body="x", source="bridge")
        html = _client(app).get("/admin/radius/notifications").get_data(as_text=True)
        assert "nc-list" in html               # القائمة محتواة
        assert "max-width:760px" in html       # العرض مقيَّد فلا يمتدّ
        assert "اقترب انتهاء الترخيص" in html

    def test_no_raw_iso_in_rows(self, app):
        _seed(timedelta(minutes=2), type="license", severity="warning",
              title="t", body="b")
        _seed(timedelta(days=2), type="service", severity="info", title="t2", body="b2")
        html = _client(app).get("/admin/radius/notifications").get_data(as_text=True)
        # لا طابع زمنيّ ISO خام (…T…Z) ظاهر في الصفحة بعد التهيئة المحلّيّة.
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", html)
        assert "منذ" in html                   # صيغة بشريّة حاضرة

    def test_actions_and_filters_wired(self, app):
        nid = _seed(timedelta(minutes=5), type="license", severity="info",
                    title="t", body="b", link="/admin/radius/license")
        c = _client(app)
        html = c.get("/admin/radius/notifications").get_data(as_text=True)
        # نقاط النهاية كما هي (تعليم كمقروء / فتح / فلتر غير المقروء / تعليم الكل)
        assert f"/notifications/{nid}/read" in html
        assert f"/notifications/{nid}/open" in html
        assert "filter=unread" in html
        assert "/notifications/read-all" in html
        assert "دفع الإشعارات للجوال" in html   # بطاقة الدفع باقية

    def test_unread_filter_endpoint(self, app):
        _seed(timedelta(minutes=2), type="license", severity="info",
              title="UNREAD_ITEM_ZZ", body="b")
        from app.radius.db.repos import notifications_repo
        # واحد مقروء + واحد غير مقروء
        rid = _seed(timedelta(minutes=3), type="system", severity="info",
                    title="READ_ITEM_ZZ", body="b")
        notifications_repo.mark_read(1, rid)
        c = _client(app)
        all_html = c.get("/admin/radius/notifications").get_data(as_text=True)
        unread_html = c.get(
            "/admin/radius/notifications?filter=unread").get_data(as_text=True)
        # المقروء يظهر في «الكل» (القائمة + جرس الشريط العلويّ) لكنّه يَسقط من
        # القائمة الرئيسيّة عند الفلتر → عدد ظهوره يَنقص؛ غير المقروء ثابت.
        assert unread_html.count("READ_ITEM_ZZ") < all_html.count("READ_ITEM_ZZ")
        assert "UNREAD_ITEM_ZZ" in unread_html
