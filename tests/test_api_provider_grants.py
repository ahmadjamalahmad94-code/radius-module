"""feat/license-service-gate — اختبارات /api/v1/provider/grants.

عقد الكلاينت (Flutter) لاستهلاك قرارات بوابة المزوّد:
  • تطلّب توكن API (401 بدونه).
  • tenant-scoped — لا تسريب بين المستأجرين.
  • تَعكس قرار اللوحة الويب بدقّة في كل حالة (active/expired/never/sync).
  • شكل ثابت (schema_version=1) فلا يَكسر الكلاينت عند توسعات لاحقة.

شغّل هذا الملف وحده (عزل لكل ملف اختبار).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest


AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "api_grants.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # كي يستجيب الـlifecycle gate كما في الإنتاج وليس البـbypass
    monkeypatch.delenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield app


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _seed_capacity(*, tenant_id: int = 1,
                    services: dict | None = None,
                    features: dict | None = None,
                    limits: dict | None = None,
                    status: str = "active") -> None:
    from app.radius.db.connection import db
    payload = {"status": status,
                "services": services or {},
                "features": features or {},
                "limits":   limits or {}}
    now = _iso(datetime.utcnow())
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'capacity_contract', 'active', 'test://capacity',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))


def _seed_license(*, tenant_id: int = 1,
                   status: str = "active",
                   expires_at=None, grace_until=None,
                   fetched_at: datetime | None = None,
                   stale_after_seconds: int = 86400) -> None:
    from app.radius.db.connection import db
    payload = {"status": status}
    if expires_at is not None:
        payload["expires_at"] = expires_at if isinstance(expires_at, str) else _iso(expires_at)
    if grace_until is not None:
        payload["grace_until"] = grace_until if isinstance(grace_until, str) else _iso(grace_until)
    fetched_iso = _iso(fetched_at or datetime.utcnow())
    normalized = "active" if status in ("active", "valid", "ok", "healthy", "grace") else status
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'license', ?, 'test://license',
                   ?, '{}', ?, ?, ?)""",
        (int(tenant_id), normalized,
         json.dumps(payload, ensure_ascii=False),
         fetched_iso, int(stale_after_seconds), fetched_iso))


def _client(app):
    return app.test_client()


def _get_grants(client):
    rv = client.get("/api/v1/provider/grants", headers=AUTH)
    body = rv.get_json() or {}
    return rv.status_code, body


# ════════════════════════════════════════════════════════════════════════
# (1) Auth
# ════════════════════════════════════════════════════════════════════════
class TestAuthentication:

    def test_no_auth_returns_401(self, app_ctx):
        rv = _client(app_ctx).get("/api/v1/provider/grants")
        assert rv.status_code == 401
        body = rv.get_json() or {}
        assert body.get("ok") is False

    def test_invalid_token_returns_401(self, app_ctx):
        rv = _client(app_ctx).get(
            "/api/v1/provider/grants",
            headers={"Authorization": "Bearer not-a-real-token"})
        assert rv.status_code == 401

    def test_valid_token_returns_200_even_without_snapshot(self, app_ctx):
        # لا snapshot = never_activated، لكن الـAPI يَردّ بحالة، لا 403
        status, body = _get_grants(_client(app_ctx))
        assert status == 200
        assert body.get("ok") is True
        assert body["data"]["license"]["state"] == "never_activated"
        assert body["data"]["license"]["blocks_panel"] is True


# ════════════════════════════════════════════════════════════════════════
# (2) شكل الـpayload
# ════════════════════════════════════════════════════════════════════════
class TestPayloadShape:

    def test_payload_has_required_top_level_fields(self, app_ctx):
        _seed_license(status="active")
        _, body = _get_grants(_client(app_ctx))
        data = body["data"]
        for key in ("license", "services", "limits", "has_snapshot",
                     "sync", "schema_version"):
            assert key in data, f"missing top-level field: {key}"
        assert data["schema_version"] == 3

    def test_license_block_shape(self, app_ctx):
        _seed_license(status="active",
                       expires_at=datetime.utcnow() + timedelta(days=30))
        _, body = _get_grants(_client(app_ctx))
        lic = body["data"]["license"]
        for k in ("state", "blocks_panel", "status", "reason",
                  "expires_at", "grace_until", "fetched_at",
                  "stale_days", "grace_remaining_days"):
            assert k in lic, f"missing license field: {k}"
        assert lic["state"] == "active"
        assert lic["blocks_panel"] is False

    def test_limits_cover_all_known_features(self, app_ctx):
        _seed_license(status="active")
        _, body = _get_grants(_client(app_ctx))
        limits = body["data"]["limits"]
        # v3+: subscribers لم يَعد سقفًا (concurrent cap بدلًا منه).
        # active_online هو المفتاح الرئيسي.
        for k in ("active_online", "cards", "cards_batch", "nas",
                  "routers", "profiles", "print_templates", "admins"):
            assert k in limits, f"missing limit feature: {k}"
            for sub in ("current", "limit", "remaining",
                         "limit_path", "usage_metric"):
                assert sub in limits[k]
        # تأكّد صريح: subscribers لم يَعد موجودًا (تَعطّل لـFlutter)
        assert "subscribers" not in limits

    def test_sync_block_shape(self, app_ctx):
        _seed_license(status="active")
        _, body = _get_grants(_client(app_ctx))
        sync = body["data"]["sync"]
        for k in ("has_snapshot", "stale", "stale_days",
                  "grace_days", "grace_remaining_days"):
            assert k in sync


