"""اختبارات متجر المايكروتيك — التوكن، نقاط /api/v1/store/*، صفحة
store.html، والتكامل مع نشر مصمّم صفحة الدخول.

المتجر صفحة HTML واحدة تُرفع إلى ملفات الهوت سبوت على الراوتر
وتتخاطب مع الراديوس عبر النقاط العامة فقط (توكن itsdangerous موقّع
في الترويسة — لا كوكيز ولا توكن إدارة).
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_api_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_STORE_TOKEN_TTL", raising=False)
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


def _make_card_user(app, *, mobile="0590000001", password="secret123"):
    """ينشئ مستخدم بطاقة بمحفظة عبر الخدمة الحقيقية (نفس مسار الإدارة)."""
    with app.app_context():
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        svc = CardUsersMarketplaceService(tenant_id=1)
        user = svc.create_card_user(
            display_name="زبون اختبار",
            mobile=mobile,
            password=password,
        )
        return user


def _login(client, mobile="0590000001", password="secret123"):
    res = client.post("/api/v1/store/login",
                      json={"mobile": mobile, "password": password})
    return res


# ───────────────────────── التوكن الموقّع ─────────────────────────


def test_store_token_round_trip_and_tamper(app):
    with app.app_context():
        from app.radius.services.store_token import (
            StoreTokenError, issue_store_token, verify_store_token,
        )
        token = issue_store_token(card_user_id=42, tenant_id=1)
        ident = verify_store_token(token)
        assert ident == {"card_user_id": 42, "tenant_id": 1}
        # عبث بالتوقيع → رفض
        with pytest.raises(StoreTokenError) as exc:
            verify_store_token(token[:-2] + "xx")
        assert exc.value.code == "token_invalid"
        # توكن فارغ → رفض
        with pytest.raises(StoreTokenError) as exc:
            verify_store_token("")
        assert exc.value.code == "token_missing"


def test_store_token_expiry(app, monkeypatch):
    with app.app_context():
        from app.radius.services.store_token import (
            StoreTokenError, issue_store_token, verify_store_token,
        )
        token = issue_store_token(card_user_id=1, tenant_id=1)
        monkeypatch.setenv("HOBERADIUS_STORE_TOKEN_TTL", "1")
        time.sleep(2.1)  # دقة طوابع itsdangerous بالثواني
        with pytest.raises(StoreTokenError) as exc:
            verify_store_token(token)
        assert exc.value.code == "token_expired"


# ───────────────────────── الدخول والنقاط ─────────────────────────


def test_store_login_success_and_me(app, client):
    _make_card_user(app)
    res = _login(client)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["token"]
    assert data["card_user"]["display_name"] == "زبون اختبار"
    # لا تسريب لكلمة المرور أو الـ hash
    import json as _json
    assert "password" not in _json.dumps(res.get_json())

    headers = {"Authorization": "Bearer " + data["token"]}
    me = client.get("/api/v1/store/me", headers=headers)
    assert me.status_code == 200, me.get_json()
    payload = me.get_json()["data"]
    assert payload["wallet"]["balance"] == "0.00"
    assert payload["card_user"]["id"] == data["card_user"]["id"]
    assert isinstance(payload["packages"], list)
    assert isinstance(payload["purchases"], list)


def test_store_login_rejects_bad_password(app, client):
    _make_card_user(app)
    res = _login(client, password="wrong")
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "invalid_credentials"


def test_store_me_requires_token(client):
    res = client.get("/api/v1/store/me")
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "token_missing"
    # توكن معبوث
    res = client.get("/api/v1/store/me",
                     headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "token_invalid"


def test_store_cors_allows_router_origin(app, client):
    """صفحة المتجر أصلها IP الراوتر — النقاط تعيد ACAO: * دائمًا."""
    res = client.post("/api/v1/store/login", json={},
                      headers={"Origin": "http://192.168.88.1"})
    assert res.headers.get("Access-Control-Allow-Origin") == "*"
    pre = client.options("/api/v1/store/me",
                         headers={"Origin": "http://192.168.88.1"})
    assert pre.status_code == 204
    assert pre.headers.get("Access-Control-Allow-Origin") == "*"
    assert "Authorization" in pre.headers.get(
        "Access-Control-Allow-Headers", "")


def test_store_preflight_succeeds_on_all_endpoints(app, client):
    """preflight (OPTIONS) لكل نقاط المتجر ينجح بترويسات CORS كاملة
    قبل أي تحقق توكن — هذا ما يرسله المتصفح من أصل الراوتر قبل POST.

    يثبت أن السبب الجذري للـ405 (ارتطام الطلب بمسار /api/<path> العام
    المخصّص لـOPTIONS وحده عند غياب نقطة المتجر) لم يعد ممكنًا: كل
    نقطة تستجيب لـOPTIONS بنجاح وبالترويسات الصحيحة دون 401/405."""
    for path in ("/api/v1/store/login", "/api/v1/store/ping",
                 "/api/v1/store/me", "/api/v1/store/packages",
                 "/api/v1/store/redeem", "/api/v1/store/purchase",
                 "/api/v1/store/my-cards", "/api/v1/store/purchases"):
        pre = client.options(path, headers={
            "Origin": "http://192.168.88.1",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        # نجاح بلا محتوى — المتصفح يقبل 204 كـ200 تمامًا للـ preflight.
        assert pre.status_code in (200, 204), (path, pre.status_code)
        assert pre.headers.get("Access-Control-Allow-Origin") == "*", path
        allow_h = pre.headers.get("Access-Control-Allow-Headers", "")
        assert "Authorization" in allow_h, path
        allow_m = pre.headers.get("Access-Control-Allow-Methods", "")
        assert "OPTIONS" in allow_m and "POST" in allow_m, path


def test_store_preflight_allows_store_key_header(app, client):
    """preflight يسمح بترويسة X-Store-Key المخصّصة — وإلا حجب المتصفح
    كل نداء يحملها (نداءات store.html المنشورة وزر «اختبار الاتصال» في
    المصمّم بعد توليد المفتاح). الترويسة ليست من القائمة الآمنة فيُلزم
    المتصفح بـ preflight قبل إرسالها."""
    pre = client.options("/api/v1/store/ping", headers={
        "Origin": "http://192.168.88.1",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-store-key",
    })
    assert pre.status_code in (200, 204)
    allow_h = pre.headers.get("Access-Control-Allow-Headers", "")
    assert "X-Store-Key" in allow_h, allow_h


def test_store_post_login_without_preflight_works(app, client):
    """POST /login مباشرة (بلا OPTIONS سابق) يعمل ويحمل ACAO:* — أي
    عميل لا يرسل preflight (curl/راوتر) يصل للنقطة دون عائق CORS."""
    _make_card_user(app)
    res = client.post("/api/v1/store/login",
                      json={"mobile": "0590000001", "password": "secret123"},
                      headers={"Origin": "http://192.168.88.1"})
    assert res.status_code == 200, res.get_json()
    assert res.headers.get("Access-Control-Allow-Origin") == "*"
    assert res.get_json()["data"]["token"]


def test_store_purchase_insufficient_balance(app, client):
    """شراء برصيد صفر → 402 برمز insufficient_balance ورسالة عربية."""
    user = _make_card_user(app)
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.helpers import now_iso
        from app.radius.services.card_users_marketplace import (
            CardUsersMarketplaceService,
        )
        # خطة وصول + باقة سوق نشطة
        db().execute(
            "INSERT INTO access_plans(tenant_id, name, code, enabled, created_at) "
            "VALUES(1, 'خطة اختبار', 'tp1', 1, ?)", (now_iso(),))
        plan_id = int(db().execute(
            "SELECT id FROM access_plans WHERE tenant_id=1 AND code='tp1'"
        ).fetchone()["id"])
        CardUsersMarketplaceService(tenant_id=1).create_package(
            name="باقة اختبار", plan_id=plan_id, price="5.00",
        )
    token = _login(client).get_json()["data"]["token"]
    headers = {"Authorization": "Bearer " + token}
    pkgs = client.get("/api/v1/store/packages", headers=headers).get_json()
    assert pkgs["data"]["count"] >= 1
    pkg_id = pkgs["data"]["items"][0]["id"]
    res = client.post("/api/v1/store/purchase", headers=headers,
                      json={"package_id": pkg_id})
    assert res.status_code == 402, res.get_json()
    assert res.get_json()["error"]["code"] == "insufficient_balance"


def test_store_redeem_unknown_card(app, client):
    _make_card_user(app)
    token = _login(client).get_json()["data"]["token"]
    res = client.post("/api/v1/store/redeem",
                      headers={"Authorization": "Bearer " + token},
                      json={"card_number": "NOPE"})
    assert res.status_code == 422
    assert "غير موجود" in res.get_json()["error"]["message"]


# ───────────────────── مفتاح تطبيق المتجر (App Key) ─────────────────────


def _set_store_key(app, key="TESTKEY_abc-123"):
    """يضبط مفتاح المتجر للمستأجر 1 عبر مستودع الإعدادات الحقيقي."""
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.services.store_key import STORE_KEY_SETTING
        tenants_repo.set_setting(1, STORE_KEY_SETTING, key)
    return key


def test_store_key_service_round_trip(app):
    with app.app_context():
        from app.radius.services import store_key as sk
        # لا مفتاح بعد → الفرض متوقف، والتحقق يمرّ (توافق قديم)
        assert sk.get_store_key(1) == ""
        assert sk.store_key_required(1) is False
        assert sk.verify_store_key("", 1) is True
        # توليد عند الطلب ثم ثبات القيمة
        key = sk.get_or_create_store_key(1)
        assert key and sk.get_store_key(1) == key
        assert sk.get_or_create_store_key(1) == key  # لا يولّد ثانية
        assert sk.store_key_required(1) is True
        # التحقق: الصحيح يمر، الخطأ والفارغ يُرفضان
        assert sk.verify_store_key(key, 1) is True
        assert sk.verify_store_key("wrong", 1) is False
        assert sk.verify_store_key("", 1) is False
        # التدوير يبدّل القيمة ويُبطل القديمة
        new = sk.rotate_store_key(1)
        assert new != key and sk.get_store_key(1) == new
        assert sk.verify_store_key(key, 1) is False
        # التعقيم يزيل المحارف الخطرة
        assert sk.sanitize_key("abc';x//<>") == "abcx"


def test_store_key_gate_blocks_without_key(app, client):
    """عند ضبط مفتاح: أي طلب بلا X-Store-Key يُرفض 403 — حتى login."""
    _make_card_user(app)
    _set_store_key(app)
    res = client.post("/api/v1/store/login",
                      json={"mobile": "0590000001", "password": "secret123"})
    assert res.status_code == 403, res.get_json()
    assert res.get_json()["error"]["code"] == "store_key_invalid"
    # مفتاح خاطئ يُرفض أيضًا
    bad = client.post("/api/v1/store/login",
                      headers={"X-Store-Key": "nope"},
                      json={"mobile": "0590000001", "password": "secret123"})
    assert bad.status_code == 403
    # ping كذلك محمي (الفحص الذاتي يرسل المفتاح)
    assert client.get("/api/v1/store/ping").status_code == 403


def test_store_key_gate_allows_with_correct_key(app, client):
    """المفتاح الصحيح يمرّ البوّابة فيكمل المنطق الطبيعي (دخول ناجح)."""
    _make_card_user(app)
    key = _set_store_key(app)
    res = client.post("/api/v1/store/login",
                      headers={"X-Store-Key": key},
                      json={"mobile": "0590000001", "password": "secret123"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["token"]
    # ping بالمفتاح الصحيح ينجح
    assert client.get("/api/v1/store/ping",
                      headers={"X-Store-Key": key}).status_code == 200


def test_store_key_gate_preflight_not_blocked(app, client):
    """preflight (OPTIONS) لا يُفحص بالمفتاح — وإلا حُجب كل نداء متصفح.
    يبقى 204 + CORS حتى مع ضبط مفتاح وبلا ترويسة مفتاح."""
    _set_store_key(app)
    pre = client.options("/api/v1/store/login",
                         headers={"Origin": "http://192.168.88.1",
                                  "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204
    assert pre.headers.get("Access-Control-Allow-Origin") == "*"


def test_store_key_rotate_route_registered(app):
    """مسار تدوير المفتاح مسجّل (الزر في الإعدادات يستدعيه)."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/settings/store-key/rotate" in rules


