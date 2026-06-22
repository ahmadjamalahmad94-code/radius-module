"""دليل خطوات «اربط تيليجرام» داخل الواجهة — إدارة + مشترك.

يثبّت أنّ الخطوات المرقّمة تظهر فعليًّا في الصفحتين (نصّ حقيقي لا صور): الإدارة
تشرح المسار كاملًا مع تمييز خطوات «مرّة واحدة» (BotFather) عن «بضغطة واحدة»؛
والمشترك يرى دليلًا أقصر بلا أي ذكر لـBotFather (البوت جاهز من المزوّد).
شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tg_guide.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _configure_bot():
    from app.radius.db.repos import tenant_telegram_settings_repo as tg
    tg.upsert(tenant_id=1, bot_token="1:ABC", chat_id="", enabled=False, thread_id="")


def _make_subscriber(sid=1):
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute(
            "INSERT INTO subscribers (id, tenant_id, username, created_at) "
            "VALUES (?, 1, 'sub', '2026-01-01T00:00:00Z')", (sid,))


# ════════════════════════════════════════════════════════════════════════
# (1) دليل الإدارة — المسار الكامل + تمييز مرّة واحدة / بضغطة
# ════════════════════════════════════════════════════════════════════════
class TestAdminGuide:
    def _client(self, app_ctx):
        c = app_ctx.test_client()
        with c.session_transaction() as s:
            s["admin_id"] = 1
            s["is_super_admin"] = True
            s["tenant_id"] = 1
        return c

    def test_integrations_page_renders_guide(self, app_ctx):
        r = self._client(app_ctx).get("/admin/radius/integrations")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        # عنوان الدليل + كامل الخطوات الخمس بمعالمها.
        assert "كيف أربط تيليجرام" in html
        assert "BotFather" in html and "/newbot" in html      # خطوة 1
        assert "اربط تيليجرام" in html                          # خطوة 3
        assert "START" in html                                  # خطوة 4
        assert "متصل ✓" in html                                 # خطوة 5

    def test_admin_guide_marks_onetime_vs_oneclick(self, app_ctx):
        html = self._client(app_ctx).get(
            "/admin/radius/integrations").get_data(as_text=True)
        assert "مرّة واحدة (إعداد البوت)" in html
        assert "بضغطة واحدة (الربط)" in html


# ════════════════════════════════════════════════════════════════════════
# (2) دليل المشترك — أقصر + بلا BotFather
# ════════════════════════════════════════════════════════════════════════
class TestSubscriberGuide:
    def _client(self, app_ctx):
        c = app_ctx.test_client()
        with c.session_transaction() as s:
            s["portal_tenant_id"] = 1
            s["portal_subscriber_id"] = 1
        return c

    def test_subscriber_card_renders_short_guide(self, app_ctx):
        _configure_bot()        # بوت المزوّد مهيّأ → تظهر البطاقة + الدليل
        _make_subscriber(1)
        r = self._client(app_ctx).get("/portal/subscriber")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert 'class="sptg-guide"' in html
        assert "اربط تيليجرام" in html and "START" in html
        # المشترك لا يلمس BotFather إطلاقًا.
        assert "BotFather" not in html
        assert "newbot" not in html

    def test_guide_absent_without_bot(self, app_ctx):
        # بلا بوت مزوّد → لا بطاقة ولا دليل مشترك.
        _make_subscriber(1)
        html = self._client(app_ctx).get(
            "/portal/subscriber").get_data(as_text=True)
        # ‏class=".." يظهر في الترميز فقط عند رسم البطاقة (محدّد CSS يبقى دومًا).
        assert 'class="sptg-guide"' not in html