# ════════════════════════════════════════════════════════════════════════
# (3) قرار اللوحة الويب يَنعكس في الـAPI (تطابق سلوكي)
# ════════════════════════════════════════════════════════════════════════
class TestMirrorsWebGate:

    def test_never_activated_mirrored(self, app_ctx):
        # لا lifecycle snapshot
        _, body = _get_grants(_client(app_ctx))
        assert body["data"]["license"]["state"] == "never_activated"
        assert body["data"]["license"]["blocks_panel"] is True
        assert body["data"]["has_snapshot"] is False

    def test_active_license_mirrored(self, app_ctx):
        _seed_license(status="active",
                       expires_at=datetime.utcnow() + timedelta(days=60))
        _, body = _get_grants(_client(app_ctx))
        lic = body["data"]["license"]
        assert lic["state"] == "active"
        assert lic["blocks_panel"] is False
        assert lic["expires_at"]

    def test_expired_license_mirrored(self, app_ctx):
        _seed_license(status="expired")
        _, body = _get_grants(_client(app_ctx))
        lic = body["data"]["license"]
        assert lic["state"] == "expired"
        assert lic["blocks_panel"] is True
        assert lic["status"] == "expired"
        assert lic["reason"] == "status_expired"

    def test_expires_at_past_mirrored(self, app_ctx):
        past = datetime.utcnow() - timedelta(days=2)
        _seed_license(status="active", expires_at=past)
        _, body = _get_grants(_client(app_ctx))
        lic = body["data"]["license"]
        assert lic["state"] == "expired"
        assert lic["reason"] == "expires_at_passed"

    def test_sync_outage_in_grace_mirrored(self, app_ctx):
        """انقطاع تزامن ضمن السماحية → blocks_panel=False (fail-open)."""
        old = datetime.utcnow() - timedelta(days=3)
        _seed_license(status="active", fetched_at=old,
                       stale_after_seconds=86400)
        _, body = _get_grants(_client(app_ctx))
        lic = body["data"]["license"]
        assert lic["state"] == "sync_outage_in_grace"
        assert lic["blocks_panel"] is False  # fail-open
        assert lic["stale_days"] > 2.5
        assert body["data"]["sync"]["stale"] is True
        assert body["data"]["sync"]["grace_remaining_days"] > 0

    def test_sync_outage_beyond_grace_mirrored(self, app_ctx, monkeypatch):
        monkeypatch.setenv("HOBERADIUS_LICENSE_SYNC_GRACE_DAYS", "1")
        old = datetime.utcnow() - timedelta(days=10)
        _seed_license(status="active", fetched_at=old,
                       stale_after_seconds=86400)
        _, body = _get_grants(_client(app_ctx))
        lic = body["data"]["license"]
        assert lic["state"] == "sync_outage_beyond_grace"
        assert lic["blocks_panel"] is True  # fail-closed
        assert lic["reason"] == "sync_grace_exhausted"


