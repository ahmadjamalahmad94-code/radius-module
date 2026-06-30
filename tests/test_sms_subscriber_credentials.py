# -*- coding: utf-8 -*-
"""إرسال بيانات المشترك (اسم المستخدم + كلمة المرور) عبر SMS.

يبني على تكامل TweetSMS BYO ومحرّك إشعارات المشترك:

  * عند إنشاء مشترك وتفعيل قناة SMS لحدث «إنشاء مشترك»، تَحمل رسالة الـSMS
    اسمَ المستخدم وكلمةَ المرور معًا (قصيرة، ضمن حدّ الـ60 حرفًا/المقطع الواحد).
  * زرّ «إرسال بيانات المشترك» يُرسل نفس البيانات لجوال المشترك عند الطلب،
    ويُخطئ بوضوح بلا جوال / بلا حساب SMS مربوط.
  * كلمة المرور حسّاسة: تذهب فقط في جسم الـSMS لرقم المشترك — لا في السجلّ.

لا شبكة: نقطة HTTP الوحيدة (tweetsms._http_get) مُموّهة. شغّل الملف وحده."""
from __future__ import annotations

import os
import urllib.parse

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "smscreds.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "smscreds-secret")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield application


def _connect_sms(api_key="K", sender="HOBE"):
    from app.radius.db.repos import tenant_sms_settings_repo
    tenant_sms_settings_repo.upsert(tenant_id=1, api_key=api_key, sender=sender, enabled=True)


def _subscriber(*, username="shop1", password="Pa55wd", mobile="0599123456"):
    from app.radius.db.connection import transaction
    with transaction() as c:
        c.execute(
            "INSERT INTO subscribers(tenant_id,username,password,full_name,"
            "mobile,status,created_at) VALUES(1,?,?,'أحمد',?,'enabled','2026-01-01')",
            (username, password, mobile),
        )


def _capture_http(monkeypatch):
    """Capture the single HTTP chokepoint; return a success line."""
    captured = {}
    from app.radius.services import tweetsms

    def _fake_get(url, timeout=12.0):
        captured["url"] = url
        captured["message"] = urllib.parse.unquote_plus(
            dict(urllib.parse.parse_qsl(url.split("?", 1)[1])).get("message", "")
        )
        return True, 200, "1:42:972599123456", ""

    monkeypatch.setattr(tweetsms, "_http_get", _fake_get)
    return captured


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "creds_admin"
        sess["admin_name"] = "Creds Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "creds-csrf"


# ───────────────────────── pure-unit: body + 60-char ─────────────────────────
def test_credentials_body_contains_user_and_pass():
    from app.radius.services import subscriber_credentials as sc

    body = sc.build_body("shop1", "Pa55wd")
    assert "shop1" in body
    assert "Pa55wd" in body
    # carries Arabic labels for both fields
    assert "المستخدم" in body and "كلمة المرور" in body


def test_credentials_body_typical_fits_one_segment():
    """A typical username/password keeps the SMS within one (≤60-char) segment."""
    from app.radius.services import subscriber_credentials as sc
    from app.radius.services import sms_segments

    body = sc.build_body("shop1", "Pa55wd")
    seg = sms_segments.analyze(body)
    assert seg.segments == 1
    assert not seg.over_recommended  # ≤ 60-char owner guide


def test_subscriber_created_template_has_no_password_placeholder():
    """Only SMS carries the password — the shared (whatsapp/telegram) template
    used by «subscriber_created» must never reference {password}."""
    from app.radius.services import notifications_engine as ne

    ev = ne.EVENTS["subscriber_created"]
    assert "{password}" not in ev.template
    assert ev.sends_credentials is True


