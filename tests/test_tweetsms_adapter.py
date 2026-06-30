"""TweetSMS adapter + per-customer SMS connection page + bundle-removal tests.

NO network anywhere: the adapter's single HTTP chokepoint (``tweetsms._http_get``)
is monkeypatched. We assert:

  * build_send_url builds the correct ``comm=sendsms`` URL for BOTH auth
    variants (api_key and user/pass), and URL-encodes Arabic message bodies.
  * parse_send_response maps Result 1 → success (SMS_ID) and every error code to
    its Arabic message; multi-recipient responses parse per line.
  * check_balance parses a numeric balance and an error code.
  * The encrypted repo round-trips secrets and never stores plaintext.
  * The per-tenant provider + the connection page render/save/test work.
  * The retired admin bundle/quota routes + nav are gone (no regression).
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


# ─────────────────────────── pure-unit tests (no app) ───────────────────────
def test_build_send_url_api_key_variant():
    from app.radius.services import tweetsms

    url = tweetsms.build_send_url(to="972599123456", message="hello", sender="HOBE", api_key="KEY123")
    assert url.startswith("https://www.tweetsms.ps/api.php?")
    assert "comm=sendsms" in url
    assert "api_key=KEY123" in url
    assert "to=972599123456" in url
    assert "sender=HOBE" in url
    assert "message=hello" in url
    # api_key variant must NOT leak user/pass params
    assert "user=" not in url and "pass=" not in url


def test_build_send_url_user_pass_variant():
    from app.radius.services import tweetsms

    url = tweetsms.build_send_url(to="972599123456", message="hi", sender="HOBE",
                                  username="bob", password="s3cret")
    assert "user=bob" in url
    assert "pass=s3cret" in url
    assert "api_key=" not in url


def test_build_send_url_url_encodes_arabic_message():
    from app.radius.services import tweetsms

    url = tweetsms.build_send_url(to="972599123456", message="مرحبا بك", sender="HOBE", api_key="K")
    # The Arabic body must be percent-encoded (no raw Arabic, spaces encoded).
    assert "مرحبا" not in url
    assert "message=%D9%85" in url  # UTF-8 percent-encoding of "م…"
    assert " " not in url


def test_build_balance_url_variants():
    from app.radius.services import tweetsms

    assert "comm=chk_balance" in tweetsms.build_balance_url(api_key="K")
    assert "api_key=K" in tweetsms.build_balance_url(api_key="K")
    up = tweetsms.build_balance_url(username="bob", password="pw")
    assert "user=bob" in up and "pass=pw" in up


def test_parse_send_response_success_returns_sms_id():
    from app.radius.services import tweetsms

    results = tweetsms.parse_send_response("1:987654:972599123456", ["972599123456"])
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["code"] == "1"
    assert results[0]["sms_id"] == "987654"
    assert results[0]["to"] == "972599123456"
    assert results[0]["message_ar"] == "تم الإرسال بنجاح"


@pytest.mark.parametrize("code,expected_ar", [
    ("-2",   "رقم غير صالح أو دولة غير مدعومة"),
    ("-999", "فشل لدى المزوّد"),
    ("u",    "حالة غير معروفة من المزوّد"),
    ("-100", "بيانات ناقصة في الطلب"),
    ("-110", "بيانات الدخول خاطئة (مفتاح API أو اسم المستخدم/كلمة المرور)"),
    ("-113", "الرصيد غير كافٍ"),
    ("-115", "اسم المرسل غير متاح"),
    ("-116", "اسم المرسل غير صالح"),
])
def test_arabic_for_code_maps_every_error(code, expected_ar):
    from app.radius.services import tweetsms

    assert tweetsms.arabic_for_code(code) == expected_ar


def test_parse_send_response_bare_request_level_error_applies_to_all():
    from app.radius.services import tweetsms

    # A bare "-110" (no colons) is a request-level error → applies to every number.
    results = tweetsms.parse_send_response("-110", ["972599111111", "972599222222"])
    assert len(results) == 2
    assert all(r["ok"] is False for r in results)
    assert all(r["code"] == "-110" for r in results)
    assert all("بيانات الدخول خاطئة" in r["message_ar"] for r in results)


def test_parse_send_response_multiple_recipients_per_line():
    from app.radius.services import tweetsms

    text = "1:111:972599111111\n-2:0:972599222222"
    results = tweetsms.parse_send_response(text, ["972599111111", "972599222222"])
    assert len(results) == 2
    assert results[0]["ok"] is True and results[0]["sms_id"] == "111"
    assert results[1]["ok"] is False and results[1]["code"] == "-2"


def test_parse_balance_response_numeric_and_error():
    from app.radius.services import tweetsms

    okp = tweetsms.parse_balance_response("152.5")
    assert okp["ok"] is True and okp["balance"] == 152.5
    errp = tweetsms.parse_balance_response("-110")
    assert errp["ok"] is False and "بيانات الدخول خاطئة" in errp["error_ar"]


def test_normalize_recipient_strips_plus_and_applies_dial_code():
    from app.radius.services import tweetsms

    # local 0-prefixed with a +970 dial code → 970…, no leading + / 00
    assert tweetsms.normalize_recipient("0599123456", "+970") == "970599123456"
    assert tweetsms.normalize_recipient("+972599123456") == "972599123456"
    assert tweetsms.normalize_recipient("00972599123456") == "972599123456"


# ──────────────────────────── app-backed tests ──────────────────────────────
@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tweetsms.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "tweetsms-test-secret")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "sms_admin"
        sess["admin_name"] = "SMS Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "sms-csrf"


def test_repo_encrypts_secrets_at_rest(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import tenant_sms_settings_repo

        tenant_sms_settings_repo.upsert(
            tenant_id=1, api_key="SUPER-SECRET-KEY", sender="HOBE", enabled=True,
        )
        # Stored value is encrypted (enc: prefix), never the plaintext key.
        row = db().execute("SELECT api_key FROM tenant_sms_settings WHERE tenant_id=1").fetchone()
        assert row["api_key"].startswith("enc:")
        assert "SUPER-SECRET-KEY" not in row["api_key"]
        # But get() returns the decrypted value.
        cfg = tenant_sms_settings_repo.get(1)
        assert cfg["api_key"] == "SUPER-SECRET-KEY"
        assert tenant_sms_settings_repo.is_configured(1) is True


def test_send_sms_through_adapter_builds_url_and_parses(app, monkeypatch):
    with app.app_context():
        from app.radius.services import tweetsms
        from app.radius.db.repos import tenant_sms_settings_repo

        tenant_sms_settings_repo.upsert(tenant_id=1, api_key="K", sender="HOBE", enabled=True)

        captured = {}

        def _fake_get(url, timeout=12.0):
            captured["url"] = url
            return True, 200, "1:42:972599123456", ""

        monkeypatch.setattr(tweetsms, "_http_get", _fake_get)
        out = tweetsms.send_sms(1, "0599123456", "مرحبا")
        assert out["ok"] is True
        assert out["sent_count"] == 1
        assert out["results"][0]["sms_id"] == "42"
        # The built URL carried the encoded Arabic body + the sender + api_key.
        assert "comm=sendsms" in captured["url"]
        assert "sender=HOBE" in captured["url"]
        assert "api_key=K" in captured["url"]
        assert "مرحبا" not in captured["url"]


def test_send_sms_maps_auth_error_to_arabic(app, monkeypatch):
    with app.app_context():
        from app.radius.services import tweetsms
        from app.radius.db.repos import tenant_sms_settings_repo

        tenant_sms_settings_repo.upsert(tenant_id=1, api_key="BAD", sender="HOBE", enabled=True)
        monkeypatch.setattr(tweetsms, "_http_get", lambda url, timeout=12.0: (True, 200, "-110", ""))
        out = tweetsms.send_sms(1, "0599123456", "x")
        assert out["ok"] is False
        assert "بيانات الدخول خاطئة" in out["error_ar"]


def test_provider_for_channel_sms_is_tweetsms(app):
    with app.app_context():
        from app.radius.services import comms_providers
        from app.radius.services.tweetsms import TweetSmsProvider

        provider = comms_providers.provider_for_channel(1, "sms")
        assert isinstance(provider, TweetSmsProvider)


def test_sms_connection_page_renders(app):
    client = app.test_client()
    _auth(client)
    res = client.get("/admin/radius/sms")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'data-testid="sms-save-form"' in html
    assert "TweetSMS" in html
    assert "فحص الرصيد" in html
    assert "إرسال رسالة تجربة" in html


def test_sms_save_persists_encrypted(app):
    client = app.test_client()
    _auth(client)
    res = client.post("/admin/radius/sms/save", data={
        "_csrf_token": "sms-csrf",
        "auth_mode": "api_key",
        "api_key": "MY-KEY-9999",
        "sender": "HOBE",
        "enabled": "1",
    })
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import tenant_sms_settings_repo
        cfg = tenant_sms_settings_repo.get(1)
        assert cfg["api_key"] == "MY-KEY-9999"
        assert cfg["sender"] == "HOBE"
        assert cfg["enabled"] is True


def test_sms_save_blank_secret_keeps_existing(app):
    client = app.test_client()
    _auth(client)
    with app.app_context():
        from app.radius.db.repos import tenant_sms_settings_repo
        tenant_sms_settings_repo.upsert(tenant_id=1, api_key="ORIGINAL-KEY", sender="HOBE", enabled=True)
    # Re-save with a blank api_key (only changing the sender) keeps the secret.
    client.post("/admin/radius/sms/save", data={
        "_csrf_token": "sms-csrf", "auth_mode": "api_key", "api_key": "", "sender": "NEW", "enabled": "1",
    })
    with app.app_context():
        from app.radius.db.repos import tenant_sms_settings_repo
        cfg = tenant_sms_settings_repo.get(1)
        assert cfg["api_key"] == "ORIGINAL-KEY"
        assert cfg["sender"] == "NEW"


def test_subscriber_sms_fires_through_tweetsms_when_connected(app, monkeypatch):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo, tenant_sms_settings_repo
        from app.radius.services import tweetsms, notifications_engine as ne

        sub = subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="rami", password="pw", user_type="subscriber",
            full_name="رامي", mobile="0599123456", balance=5.0, status="enabled", expire_at=None,
        ))
        tenant_sms_settings_repo.upsert(tenant_id=1, api_key="K", sender="HOBE", enabled=True)

        calls = []

        def _fake_get(url, timeout=12.0):
            calls.append(url)
            return True, 200, "1:777:970599123456", ""

        monkeypatch.setattr(tweetsms, "_http_get", _fake_get)

        ne.save_rules(1, {
            "subscriber_created__enabled": "1",
            "subscriber_created__channels": ["sms"],
            "subscriber_created__template": "أهلًا {username}",
        })
        outcome = ne.notify_event("subscriber_created", tenant_id=1, subscriber=sub)
        assert outcome.sent.get("sms") is True
        assert len(calls) == 1
        assert "comm=sendsms" in calls[0]


def test_retired_quota_bundle_routes_are_gone(app):
    """The admin-sold message-bundle/quota UI + routes must be fully retired."""
    client = app.test_client()
    _auth(client)
    # Web quota pages → 404 (route removed).
    assert client.get("/admin/radius/communications/quota").status_code == 404
    # JSON API quota endpoint no longer serves GET (route removed).
    assert client.get("/api/v1/communications/quota",
                      headers={"Authorization": "Bearer x"}).status_code in (401, 404, 405)
    # The in-section nav bar no longer advertises «الرصيد والحِزم».
    html = client.get("/admin/radius/communications").get_data(as_text=True)
    assert "الرصيد والحِزم" not in html
    # The url endpoints themselves are unregistered.
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    assert "radius.communications_quota" not in rules
    assert "radius.communications_quota_request" not in rules
    assert "radius.communications_quota_credit" not in rules