# ───────────────────────── صفحة المتجر ─────────────────────────


def test_render_store_page_structure():
    from app.radius.services.hotspot_store_page import (
        StorePageError, render_store_page,
    )
    html = render_store_page(api_base="10.10.0.5",
                             tenant_name="شبكة الاختبار",
                             accent_color="#0D9488")
    # كل المتغيّرات استُبدلت
    assert "{{API_BASE}}" not in html
    assert "{{TENANT_NAME}}" not in html
    assert "{{STORE_KEY}}" not in html
    assert "var API = 'http://10.10.0.5';" in html
    # بلا مفتاح ممرَّر → سلسلة المفتاح فارغة (تثبيت قبل توليد مفتاح)
    assert "var SKEY = '';" in html
    # مفتاح ممرَّر → يُحقن حرفيًا في سلسلة JS (token_urlsafe آمن)
    keyed = render_store_page(api_base="10.10.0.5",
                              store_key="Ab3_Xy-9zKq")
    assert "var SKEY = 'Ab3_Xy-9zKq';" in keyed
    assert "{{STORE_KEY}}" not in keyed
    # محارف خطرة في المفتاح تُعقَّم قبل الحقن (لا كسر لسلسلة JS)
    dirty = render_store_page(api_base="10.10.0.5",
                              store_key="abc';alert(1)//")
    assert "var SKEY = 'abcalert1';" in dirty
    # هيكل الشاشات + نقاط الـ API + الجلسة
    for marker in ('id="scrLogin"', 'id="scrHome"', 'id="buyModal"',
                   "/api/v1/store", "sessionStorage", 'dir="rtl"',
                   "walled-garden", "login.html"):
        assert marker in html, marker
    # عنوان فاسد يُرفض برسالة عربية
    with pytest.raises(StorePageError):
        render_store_page(api_base="javascript:alert(1)")
    # شعار خبيث يسقط بصمت — لا يدخل الصفحة
    safe = render_store_page(api_base="1.2.3.4",
                             logo_url='"><script>x</script>')
    assert "<script>x</script>" not in safe