# ───────────────────────── service: send() outcomes ─────────────────────────
def test_send_includes_user_and_pass_in_sms_body(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _subscriber()
        cap = _capture_http(monkeypatch)
        from app.radius.services import subscriber_credentials as sc
        from app.radius.db.repos import subscribers_repo

        sub = subscribers_repo.get_subscriber(1, "shop1")
        res = sc.send(1, sub, actor="tester")
        assert res["ok"] is True
        assert "shop1" in cap["message"]
        assert "Pa55wd" in cap["message"]
        assert res["segments"].get("segments") == 1


def test_send_no_mobile_clear_error(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _subscriber(username="nomob", mobile="")
        _capture_http(monkeypatch)
        from app.radius.services import subscriber_credentials as sc
        from app.radius.db.repos import subscribers_repo

        res = sc.send(1, subscribers_repo.get_subscriber(1, "nomob"), actor="t")
        assert res["ok"] is False
        assert res["reason"] == "no_mobile"
        assert res["error_ar"] == "لا يوجد رقم جوال للمشترك"


def test_send_not_connected_clear_error(app, monkeypatch):
    with app.app_context():
        # No _connect_sms() → tenant has no TweetSMS account.
        _subscriber(username="nolink")
        from app.radius.services import subscriber_credentials as sc
        from app.radius.db.repos import subscribers_repo

        res = sc.send(1, subscribers_repo.get_subscriber(1, "nolink"), actor="t")
        assert res["ok"] is False
        assert res["reason"] == "not_connected"
        assert res["error_ar"] == "اربط حساب SMS أولاً"


def test_password_never_appears_in_audit_log(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _subscriber(password="TopSecret9")
        _capture_http(monkeypatch)
        from app.radius.services import subscriber_credentials as sc
        from app.radius.services.audit import get_audit_service
        from app.radius.db.repos import subscribers_repo

        sub = subscribers_repo.get_subscriber(1, "shop1")
        sc.send(1, sub, actor="tester")
        # The audit row exists, records the send, but NEVER the cleartext password.
        rows = list(get_audit_service().recent(limit=50))
        creds = [r for r in rows if r.action == "subscriber.credentials_sms"]
        assert creds, "expected a redacted credentials-sms audit row"
        import json
        for r in creds:
            assert "TopSecret9" not in json.dumps(r.payload, ensure_ascii=False)


# ───────────────────────── engine: subscriber_created SMS ───────────────────
def test_subscriber_created_sms_channel_sends_credentials(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _subscriber(password="Cr3ds!")
        cap = _capture_http(monkeypatch)
        from app.radius.services import notifications_engine as ne

        # Operator enables the SMS channel for «subscriber_created».
        ne.save_rules(
            1,
            {"subscriber_created__enabled": "1", "subscriber_created__channels": ["sms"]},
            only_keys=["subscriber_created"],
        )
        sub = ne.find_subscriber(1, username="shop1")
        out = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        assert out.fired is True
        assert out.sent.get("sms") is True
        # The SMS body carried BOTH the username and the cleartext password.
        assert "shop1" in cap["message"]
        assert "Cr3ds!" in cap["message"]


# ───────────────────────── route: «إرسال بيانات المشترك» ─────────────────────
def test_route_sends_credentials_returns_ok(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _subscriber()
    cap = _capture_http(monkeypatch)
    client = app.test_client()
    _auth(client)
    res = client.post("/admin/radius/users/shop1/send-credentials",
                      data={"_csrf_token": "creds-csrf"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert "shop1" in cap["message"] and "Pa55wd" in cap["message"]


def test_route_no_mobile_returns_arabic_error(app, monkeypatch):
    with app.app_context():
        _connect_sms()
        _subscriber(username="nomob", mobile="")
    _capture_http(monkeypatch)
    client = app.test_client()
    _auth(client)
    res = client.post("/admin/radius/users/nomob/send-credentials",
                      data={"_csrf_token": "creds-csrf"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "لا يوجد رقم جوال للمشترك"


def test_route_not_connected_returns_hint(app, monkeypatch):
    with app.app_context():
        _subscriber(username="nolink")
    client = app.test_client()
    _auth(client)
    res = client.post("/admin/radius/users/nolink/send-credentials",
                      data={"_csrf_token": "creds-csrf"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"] == "اربط حساب SMS أولاً"


def test_route_unknown_subscriber_404(app):
    client = app.test_client()
    _auth(client)
    res = client.post("/admin/radius/users/ghost/send-credentials",
                      data={"_csrf_token": "creds-csrf"})
    assert res.status_code == 404
