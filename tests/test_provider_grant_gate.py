"""feat/license-service-gate — اختبارات بوابة منح المزوّد.

تغطّي:
  * سلطة المزوّد فوق RBAC: السوبر-أدمن لا يتجاوز خدمة موقوفة.
  * «موقوفة» (services.<k>.status=disabled أو features.<k>=locked/hidden)
    → redirect لصفحة blocked عند GET + 403 عند الكتابة.
  * «مخفية من البوابة» (services.<k>.hidden_portal=true) → خدمة مرئية
    للإدارة لكن flag صحيح؛ لا تعطيل عام.
  * سقف الكميّ («مجانية محدودة») — limits.subscribers.max_total يمنع إنشاء
    مشترك إضافي حتى للسوبر.
  * fail-open عند عدم وجود لقطة (كل شيء مسموح).
  * إخفاء بنود الشريط الجانبي للخدمات الموقوفة.
  * صفحة blocked + صفحة status تعملان حتى عند إيقاف كل الخدمات.

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "provider_gate.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
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


def _seed_snapshot(tenant_id: int = 1, *,
                    services: dict | None = None,
                    features: dict | None = None,
                    limits: dict | None = None,
                    status: str = "active",
                    skip_license: bool = False) -> int:
    """يكتب لقطة capacity_contract + لقطة license نشطة (ضرورية الآن لأنّ
    حارس دورة حياة الترخيص يُقفل اللوحة بدون لقطة license)."""
    from app.radius.db.connection import db
    payload = {"status": status,
                "services": services or {},
                "features": features or {},
                "limits":   limits or {}}
    now = datetime.utcnow().isoformat() + "Z"
    cur = db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))
    if not skip_license:
        # لقطة ترخيص نشطة كي تتجاوز حارس دورة الحياة (تعتبر اللوحة active).
        db().execute(
            """INSERT INTO license_admin_bridge_snapshots
               (tenant_id, snapshot_type, normalized_status, source_url,
                payload_json, error_json, fetched_at, stale_after_seconds, created_at)
               VALUES (?, 'license', 'active', 'test://license',
                       ?, '{}', ?, 86400, ?)""",
            (int(tenant_id),
             json.dumps({"status": "active"}, ensure_ascii=False),
             now, now))
    return int(cur.lastrowid or 0)


def _seed_active_license_only(tenant_id: int = 1) -> None:
    """يضع فقط لقطة license نشطة (للاختبارات التي لا تحتاج capacity)."""
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'license', 'active', 'test://license',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id),
         json.dumps({"status": "active"}, ensure_ascii=False),
         now, now))


def _client(app, *, super_admin: bool = True):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=super_admin, tenant_id=1,
                 permissions=["*"])
    return c


# ════════════════════════════════════════════════════════════════════════
# (1) منطق خالص — ServiceGrant + lookup
# ════════════════════════════════════════════════════════════════════════
class TestPureLookup:

    def test_no_snapshot_means_allow(self, app_ctx):
        from app.radius.services import provider_grant
        # لا لقطة بعد → fail-open
        assert not provider_grant.is_service_disabled(1, "reports")
        assert not provider_grant.is_hidden_from_portal(1, "reports")
        assert provider_grant.get_limit(1, "subscribers.max_total") is None

    def test_service_status_disabled_blocks(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(services={"reports": {"enabled": True, "status": "disabled"}})
        assert provider_grant.is_service_disabled(1, "reports")
        # «مخفية من البوابة» تنتج تلقائيًّا من «موقوفة» (موقوف = مخفي ضمنًا)
        assert provider_grant.is_hidden_from_portal(1, "reports")

    def test_service_enabled_false_blocks(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(services={"cards": {"enabled": False, "status": "active"}})
        assert provider_grant.is_service_disabled(1, "cards")

    def test_feature_state_locked_blocks(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(features={"finance": "locked"})
        assert provider_grant.is_service_disabled(1, "finance")

    def test_feature_state_hidden_blocks(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(features={"network": "hidden"})
        assert provider_grant.is_service_disabled(1, "network")

    def test_hidden_portal_does_not_disable_admin(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(services={"store": {"enabled": True, "status": "active",
                                              "hidden_portal": True}})
        # «مخفية من البوابة» فقط: الإدارة تستعملها عاديًّا، الزبائن لا يرونها.
        assert not provider_grant.is_service_disabled(1, "store")
        assert provider_grant.is_hidden_from_portal(1, "store")

    def test_readonly_feature(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(features={"reports": "readonly"})
        assert provider_grant.is_readonly(1, "reports")
        assert not provider_grant.is_service_disabled(1, "reports")

    def test_list_all_grants_view(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(
            services={"reports": {"enabled": True, "status": "disabled"},
                        "store": {"enabled": True, "status": "active",
                                  "hidden_portal": True}},
            features={"finance": "readonly"})
        rows = provider_grant.list_all_grants(1)
        keys = {r["key"]: r for r in rows}
        assert keys["reports"]["disabled"] is True
        assert keys["store"]["hidden_from_portal_effective"] is True
        assert keys["finance"]["readonly"] is True


# ════════════════════════════════════════════════════════════════════════
# (2) endpoint → service_key mapping
# ════════════════════════════════════════════════════════════════════════
class TestEndpointMapping:

    def test_known_endpoint_maps(self):
        from app.radius.auth.provider_gate import service_key_for_endpoint
        assert service_key_for_endpoint("rep_login_attempts") == "reports"
        assert service_key_for_endpoint("users_list") == "subscribers"
        assert service_key_for_endpoint("cards_generate") == "cards"
        assert service_key_for_endpoint("finance_hub") == "finance"
        assert service_key_for_endpoint("mt_dashboard") == "network"

    def test_prefix_fallback(self):
        from app.radius.auth.provider_gate import service_key_for_endpoint
        # غير مُسجَّل صريحًا، يلتقطه prefix
        assert service_key_for_endpoint("rep_anything_new") == "reports"
        assert service_key_for_endpoint("mt_arbitrary_widget") == "network"

    def test_unknown_endpoint_returns_none(self):
        from app.radius.auth.provider_gate import service_key_for_endpoint
        # لا تعيين = لا حظر من البوابة (السماح هو الافتراضي)
        assert service_key_for_endpoint("dashboard") is None

    def test_blocked_check_uses_mapping(self, app_ctx):
        from app.radius.auth.provider_gate import is_endpoint_blocked_by_provider
        _seed_snapshot(services={"reports": {"enabled": True, "status": "disabled"}})
        blocked, key = is_endpoint_blocked_by_provider(1, "rep_login_attempts")
        assert blocked and key == "reports"
        # مفتاح غير محظور لا يتأثر
        blocked2, _ = is_endpoint_blocked_by_provider(1, "users_list")
        assert not blocked2


# ════════════════════════════════════════════════════════════════════════
# (3) حارس مسار blueprint — السوبر-أدمن لا يتجاوز
# ════════════════════════════════════════════════════════════════════════
class TestRouteGuardEvenForSuper:

    def test_super_admin_blocked_on_disabled_service_get(self, app_ctx):
        _seed_snapshot(services={"reports": {"enabled": True, "status": "disabled"}})
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/reports/login_states",
                          follow_redirects=False)
        assert rv.status_code in (302, 303)
        loc = rv.headers.get("Location", "")
        assert "/admin/radius/_provider/blocked" in loc
        assert "service=reports" in loc

    def test_super_admin_write_returns_403_on_disabled(self, app_ctx):
        _seed_snapshot(services={"cards": {"enabled": True, "status": "disabled"}})
        client = _client(app_ctx, super_admin=True)
        # GET first لتوليد _csrf_token في الجلسة (CSRF عام مبسَّط)
        client.get("/admin/radius/")
        with client.session_transaction() as s:
            token = s.get("_csrf_token") or ""
        rv = client.post("/admin/radius/cards/generate",
                          data={"plan_id": "1", "count": "5",
                                "_csrf_token": token})
        assert rv.status_code == 403

    def test_super_admin_passes_when_no_snapshot(self, app_ctx):
        # license نشط، لكن لا capacity → no provider grants → سماح كامل
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/reports/login_states",
                          follow_redirects=False)
        # لا لقطة → لا حظر
        assert rv.status_code in (200, 302)
        assert "/admin/radius/_provider/blocked" not in (rv.headers.get("Location") or "")

    def test_super_admin_passes_when_service_active(self, app_ctx):
        _seed_snapshot(services={"reports": {"enabled": True, "status": "active"}})
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/reports/login_states",
                          follow_redirects=False)
        assert rv.status_code in (200, 302)
        assert "/admin/radius/_provider/blocked" not in (rv.headers.get("Location") or "")

    def test_normal_admin_also_blocked(self, app_ctx):
        _seed_snapshot(services={"reports": {"enabled": True, "status": "disabled"}})
        client = _client(app_ctx, super_admin=False)
        rv = client.get("/admin/radius/reports/login_states",
                          follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert "/admin/radius/_provider/blocked" in rv.headers.get("Location", "")


# ════════════════════════════════════════════════════════════════════════
# (4) إخفاء بنود الشريط الجانبي
# ════════════════════════════════════════════════════════════════════════
class TestSidebarHide:

    def test_disabled_service_hides_item_from_super(self, app_ctx):
        _seed_snapshot(services={"reports": {"enabled": True, "status": "disabled"}})
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/")
        body = rv.get_data(as_text=True)
        # بند تقارير محذوف من الشريط — لا href يبدأ بـ/reports
        assert '/admin/radius/reports/login_states' not in body

    def test_active_service_still_shows(self, app_ctx):
        _seed_snapshot(services={"reports": {"enabled": True, "status": "active"}})
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/")
        body = rv.get_data(as_text=True)
        assert '/admin/radius/reports/' in body


# ════════════════════════════════════════════════════════════════════════
# (5) سقوف الكمّ
# ════════════════════════════════════════════════════════════════════════
class TestQuantityLimits:

    def test_subscribers_limit_enforced(self, app_ctx):
        # سقف صفر = لا يُسمح بإنشاء أي مشترك حتى للسوبر.
        _seed_snapshot(limits={"subscribers": {"max_total": 0}})
        from app.radius.services import provider_grant
        dec = provider_grant.check_limit(1, "subscribers", increment=1)
        assert not dec.allowed
        assert dec.reason == "provider_limit_exceeded"
        assert dec.limit == 0
        assert "الحدّ المسموح" in dec.message_ar

    def test_subscribers_limit_allows_within(self, app_ctx):
        _seed_snapshot(limits={"subscribers": {"max_total": 10}})
        from app.radius.services import provider_grant
        dec = provider_grant.check_limit(1, "subscribers", increment=1)
        assert dec.allowed
        assert dec.limit == 10

    def test_cards_per_batch_limit(self, app_ctx):
        _seed_snapshot(limits={"cards": {"generate_per_batch": 5}})
        from app.radius.services import provider_grant
        dec = provider_grant.check_limit(1, "cards_batch", increment=10)
        assert not dec.allowed

    def test_unknown_feature_key_allows(self, app_ctx):
        from app.radius.services import provider_grant
        dec = provider_grant.check_limit(1, "not_a_feature", increment=1)
        assert dec.allowed

    def test_no_limit_set_allows(self, app_ctx):
        _seed_snapshot(limits={})
        from app.radius.services import provider_grant
        dec = provider_grant.check_limit(1, "subscribers", increment=5000)
        assert dec.allowed


# ════════════════════════════════════════════════════════════════════════
# (6) صفحات بوابة المزوّد (blocked + status)
# ════════════════════════════════════════════════════════════════════════
class TestProviderPages:

    def test_blocked_page_renders(self, app_ctx):
        _seed_snapshot(services={"reports": {"enabled": True, "status": "disabled"}})
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/_provider/blocked?service=reports")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "الخدمة غير مفعّلة من المزوّد" in body
        assert "reports" in body

    def test_blocked_page_accessible_even_when_all_disabled(self, app_ctx):
        # كل الخدمات الرئيسية موقوفة — صفحة blocked يجب أن تبقى متاحة
        _seed_snapshot(services={
            "reports": {"enabled": True, "status": "disabled"},
            "subscribers": {"enabled": True, "status": "disabled"},
            "cards": {"enabled": True, "status": "disabled"},
            "finance": {"enabled": True, "status": "disabled"},
            "network": {"enabled": True, "status": "disabled"},
        })
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/_provider/blocked?service=reports")
        assert rv.status_code == 200

    def test_grants_status_page_renders(self, app_ctx):
        _seed_snapshot(services={"reports": {"enabled": True, "status": "disabled"},
                                   "store": {"enabled": True, "status": "active",
                                             "hidden_portal": True}})
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/_provider/grants")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "حالة منح المزوّد" in body
        assert "reports" in body
        assert "store" in body

    def test_grants_status_in_sidebar(self, app_ctx):
        # license نشط ضروري كي لا يقفل حارس دورة الحياة الـdashboard
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/")
        body = rv.get_data(as_text=True)
        assert "/admin/radius/_provider/grants" in body
        assert "حالة منح المزوّد" in body