def test_deploy_store_file_add_and_set():
    from app.radius.services.hotspot_store_page import deploy_store

    class FakeClient:
        def __init__(self, existing):
            self.calls = []
            self.existing = existing

        def run(self, path, attrs=None):
            self.calls.append((path, attrs))
            if path == "/file/print":
                return ([{"name": "hotspot/store.html", ".id": "*7"}]
                        if self.existing else [])
            return []

    fresh = FakeClient(existing=False)
    res = deploy_store(fresh, api_base="10.0.0.9", tenant_name="شبكتي")
    assert res.ok and res.path == "hotspot/store.html" and res.bytes > 5000
    assert fresh.calls[0][0] == "/file/print"
    assert fresh.calls[1][0] == "/file/add"

    again = FakeClient(existing=True)
    res2 = deploy_store(again, api_base="10.0.0.9")
    assert res2.ok and again.calls[1][0] == "/file/set"
    assert again.calls[1][1][".id"] == "*7"

    # بلا عنوان راديوس → فشل مبكر بلا أي نداء للراوتر
    silent = FakeClient(existing=False)
    res3 = deploy_store(silent, api_base="")
    assert not res3.ok and not silent.calls
    assert "network.radius_server_ip" in res3.error


# ───────────────────────── تكامل المصمّم ─────────────────────────


