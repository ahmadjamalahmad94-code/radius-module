# -*- coding: utf-8 -*-
"""إشعار «إنشاء حساب مستفيد» (طلب المالك 2026-07-27).

عند إضافة مستفيد من اللوحة (source=admin) أو تسجيله الذاتي من المتجر
(source=store) تصل رسالة بيانات الدخول: اسم المستخدم (= رقم الجوال)
وكلمة المرور — حدث ``store_account_created`` (مفعّل افتراضيًّا) عبر
SMS/واتساب مباشرة، وكلمة المرور لا تُسجَّل أبدًا (تدقيق منقّح فقط).

لا شبكة: نقطة HTTP الوحيدة (tweetsms._http_get) مُموّهة. شغّل الملف وحده.
"""
from __future__ import annotations

import os
import urllib.parse

import pytest

PASSWORD = "sirri4567"
MOBILE = "0590000777"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "store_acct.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "store-acct-secret")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


def _connect_sms():
    from app.radius.db.repos import tenant_sms_settings_repo
    tenant_sms_settings_repo.upsert(tenant_id=1, api_key="K", sender="HOBE", enabled=True)


def _capture_http(monkeypatch):
    captured = {}
    from app.radius.services import tweetsms

    def _fake_get(url, timeout=12.0):
        captured["url"] = url
        captured["message"] = urllib.parse.unquote_plus(
            dict(urllib.parse.parse_qsl(url.split("?", 1)[1])).get("message", "")
        )
        captured["to"] = dict(urllib.parse.parse_qsl(url.split("?", 1)[1])).get("to", "")
        return True, 200, "1:42:972599123456", ""

    monkeypatch.setattr(tweetsms, "_http_get", _fake_get)
    return captured


def _register(source: str):
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
    return CardUsersMarketplaceService(tenant_id=1).register_card_user(
        display_name="أحمد محمد علي", mobile=MOBILE,
        password=PASSWORD, source=source)


def test_event_registered_and_default_on(app):
    from app.radius.services import notifications_engine as ne
    ev = ne.EVENTS.get("store_account_created")
    assert ev is not None and ev.group == "store"
    assert ev.sends_account_credentials is True
    assert ev.default_enabled is True
    assert ev.channels == ("sms", "whatsapp")


def test_self_registration_sends_credentials_sms(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        captured = _capture_http(monkeypatch)
        _register("store")
    assert "تم إنشاء حسابك" in captured.get("message", "")
    assert MOBILE in captured["message"]          # اسم المستخدم = رقم الجوال
    assert PASSWORD in captured["message"]
    assert captured.get("to")                     # أُرسلت لرقم المستفيد


def test_admin_add_sends_credentials_sms(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        captured = _capture_http(monkeypatch)
        _register("admin")
    assert "تم إنشاء حسابك" in captured.get("message", "")
    assert PASSWORD in captured["message"]


def test_password_never_in_audit_or_deliveries(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _capture_http(monkeypatch)
        _register("store")
        from app.radius.db.connection import db
        rows = db().execute("SELECT action, payload_json FROM audit_log").fetchall()
        acct = [r for r in rows if "account_credentials" in (r["action"] or "")]
        assert acct, "يجب أن يوجد صفّ تدقيق منقّح للإرسال"
        for r in rows:
            assert PASSWORD not in str(r["payload_json"] or "")
        n = db().execute("SELECT COUNT(*) AS c FROM message_deliveries").fetchone()
        assert int(n["c"]) == 0  # الإرسال مباشر — لا صفوف تسليم بجسم الرسالة


def test_disabled_event_sends_nothing(app, monkeypatch):
    with app.app_context():
        from app.radius.services import notifications_engine as ne
        ne.save_rules(1, {"store_account_created__enabled": ""},
                      only_keys=["store_account_created"])
        _connect_sms()
        captured = _capture_http(monkeypatch)
        _register("store")
    assert "message" not in captured  # لا أي إرسال


def test_registration_survives_sms_failure(app, monkeypatch):
    """فشل الإرسال لا يكسر إنشاء الحساب أبدًا."""
    with app.app_context():
        _connect_sms()
        from app.radius.services import tweetsms

        def _boom(url, timeout=12.0):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(tweetsms, "_http_get", _boom)
        user = _register("store")
        assert int(user["id"]) > 0
