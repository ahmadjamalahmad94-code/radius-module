"""
Security Slice S-1 regression tests.

Covers:
  A. Dev fallback token gated by environment
     - works when HOBERADIUS_ENV is unset/dev
     - rejected when HOBERADIUS_ENV=production
  B. Token expiry enforcement
     - valid (non-expired) DB token passes
     - expired DB token → 401 token_expired
     - revoked DB token → 401 unauthorized
     - missing / unknown token → 401 unauthorized
  C. Login mints a token with the configured TTL.
"""
from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timedelta

import pytest


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_api_auth_")
    db_path = os.path.join(tmp, "test.db")
    old_db = os.environ.get("HOBERADIUS_DB_PATH")
    old_worker = os.environ.get("HOBERADIUS_NO_WORKER")
    old_seed = os.environ.get("HOBERADIUS_NO_SEED")
    os.environ["HOBERADIUS_DB_PATH"] = db_path
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ.pop("HOBERADIUS_NO_SEED", None)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_path)
    from app import create_app
    created = create_app()
    yield created
    if old_db is None:
        os.environ.pop("HOBERADIUS_DB_PATH", None)
        reset_for_tests(None)
    else:
        os.environ["HOBERADIUS_DB_PATH"] = old_db
        reset_for_tests(old_db)
    if old_worker is None:
        os.environ.pop("HOBERADIUS_NO_WORKER", None)
    else:
        os.environ["HOBERADIUS_NO_WORKER"] = old_worker
    if old_seed is None:
        os.environ.pop("HOBERADIUS_NO_SEED", None)
    else:
        os.environ["HOBERADIUS_NO_SEED"] = old_seed


@pytest.fixture
def client(app):
    return app.test_client()


def _admin_token(client) -> str:
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    return res.get_json()["data"]["token"]


# ─────────────── A. dev fallback gating ───────────────

def test_dev_fallback_works_in_dev_mode(client, monkeypatch):
    """With HOBERADIUS_ENV unset (default), the legacy dev token authenticates."""
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer dev-token-please-change"},
    )
    assert res.status_code == 200, res.get_json()


def test_dev_fallback_blocked_in_production(client, monkeypatch):
    """With HOBERADIUS_ENV=production, the dev fallback must not authenticate."""
    monkeypatch.setenv("HOBERADIUS_ENV", "production")
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer dev-token-please-change"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_env_tokens_still_work_in_production(client, monkeypatch):
    """Explicit HOBERADIUS_API_TOKENS env tokens still authenticate even in prod."""
    monkeypatch.setenv("HOBERADIUS_ENV", "production")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "explicit-prod-token-1234")
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer explicit-prod-token-1234"},
    )
    assert res.status_code == 200


def test_api_rate_limit_is_unlimited_unless_explicitly_enabled(monkeypatch):
    from app.api.auth import _configured_api_rpm

    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    assert _configured_api_rpm(tenant_rpm=10) == 0

    monkeypatch.setenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", "250")
    assert _configured_api_rpm(tenant_rpm=10) == 0

    monkeypatch.setenv("HOBERADIUS_API_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", "250")
    assert _configured_api_rpm(tenant_rpm=10) == 250

    monkeypatch.setenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", "0")
    assert _configured_api_rpm(tenant_rpm=10) == 0


# ─────────────── B. token expiry / validity ───────────────

def test_valid_login_token_authenticates(client):
    token = _admin_token(client)
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


def test_expired_token_rejected_with_token_expired_code(app, client):
    """Mint a token, manually set expires_at to the past, expect 401 token_expired."""
    from app.radius.db.connection import transaction
    from app.radius.db.repos import api_tokens_repo

    with app.app_context():
        record, plain = api_tokens_repo.create_token(
            tenant_id=1,
            name="qa_expired_token",
            scopes=["admin:full"],
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        # Backdate via direct UPDATE — simulates the clock advancing past TTL.
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                "UPDATE api_tokens SET expires_at = ? WHERE id = ?",
                (past, record["id"]),
            )

    try:
        res = client.get(
            "/api/v1/accounts",
            headers={"Authorization": f"Bearer {plain}"},
        )
        assert res.status_code == 401
        body = res.get_json()
        assert body["error"]["code"] == "token_expired"
    finally:
        with app.app_context():
            api_tokens_repo.revoke_token(1, record["id"])