def test_validate_vars_accepts_onrouter_store_filename(app):
    """STORE_URL يقبل القيمة الحرفية store.html (يحقنها مسار النشر)
    ويبقى يرفض أي رابط نسبي آخر."""
    with app.app_context():
        from app.radius.services import hotspot_templates as ht
        safe = ht.validate_vars({"STORE_URL": ht.STORE_ONROUTER_FILENAME})
        assert safe["STORE_URL"] == "store.html"
        # رابط نسبي عشوائي يمر عبر التطبيع الودّي http:// ثم الفحص —
        # «evil.html» يصبح http://evil.html (رابط مطلق صالح شكليًا)،
        # لكن قيمة فيها مسافة تُرفض.
        with pytest.raises(ValueError):
            ht.validate_vars({"STORE_URL": "bad value.html"})


def test_render_login_with_onrouter_store_url(app):
    """قالب «بوابة المتجر» يقبل STORE_URL=store.html ويضعه في href
    زر المتجر — هذا ما ينشره المصمّم عند تفعيل المتجر."""
    with app.app_context():
        from app.radius.services import hotspot_templates as ht
        html = ht.render("aurora_store", {
            "STORE_ENABLED": "yes",
            "STORE_URL": ht.STORE_ONROUTER_FILENAME,
        })
        assert 'href="store.html"' in html
        # عقد RouterOS سليم بعد الحقن
        assert not ht.validate_routeros_placeholders(
            ht.TEMPLATES_BY_SLUG["aurora_store"].html)


def test_resolve_store_api_base(app, monkeypatch):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.services import hotspot_templates as ht
        monkeypatch.delenv("HOBERADIUS_PUBLIC_IP", raising=False)
        tenants_repo.set_setting(1, "network.radius_server_ip", "10.20.30.40")
        assert ht.resolve_store_api_base(1) == "http://10.20.30.40"
        assert ht.resolve_store_url(1) == "http://10.20.30.40/portal/card"
        tenants_repo.set_setting(1, "network.radius_server_ip", "")


def test_store_routes_registered(app, client):
    """نقاط المتجر مسجلة تحت /api/v1/store/* في خريطة الروابط."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    for rule in ("/api/v1/store/login", "/api/v1/store/me",
                 "/api/v1/store/packages", "/api/v1/store/redeem",
                 "/api/v1/store/purchase"):
        assert rule in rules, rule
