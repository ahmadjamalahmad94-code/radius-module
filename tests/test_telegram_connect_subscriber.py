"""ربط تيليجرام للمشترك — نقاط بوّابة المشترك (portal) + بطاقة الصفحة.

يكمّل test_telegram_connect.py (الذي يغطّي الخدمة + نقاط الإدارة): هنا نختبر
سطح المشترك تحديدًا — connect/start + connect/poll عبر بلوبرنت البوّابة، أنّ
الالتقاط يكتب chat_id على *ملف المشترك* (لا إعدادات المستأجر)، وأنّ بطاقة
«اربط تيليجرام» تظهر في صفحة المشترك فقط عند تهيئة بوت المزوّد. طبقة تيليجرام
HTTP مُحاكاة. شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tgc_sub.db")
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


class _FakeTelegram:
    """يحاكي api.telegram.org: getMe + طابور getUpdates + التقاط الطلبات."""

    def __init__(self, username="HobeAlertsBot", name="Hobe Alerts"):
        self.username = username
        self.name = name
        self.pending = []
        self.calls = []

    def __call__(self, token, method, params=None):
        self.calls.append((method, params))
        if method == "getMe":
            return {"ok": True, "result": {"id": 1, "username": self.username,
                                           "first_name": self.name}}
        if method == "getUpdates":
            ups, self.pending = self.pending, []
            return {"ok": True, "result": ups}
        return {"ok": True, "result": True}

    def push_start(self, code, *, update_id, chat):
        self.pending.append({"update_id": update_id,
                             "message": {"text": "/start " + code, "chat": chat}})


def _configure_bot(tid=1, token="123456:ABCDEF-test"):
    from app.radius.db.repos import tenant_telegram_settings_repo as tg
    tg.upsert(tenant_id=tid, bot_token=token, chat_id="", enabled=False, thread_id="")


def _make_subscriber(sid=7, username="sub7"):
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute(
            "INSERT INTO subscribers (id, tenant_id, username, created_at) "
            "VALUES (?, 1, ?, '2026-01-01T00:00:00Z')", (sid, username))


def _login(app_ctx, sid=7):
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["portal_tenant_id"] = 1
        s["portal_subscriber_id"] = sid
        s["_csrf_token"] = "tok"
    return c


def _post(c, path):
    return c.post(path, headers={"X-CSRFToken": "tok"})


# ════════════════════════════════════════════════════════════════════════
# (1) النقاط — start + poll عبر بلوبرنت البوّابة
# ════════════════════════════════════════════════════════════════════════
class TestSubscriberPortalRoutes:
    START = "/portal/subscriber/telegram/connect/start"
    POLL = "/portal/subscriber/telegram/connect/poll"

    def test_start_requires_session(self, app_ctx):
        c = app_ctx.test_client()  # لا جلسة مشترك
        r = c.post(self.START, headers={"X-CSRFToken": "tok"})
        # محميّ: حارس البوّابة يعيد التوجيه للدخول (302) أو الحارس الداخلي 401.
        assert r.status_code in (302, 401)
        assert r.status_code != 200

    def test_start_returns_deeplink_and_qr(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram())
        _configure_bot()
        _make_subscriber(sid=7)
        c = _login(app_ctx, 7)
        d = _post(c, self.START).get_json()
        assert d["ok"] and d["qr_svg"].startswith("<svg")
        assert d["deep_link"].endswith(d["code"])

    def test_poll_captures_to_subscriber_profile(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        from app.radius.db.connection import db
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        _configure_bot()
        _make_subscriber(sid=7)
        c = _login(app_ctx, 7)
        start = _post(c, self.START).get_json()
        # قبل START → pending.
        assert _post(c, self.POLL).get_json()["linked"] is False
        # المشترك يضغط START.
        fake.push_start(start["code"], update_id=5,
                        chat={"id": 24680, "first_name": "Sami"})
        out = _post(c, self.POLL).get_json()
        assert out["linked"] and out["account_name"] == "Sami"
        # chat_id خُزِّن على ملف المشترك (لا على إعدادات المستأجر).
        row = db().execute(
            "SELECT telegram_chat_id, telegram_account_name "
            "FROM subscribers WHERE id=7").fetchone()
        assert row["telegram_chat_id"] == "24680"
        assert row["telegram_account_name"] == "Sami"

    def test_admin_binding_untouched_by_subscriber_link(self, app_ctx, monkeypatch):
        """ربط المشترك لا يلمس chat_id الخاص بإعدادات المستأجر (الإدارة)."""
        from app.radius.services import telegram_connect as tc
        from app.radius.db.repos import tenant_telegram_settings_repo as tg
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        _configure_bot()
        _make_subscriber(sid=7)
        c = _login(app_ctx, 7)
        start = _post(c, self.START).get_json()
        fake.push_start(start["code"], update_id=9, chat={"id": 999, "title": "S"})
        _post(c, self.POLL)
        # إعدادات المستأجر بقيت بلا chat_id (لم يُفعّل بربط مشترك).
        assert (tg.get(1)["chat_id"] or "") == ""


# ════════════════════════════════════════════════════════════════════════
# (2) البطاقة في الصفحة — تظهر فقط عند تهيئة بوت المزوّد
# ════════════════════════════════════════════════════════════════════════
class TestSubscriberPortalPage:
    HOME = "/portal/subscriber"

    # عنوان قسم البطاقة يظهر في الترميز فقط عند رسمها (نصّ الـJS ثابت دومًا فلا
    # يصلح كعلامة) — فنفحص عنوان القسم «إشعارات تيليجرام».
    _CARD_MARK = "إشعارات تيليجرام"

    def test_card_hidden_without_bot(self, app_ctx):
        _make_subscriber(sid=7)
        c = _login(app_ctx, 7)
        html = c.get(self.HOME).get_data(as_text=True)
        assert self._CARD_MARK not in html  # لا بوت → لا بطاقة

    def test_card_shown_with_bot(self, app_ctx):
        _configure_bot()
        _make_subscriber(sid=7)
        c = _login(app_ctx, 7)
        html = c.get(self.HOME).get_data(as_text=True)
        assert self._CARD_MARK in html and "اربط تيليجرام" in html