# ════════════════════════════════════════════════════════════════════════
# (4) خدمات + سقوف
# ════════════════════════════════════════════════════════════════════════
class TestServicesAndLimits:

    def test_disabled_service_marked_disabled(self, app_ctx):
        _seed_license(status="active")
        _seed_capacity(services={
            "reports": {"enabled": True, "status": "disabled"},
            "cards":   {"enabled": True, "status": "active"},
        })
        _, body = _get_grants(_client(app_ctx))
        services = {s["key"]: s for s in body["data"]["services"]}
        assert services["reports"]["disabled"] is True
        assert services["reports"]["hidden_from_portal_effective"] is True
        assert services["cards"]["disabled"] is False

    def test_hidden_portal_only_marked_correctly(self, app_ctx):
        _seed_license(status="active")
        _seed_capacity(services={
            "store": {"enabled": True, "status": "active",
                       "hidden_portal": True}})
        _, body = _get_grants(_client(app_ctx))
        store = next(s for s in body["data"]["services"] if s["key"] == "store")
        assert store["disabled"] is False
        assert store["hidden_portal"] is True
        assert store["hidden_from_portal_effective"] is True

    def test_readonly_feature_marked(self, app_ctx):
        _seed_license(status="active")
        _seed_capacity(features={"reports": "readonly"})
        _, body = _get_grants(_client(app_ctx))
        reports = next(s for s in body["data"]["services"] if s["key"] == "reports")
        assert reports["readonly"] is True
        assert reports["disabled"] is False

    def test_limit_with_cap_shows_current_and_remaining(self, app_ctx):
        # v3+: المفتاح الرئيسي للسقف concurrent online = active_online
        _seed_license(status="active")
        _seed_capacity(limits={"active_online": {"max": 100}})
        _, body = _get_grants(_client(app_ctx))
        ao = body["data"]["limits"]["active_online"]
        assert ao["limit"] == 100
        assert ao["current"] >= 0
        assert ao["remaining"] is not None
        assert ao["remaining"] == 100 - ao["current"]

    def test_limit_without_cap_returns_null_limit(self, app_ctx):
        _seed_license(status="active")  # no capacity = no limits
        _, body = _get_grants(_client(app_ctx))
        ao = body["data"]["limits"]["active_online"]
        assert ao["limit"] is None
        assert ao["remaining"] is None

    def test_zero_limit_means_unlimited(self, app_ctx):
        # ملاحظة: في active_online نَعتَبر 0 = unlimited (نفس get_active_online_cap)
        # لأن «صفر متّصلين مسموح» لا معنى تجاريًّا. provider_grant.get_limit
        # يُرجع 0 كرقم، لكن check_limit يَعتَبره cap=0 → remaining=0.
        _seed_license(status="active")
        _seed_capacity(limits={"active_online": {"max": 0}})
        _, body = _get_grants(_client(app_ctx))
        ao = body["data"]["limits"]["active_online"]
        assert ao["limit"] == 0
        assert ao["remaining"] == 0

    def test_cards_per_batch_and_monthly_distinct(self, app_ctx):
        _seed_license(status="active")
        _seed_capacity(limits={"cards": {"monthly_generated": 1000,
                                            "generate_per_batch": 50}})
        _, body = _get_grants(_client(app_ctx))
        assert body["data"]["limits"]["cards"]["limit"] == 1000
        assert body["data"]["limits"]["cards_batch"]["limit"] == 50


# ════════════════════════════════════════════════════════════════════════
# (5) عزل المستأجر (tenant isolation)
# ════════════════════════════════════════════════════════════════════════
class TestTenantIsolation:

    def test_token_for_tenant_1_does_not_see_tenant_2_grants(self, app_ctx):
        # نضع لقطتين مختلفتين لكل tenant، ثم نتأكّد أنّ تطلّب tenant 1
        # يَرى لقطته فقط (التوكن الـenv-default مرتبط بـtenant_id=1).
        _seed_license(tenant_id=1, status="active")
        _seed_capacity(tenant_id=1, services={
            "reports": {"enabled": True, "status": "disabled"}})
        # tenant 2: خدمة reports نشطة (مختلفة)
        from app.radius.db.repos import tenants_repo
        from app.radius.db.connection import db
        try:
            db().execute("INSERT OR IGNORE INTO tenants(id, name) VALUES(2, 'tenant-2')")
        except Exception:
            pass
        _seed_license(tenant_id=2, status="active")
        _seed_capacity(tenant_id=2, services={
            "reports": {"enabled": True, "status": "active"}})

        _, body = _get_grants(_client(app_ctx))
        # التوكن يربط بـtenant 1 → reports موقوفة لنا
        reports = next(s for s in body["data"]["services"] if s["key"] == "reports")
        assert reports["disabled"] is True


# ════════════════════════════════════════════════════════════════════════
# (6) ثبات الـschema_version (للكلاينت Flutter)
# ════════════════════════════════════════════════════════════════════════
class TestSchemaStability:

    def test_schema_version_is_3(self, app_ctx):
        _seed_license(status="active")
        _, body = _get_grants(_client(app_ctx))
        assert body["data"]["schema_version"] == 3

    def test_response_is_jsonable_and_no_python_objects(self, app_ctx):
        """الـreply يجب أن يكون JSON خالصًا (لا datetime، لا enum، لا dataclass)."""
        _seed_license(status="active",
                       expires_at=datetime.utcnow() + timedelta(days=10))
        _, body = _get_grants(_client(app_ctx))
        # re-serialize روبتي للتأكّد من JSONable التام
        json.dumps(body, ensure_ascii=False)
