"""telegram_connect — «اربط تيليجرام» بضغطة واحدة.

يغطّي: توليد رمز الربط + مطابقته، تحليل اسم البوت من getMe، التقاط
``/start <code>`` → تخزين chat_id (إدارة + مشترك)، إزاحة getUpdates (لا تكرار)،
المسار اليدوي البديل، ونقطتا الويب connect/start + connect/poll. طبقة HTTP
لتيليجرام مُحاكاة بالكامل (لا شبكة). شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tgc.db")
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
    """يحاكي api.telegram.org: getMe ثابت + طابور getUpdates + التقاط الطلبات."""

    def __init__(self, username="HobeAlertsBot", name="Hobe Alerts",
                 me_ok=True, network_fail=False):
        self.username = username
        self.name = name
        self.me_ok = me_ok
        self.network_fail = network_fail
        self.pending = []      # تحديثات تُسلَّم في getUpdates التالي
        self.calls = []        # (method, params)

    def __call__(self, token, method, params=None):
        self.calls.append((method, params))
        if self.network_fail:
            from app.radius.services.telegram_connect import TelegramNetworkError
            raise TelegramNetworkError("DNS")
        if method == "getMe":
            if not self.me_ok:
                return {"ok": False, "error_code": 401,
                        "description": "Unauthorized"}
            return {"ok": True, "result": {"id": 777, "username": self.username,
                                           "first_name": self.name}}
        if method == "deleteWebhook":
            return {"ok": True, "result": True}
        if method == "getUpdates":
            ups, self.pending = self.pending, []
            return {"ok": True, "result": ups}
        return {"ok": True, "result": True}

    def push_start(self, code, *, update_id, chat):
        self.pending.append({"update_id": update_id,
                             "message": {"text": "/start " + code, "chat": chat}})


def _save_token(tid=1, token="123456:ABCDEF-test"):
    from app.radius.db.repos import tenant_telegram_settings_repo as tg
    tg.upsert(tenant_id=tid, bot_token=token, chat_id="", enabled=False,
              thread_id="")


# ════════════════════════════════════════════════════════════════════════
# (1) رمز الربط — توليد + مطابقة + إبطال السابق
# ════════════════════════════════════════════════════════════════════════
class TestLinkCodes:
    def test_create_and_lookup(self, app_ctx):
        from app.radius.db.repos import telegram_link_codes_repo as cr
        rec = cr.create_code(tenant_id=1, scope="admin")
        assert rec["status"] == "pending" and len(rec["code"]) == 10
        got = cr.get_by_code(rec["code"])
        assert got and got["code"] == rec["code"] and got["scope"] == "admin"

    def test_create_invalidates_previous_pending(self, app_ctx):
        from app.radius.db.repos import telegram_link_codes_repo as cr
        a = cr.create_code(tenant_id=1, scope="admin")
        b = cr.create_code(tenant_id=1, scope="admin")
        assert cr.get_by_code(a["code"])["status"] == "expired"
        assert cr.get_active(tenant_id=1, scope="admin")["code"] == b["code"]

    def test_codes_are_unique(self, app_ctx):
        from app.radius.db.repos import telegram_link_codes_repo as cr
        codes = {cr.create_code(tenant_id=1, scope="subscriber",
                                subscriber_id=i)["code"] for i in range(25)}
        assert len(codes) == 25

    def test_mark_linked_is_idempotent(self, app_ctx):
        from app.radius.db.repos import telegram_link_codes_repo as cr
        rec = cr.create_code(tenant_id=1, scope="admin")
        assert cr.mark_linked(rec["code"], chat_id="55", account_name="X")
        # ثانيًا: لم يعد pending → لا يتغيّر.
        assert not cr.mark_linked(rec["code"], chat_id="99")
        assert cr.get_by_code(rec["code"])["chat_id"] == "55"

    def test_expire_stale(self, app_ctx):
        from app.radius.db.repos import telegram_link_codes_repo as cr
        rec = cr.create_code(tenant_id=1, scope="admin", ttl_sec=-5)  # منتهٍ
        assert cr.get_active(tenant_id=1, scope="admin") is None
        cr.expire_stale()
        assert cr.get_by_code(rec["code"])["status"] == "expired"

    def test_cursor_roundtrip(self, app_ctx):
        from app.radius.db.repos import telegram_link_codes_repo as cr
        assert cr.get_cursor(1) == 0
        cr.set_cursor(1, 42)
        assert cr.get_cursor(1) == 42


# ════════════════════════════════════════════════════════════════════════
# (2) getMe — تحليل اسم البوت
# ════════════════════════════════════════════════════════════════════════
class TestGetMe:
    def test_parses_username(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram(username="MyCoolBot"))
        me = tc.get_me("123:ABC")
        assert me["ok"] and me["username"] == "MyCoolBot" and me["name"]

    def test_no_token(self, app_ctx):
        from app.radius.services import telegram_connect as tc
        assert tc.get_me("")["ok"] is False

    def test_bad_token(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram(me_ok=False))
        me = tc.get_me("123:bad")
        assert me["ok"] is False and "رفض" in me["error"]

    def test_network_failure(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram(network_fail=True))
        me = tc.get_me("123:ABC")
        assert me["ok"] is False and "تعذّر الاتصال" in me["error"]


# ════════════════════════════════════════════════════════════════════════
# (3) start_link — رابط عميق + QR
# ════════════════════════════════════════════════════════════════════════
class TestStartLink:
    def test_requires_token(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram())
        res = tc.start_link(1, scope="admin")  # لا توكن محفوظ
        assert res["ok"] is False and "توكن" in res["error"]

    def test_builds_deep_link_and_qr(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        fake = _FakeTelegram(username="HobeAlertsBot")
        monkeypatch.setattr(tc, "_api_call", fake)
        _save_token()
        res = tc.start_link(1, scope="admin")
        assert res["ok"]
        assert res["deep_link"] == "https://t.me/HobeAlertsBot?start=" + res["code"]
        assert res["qr_svg"].startswith("<svg")
        # getMe استُدعي لاشتقاق الاسم.
        assert any(c[0] == "getMe" for c in fake.calls)

    def test_passed_token_is_saved(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        from app.radius.db.repos import tenant_telegram_settings_repo as tg
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram())
        res = tc.start_link(1, scope="admin", token="999:NEWTOKEN")
        assert res["ok"]
        assert tg.get(1)["bot_token"] == "999:NEWTOKEN"


# ════════════════════════════════════════════════════════════════════════
# (4) poll_link — التقاط /start → chat_id (المحور)
# ════════════════════════════════════════════════════════════════════════
class TestPollCapture:
    def test_admin_capture_binds_chat_id_and_enables(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        from app.radius.db.repos import tenant_telegram_settings_repo as tg
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        _save_token()
        res = tc.start_link(1, scope="admin")
        # قبل START: معلّق.
        assert tc.poll_link(1, scope="admin")["status"] == "pending"
        # المستخدم يضغط START.
        fake.push_start(res["code"], update_id=10,
                        chat={"id": -100999, "title": "Ops Group"})
        out = tc.poll_link(1, scope="admin")
        assert out["linked"] and out["account_name"] == "Ops Group"
        # chat_id خُزّن في إعدادات المستأجر + فُعّلت القناة.
        cfg = tg.get(1)
        assert cfg["chat_id"] == "-100999" and cfg["enabled"] is True

    def test_private_chat_name(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        _save_token()
        res = tc.start_link(1, scope="admin")
        fake.push_start(res["code"], update_id=3,
                        chat={"id": 555, "first_name": "Ali", "last_name": "K"})
        assert tc.poll_link(1, scope="admin")["account_name"] == "Ali K"

    def test_subscriber_capture_writes_profile(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        from app.radius.db.connection import db, transaction
        # أنشئ مشتركًا.
        with transaction() as conn:
            conn.execute(
                "INSERT INTO subscribers (id, tenant_id, username, created_at) "
                "VALUES (42, 1, 'sub42', '2026-01-01T00:00:00Z')")
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        _save_token()
        res = tc.start_link(1, scope="subscriber", subscriber_id=42)
        fake.push_start(res["code"], update_id=7,
                        chat={"id": 12345, "first_name": "Sub", "username": "subtg"})
        out = tc.poll_link(1, scope="subscriber", subscriber_id=42)
        assert out["linked"] and out["account_name"] == "Sub"
        row = db().execute(
            "SELECT telegram_chat_id FROM subscribers WHERE id=42").fetchone()
        assert row["telegram_chat_id"] == "12345"

    def test_wrong_code_ignored(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        _save_token()
        tc.start_link(1, scope="admin")
        fake.push_start("WRONGCODE9", update_id=2, chat={"id": 1})
        assert tc.poll_link(1, scope="admin")["linked"] is False

    def test_cursor_advances_no_reprocess(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        from app.radius.db.repos import telegram_link_codes_repo as cr
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        _save_token()
        res = tc.start_link(1, scope="admin")
        fake.push_start(res["code"], update_id=20, chat={"id": 9, "title": "G"})
        tc.poll_link(1, scope="admin")
        # المؤشّر تقدّم خلف 20 → التحديث لن يُعاد تسليمه (الطابور فرغ أصلًا).
        assert cr.get_cursor(1) == 21


# ════════════════════════════════════════════════════════════════════════
# (5) المسار اليدوي البديل — حفظ chat_id يدويًّا
# ════════════════════════════════════════════════════════════════════════
class TestManualFallback:
    def test_manual_save_still_works(self, app_ctx):
        from app.radius.db.repos import tenant_telegram_settings_repo as tg
        tg.upsert(tenant_id=1, bot_token="123:ABC", chat_id="-100777",
                  enabled=True, thread_id="9")
        cfg = tg.get(1)
        assert cfg["chat_id"] == "-100777" and cfg["enabled"] and cfg["thread_id"] == "9"


# ════════════════════════════════════════════════════════════════════════
# (6) نقاط الويب — connect/start + connect/poll (AJAX)
# ════════════════════════════════════════════════════════════════════════
class TestRoutes:
    _CSRF = "test-csrf-token"

    def _client(self, app_ctx):
        c = app_ctx.test_client()
        with c.session_transaction() as s:
            s["admin_id"] = 1
            s["is_super_admin"] = True
            s["tenant_id"] = 1
            s["_csrf_token"] = self._CSRF
        return c

    def _post(self, c, path, **data):
        return c.post(path, data=data or None,
                      headers={"X-CSRFToken": self._CSRF})

    def test_connect_start_route(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram())
        c = self._client(app_ctx)
        r = self._post(c, "/admin/radius/alerts/telegram/connect/start",
                       bot_token="123:ABC")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] and d["username"] == "HobeAlertsBot"
        assert d["qr_svg"].startswith("<svg") and "start=" in d["deep_link"]

    def test_connect_poll_route_captures(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        fake = _FakeTelegram()
        monkeypatch.setattr(tc, "_api_call", fake)
        c = self._client(app_ctx)
        start = self._post(c, "/admin/radius/alerts/telegram/connect/start",
                           bot_token="123:ABC").get_json()
        # قبل START → pending.
        p1 = self._post(c, "/admin/radius/alerts/telegram/connect/poll").get_json()
        assert p1["linked"] is False
        # START.
        fake.push_start(start["code"], update_id=1,
                        chat={"id": -100123, "title": "Admins"})
        p2 = self._post(c, "/admin/radius/alerts/telegram/connect/poll").get_json()
        assert p2["linked"] and p2["account_name"] == "Admins"

    def test_connect_start_no_token_returns_error_not_500(self, app_ctx, monkeypatch):
        from app.radius.services import telegram_connect as tc
        monkeypatch.setattr(tc, "_api_call", _FakeTelegram())
        c = self._client(app_ctx)
        r = self._post(c, "/admin/radius/alerts/telegram/connect/start")
        assert r.status_code == 200
        assert r.get_json()["ok"] is False