def test_revoked_token_rejected(app, client):
    from app.radius.db.repos import api_tokens_repo

    with app.app_context():
        record, plain = api_tokens_repo.create_token(
            tenant_id=1,
            name="qa_revoked_token",
            scopes=["admin:full"],
        )
        api_tokens_repo.revoke_token(1, record["id"])

    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": f"Bearer {plain}"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_unknown_token_rejected(client):
    res = client.get(
        "/api/v1/accounts",
        headers={"Authorization": "Bearer some-token-that-does-not-exist-xyz"},
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_missing_token_rejected(client):
    res = client.get("/api/v1/accounts")
    assert res.status_code == 401


# ─────────────── D. اعتماد أدمن (HTTP Basic) كبديل للمفتاح ───────────────

def test_admin_basic_credentials_authenticate(client):
    """يوزر/باس الأدمن الصحيحان عبر Basic يُصادِقان أي نقطة إدارية — بديل المفتاح."""
    res = client.get("/api/v1/accounts", auth=("admin", "admin"))
    assert res.status_code == 200, res.get_json()


def test_admin_basic_wrong_password_rejected(client):
    res = client.get("/api/v1/accounts", auth=("admin", "wrong-password"))
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_admin_basic_unknown_user_rejected(client):
    res = client.get("/api/v1/accounts", auth=("ghost-admin", "whatever"))
    assert res.status_code == 401


def test_x_api_key_header_authenticates(client, monkeypatch):
    """ترويسة X-API-Key تُعامَل كمفتاح API تمامًا مثل Authorization Bearer."""
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "xapikey-token-9876")
    res = client.get(
        "/api/v1/accounts",
        headers={"X-API-Key": "xapikey-token-9876"},
    )
    assert res.status_code == 200, res.get_json()


def test_options_preflight_not_blocked_by_auth(client):
    """طلب OPTIONS (preflight) لا يُحجب بـ401 — يحسمه Flask قبل الديكوريتر."""
    res = client.options("/api/v1/accounts")
    assert res.status_code != 401


# ─────────────── E. الفرض المركزي التدريجي (يغطّي نقاط VPS/Flutter) ───────────────

_VPS_LIKE_PATH = "/api/v1/some-vps-endpoint-not-in-repo"


def test_unknown_api_path_open_when_enforcement_off(client, monkeypatch):
    """الفرض معطّل (الافتراضي): نقطة /api غير معرّفة هنا (تحاكي نقطة Flutter
    على الـVPS) لا يحرسها الحارس المركزي — تصل للتوجيه فتعطي 404 لا 401."""
    monkeypatch.delenv("HOBERADIUS_API_AUTH_REQUIRED", raising=False)
    res = client.get(_VPS_LIKE_PATH)
    # يصل للتوجيه (هنا 405 لارتطامه بـ catch-all الـOPTIONS العام) — المهم
    # أنه ليس 401: الحارس المركزي لم يحجبه.
    assert res.status_code != 401
    assert res.status_code in (404, 405)


def test_unknown_api_path_blocked_when_enforcement_on(client, monkeypatch):
    """الفرض مفعّل: أي نقطة /api بلا اعتماد → 401 قبل التوجيه، فيشمل نقاط
    Flutter غير المزخرفة تلقائيًا (مركزية بلا تعديل كل دالة)."""
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    res = client.get(_VPS_LIKE_PATH)
    assert res.status_code == 401


def test_enforcement_on_passes_with_valid_key(client, monkeypatch):
    """مع الفرض: مفتاح صحيح يمرّ. النقطة المعرّفة → 200، وغير المعرّفة →
    404 (مُصادَق لكن لا توجد) — أي أن المصادقة نجحت قبل التوجيه."""
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    hdr = {"Authorization": "Bearer dev-token-please-change"}
    assert client.get("/api/v1/accounts", headers=hdr).status_code == 200
    # مُصادَق بمفتاح صحيح → يمرّ الحارس ويصل للتوجيه (404/405) لا 401.
    assert client.get(_VPS_LIKE_PATH, headers=hdr).status_code != 401


