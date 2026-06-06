"""اختبارات التسجيل الذاتي في المتجر — نقطة /api/v1/store/register
وخدمة register_card_user.

التسجيل ينشئ حساب مستخدم بطاقة **فعّالًا فورًا** بمحفظة (بلا تأكيد
إداري)، يدخل تلقائيًا (توكن)، ويقدر يشحن ويشتري مباشرة. الاختبارات
تغطي: النجاح + الدخول التلقائي، منع تكرار الجوال، فحص الصيغة، حدّ
كلمة المرور، الاسم الثلاثي، كبح المعدّل، وتهشيم كلمة المرور.
"""
from __future__ import annotations

import json as _json
import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_reg_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _register(client, *, name="محمد أحمد علي", mobile="0590000001",
              password="secret123"):
    return client.post("/api/v1/store/register", json={
        "display_name": name, "mobile": mobile, "password": password,
    })


# ───────────────────────── النجاح + الدخول التلقائي ─────────────────────────


def test_register_success_creates_active_user_and_auto_login(app, client):
    res = _register(client)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert data["token"]
    assert data["card_user"]["display_name"] == "محمد أحمد علي"
    assert data["card_user"]["mobile"] == "0590000001"
    assert data["card_user"]["status"] == "active"
    # لا تسريب لكلمة المرور أو الهاش في أي رد
    assert "password" not in _json.dumps(res.get_json())

    # الدخول التلقائي: التوكن المُعاد يعمل فورًا على /me ومعه محفظة فعّالة
    headers = {"Authorization": "Bearer " + data["token"]}
    me = client.get("/api/v1/store/me", headers=headers)
    assert me.status_code == 200, me.get_json()
    payload = me.get_json()["data"]
    assert payload["wallet"]["balance"] == "0.00"
    assert payload["card_user"]["id"] == data["card_user"]["id"]


def test_register_then_login_with_same_credentials(app, client):
    """الحساب يقدر يسجّل دخوله لاحقًا بنفس الاعتماد (كلمة مرور مهشّمة)."""
    _register(client, mobile="0591111111", password="mypass99")
    res = client.post("/api/v1/store/login",
                      json={"mobile": "0591111111", "password": "mypass99"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["token"]


def test_registered_user_password_is_hashed_not_plaintext(app, client):
    _register(client, mobile="0592222222", password="plain1234")
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT password_hash FROM card_users WHERE mobile=?",
            ("0592222222",),
        ).fetchone()
        h = str(row["password_hash"])
        assert h and h != "plain1234"
        assert h.startswith(("scrypt:", "pbkdf2:", "argon2:"))


# ───────────────────────── الحماية من الإساءة ─────────────────────────


def test_register_rejects_duplicate_mobile(app, client):
    first = _register(client, mobile="0593333333")
    assert first.status_code == 201
    dup = _register(client, name="شخص آخر تماما", mobile="0593333333")
    assert dup.status_code == 422
    assert dup.get_json()["error"]["code"] == "register_failed"


def test_register_rejects_invalid_mobile(app, client):
    res = _register(client, mobile="abc-not-a-phone")
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "register_failed"


def test_register_rejects_short_password(app, client):
    res = _register(client, mobile="0594444444", password="12")
    assert res.status_code == 422


def test_register_rejects_single_word_name(app, client):
    res = _register(client, name="محمد", mobile="0595555555")
    assert res.status_code == 422


def test_register_requires_all_fields(app, client):
    res = client.post("/api/v1/store/register",
                      json={"display_name": "", "mobile": "", "password": ""})
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "validation_error"


def test_register_rate_limited_after_threshold(app, client):
    # 5 محاولات مسموحة لكل IP في النافذة، ثم 429.
    for i in range(5):
        ok = _register(client, mobile="06000000" + str(10 + i))
        assert ok.status_code == 201, ok.get_json()
    blocked = _register(client, mobile="0699999999")
    assert blocked.status_code == 429
    assert blocked.get_json()["error"]["code"] == "rate_limited"


# ───────────────────────── الخدمة مباشرةً ─────────────────────────


def test_service_normalize_mobile():
    # خدمة خالصة — لا حاجة لـapp context.
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService as S,
    )
    assert S.normalize_mobile("05 9000-0001") == "0590000001"
    assert S.normalize_mobile("00967711111111") == "+967711111111"
    assert S.normalize_mobile("+967711111111") == "+967711111111"
    assert S.normalize_mobile("abc") == ""
    assert S.normalize_mobile("123") == ""  # قصير جدًا


def test_service_register_creates_wallet_and_event(app):
    with app.app_context():
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        svc = CardUsersMarketplaceService(tenant_id=1)
        user = svc.register_card_user(
            display_name="سعيد علي صالح", mobile="0598888888",
            password="pw1234",
        )
        assert user["status"] == "active"
        wallet = svc._wallet_for_card_user(user["id"])
        assert int(wallet["balance_minor"]) == 0
        assert svc.mobile_exists("0598888888") is True