def test_enforcement_on_passes_with_admin_basic(client, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    res = client.get("/api/v1/accounts", auth=("admin", "admin"))
    assert res.status_code == 200


def test_enforcement_on_keeps_public_paths_open(client, monkeypatch):
    """version/store تبقى مفتوحة حتى مع تفعيل الفرض (لها مصادقتها/عامة)."""
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    assert client.get("/api/v1/version").status_code == 200
    assert client.get("/api/v1/store/ping").status_code != 401


# ─────────────── F. تغطية مستقبلية: نقاط qa/phase-b-fixes تُحمى تلقائيًا ───────────────
#
# هذه مسارات **تمثيلية** لعائلات النقاط التي ستُدمج لاحقًا من فرع المستخدم
# وتستهلكها تطبيقات Flutter. الغرض إثبات أن الحارس المركزي يطابق بالمسار
# (/api/v1/*) لا باسم الدالة — فأي نقطة جديدة تحت /api تُحمى تلقائيًا بلا
# ديكوريتر يدوي. لا يهمّ أن المسار غير موجود الآن (404/405)؛ المهم سلوك
# الحارس: مفتوح عند التعطيل، يُحجب 401 عند التفعيل بلا اعتماد، يمرّ باعتماد.
# لاحقة فريدة `__phaseb_probe` تضمن ألا يصطدم المسار بأي نقطة موجودة الآن
# (لو اصطدم بنقطة مزخرفة لأعطى 401 من الديكوريتر بدل اختبار الحارس وحده).
_PROBE = "__phaseb_probe"
_FUTURE_API_PATHS = [
    f"/api/v1/communications/channels/quota/{_PROBE}",   # communications channel quota api
    f"/api/v1/subscriber-portal/me/{_PROBE}",            # subscriber portal api
    f"/api/v1/routers/service-status/{_PROBE}",          # router service status api
    f"/api/v1/setup-wizard/lifecycle/{_PROBE}",          # setup wizard lifecycle api
    f"/api/v1/setup-wizard/phase-planning/{_PROBE}",     # setup wizard phase planning api
    f"/api/v1/network-policy/runtime/{_PROBE}",          # network policy runtime api
    f"/api/v1/sessions/control/{_PROBE}",                # session control api
    f"/api/v1/payments/request-details/{_PROBE}",        # payment request details api
    f"/api/v1/operational-reports/summary/{_PROBE}",     # operational report api
    f"/api/v1/recharge-cards/{_PROBE}",                  # recharge cards api
]


@pytest.mark.parametrize("path", _FUTURE_API_PATHS)
def test_future_endpoint_open_when_enforcement_off(client, monkeypatch, path):
    monkeypatch.delenv("HOBERADIUS_API_AUTH_REQUIRED", raising=False)
    for call in (client.get, client.post):
        assert call(path).status_code != 401, path


@pytest.mark.parametrize("path", _FUTURE_API_PATHS)
def test_future_endpoint_blocked_when_enforcement_on(client, monkeypatch, path):
    """التغطية التلقائية: مع تفعيل الفرض، كل عائلة (GET وPOST) تُحجب 401 بلا
    اعتماد — حتى قبل أن توجد فعليًا في هذا الريبو."""
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    assert client.get(path).status_code == 401, path
    assert client.post(path).status_code == 401, path


@pytest.mark.parametrize("path", _FUTURE_API_PATHS)
def test_future_endpoint_passes_auth_with_admin_basic(client, monkeypatch, path):
    """مع الفرض واعتماد أدمن صحيح: يمرّ الحارس (النتيجة 404/405 لعدم وجود
    المسار بعد, لا 401) — أي أن المصادقة المركزية لا تعيق النقاط الشرعية."""
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    assert client.get(path, auth=("admin", "admin")).status_code != 401, path


# ─────────────── G. توكن Flutter (من /api/admin/login) يجتاز الفرض ───────────────

def test_flutter_login_token_passes_central_enforcement(client, monkeypatch):
    """العميل الحقيقي: يسجّل دخولًا عبر /api/admin/login بالاعتماد فيستلم
    token، ثم يرسله Bearer على كل نداء. هذا الاختبار يثبت أن نفس التوكن
    يجتاز الفرض المركزي **المفعّل** — فلن ينكسر تطبيق Flutter."""
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    # 1. /api/admin/login مستثنى من الحارس فيعمل رغم تفعيل الفرض
    login = client.post("/api/admin/login",
                        json={"username": "admin", "password": "admin"})
    assert login.status_code == 200, login.get_json()
    token = login.get_json()["data"]["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    # 2. نقطة محمية موجودة → 200 بالتوكن نفسه
    assert client.get("/api/v1/accounts", headers=hdr).status_code == 200
    # 3. نقطة /api مجهولة (تحاكي نقطة Flutter ستُدمج لاحقًا) → يجتاز الحارس (ليس 401)
    assert client.get("/api/v1/a-future-flutter-endpoint",
                      headers=hdr).status_code != 401


def test_flutter_login_token_blocked_without_header_when_enforced(client, monkeypatch):
    """للتباين: نفس النقطة بلا ترويسة Bearer → 401 عند تفعيل الفرض."""
    monkeypatch.setenv("HOBERADIUS_API_AUTH_REQUIRED", "1")
    assert client.get("/api/v1/accounts").status_code == 401


# ─────────────── C. login mints a token with TTL ───────────────

def test_login_returns_expires_at(client, monkeypatch):
    """The login response surfaces the new token's expires_at so Flutter can
    warn the user before the session lapses."""
    monkeypatch.setenv("HOBERADIUS_TOKEN_TTL_HOURS", "24")
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["expires_at"] is not None
    # Parse and confirm it's roughly 24h ahead (allow 5min clock skew)
    exp = datetime.fromisoformat(str(data["expires_at"]).replace("Z", ""))
    delta = exp - datetime.utcnow()
    assert timedelta(hours=23, minutes=55) < delta < timedelta(hours=24, minutes=5)


def test_admin_password_change_requires_login_token(client):
    res = client.post(
        "/api/admin/password",
        headers={"Authorization": "Bearer dev-token-please-change"},
        json={
            "current_password": "admin",
            "new_password": "new_admin_password_1",
            "confirm_password": "new_admin_password_1",
        },
    )
    assert res.status_code == 401
    assert res.get_json()["error"]["code"] == "unauthorized"


def test_admin_password_change_rejects_invalid_values(client):
    token = _admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    wrong_current = client.post(
        "/api/admin/password",
        headers=headers,
        json={
            "current_password": "wrong",
            "new_password": "new_admin_password_1",
            "confirm_password": "new_admin_password_1",
        },
    )
    assert wrong_current.status_code == 422
    assert wrong_current.get_json()["error"]["code"] == "invalid_current_password"

    short_password = client.post(
        "/api/admin/password",
        headers=headers,
        json={
            "current_password": "admin",
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert short_password.status_code == 422
    assert short_password.get_json()["error"]["code"] == "validation_error"

    mismatch = client.post(
        "/api/admin/password",
        headers=headers,
        json={
            "current_password": "admin",
            "new_password": "new_admin_password_1",
            "confirm_password": "new_admin_password_2",
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.get_json()["error"]["code"] == "validation_error"


def test_admin_password_change_allows_flutter_account_flow(app, client):
    from app.radius.db.repos import admins_repo

    username = f"qa_pwd_{int(time.time() * 1000)}"
    old_password = "old_admin_password_1"
    new_password = "new_admin_password_1"
    with app.app_context():
        admin = admins_repo.create_admin(
            username=username,
            password=old_password,
            full_name="QA Password Admin",
            email=f"{username}@example.test",
        )

    try:
        login = client.post(
            "/api/admin/login",
            json={"username": username, "password": old_password},
        )
        assert login.status_code == 200, login.get_json()
        token = login.get_json()["data"]["token"]

        changed = client.post(
            "/api/admin/password",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "current_password": old_password,
                "new_password": new_password,
                "confirm_password": new_password,
            },
        )
        assert changed.status_code == 200, changed.get_json()
        data = changed.get_json()["data"]
        assert data["updated"] is True
        assert data["source"] == "local"

        old_login = client.post(
            "/api/admin/login",
            json={"username": username, "password": old_password},
        )
        assert old_login.status_code == 401

        new_login = client.post(
            "/api/admin/login",
            json={"username": username, "password": new_password},
        )
        assert new_login.status_code == 200, new_login.get_json()
    finally:
        with app.app_context():
            admins_repo.archive_admin(int(admin.id or 0), actor="test")
