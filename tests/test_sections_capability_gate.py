"""customer-panel cleanup — «إدارة أقسام الواجهة» قدرة default-off + دمج التواصل.

يغطّي:
  * منطق خالص: provider_grant.is_capability_granted — default-OFF (الغياب =
    غير ممنوح)، يُمنَح فقط بـ services.<k> enabled+active أو features.<k>=enabled،
    ويُحجَب على disabled / requires_upgrade.
  * خريطة provider_gate: capability_key_for_endpoint + is_endpoint_capability_granted
    (نقاط sections_admin_* → «sections»؛ غيرها → (True, "")).
  * حارس المسار: /sections مُطفأة افتراضيًّا (لا لقطة قدرة) → السوبر يُعاد
    توجيهه (GET) والكتابة 403؛ ومع منح «sections» صراحةً → 200.
  * الشريط الجانبي: بند «إدارة الأقسام» مخفيّ ما لم تُمنَح القدرة، يَظهر بعدها.
  * دمج «التواصل والحملات» داخل «الإشعارات والتواصل»: رابط communications
    موجود مرّة واحدة (لا تكرار) بعد نقله.

شغّل هذا الملف وحده (عزل لكل ملف).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sections_cap.db")
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
                    skip_license: bool = False) -> None:
    from app.radius.db.connection import db
    payload = {"status": "active",
                "services": services or {},
                "features": features or {},
                "limits": {}}
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))
    if not skip_license:
        db().execute(
            """INSERT INTO license_admin_bridge_snapshots
               (tenant_id, snapshot_type, normalized_status, source_url,
                payload_json, error_json, fetched_at, stale_after_seconds, created_at)
               VALUES (?, 'license', 'active', 'test://license',
                       ?, '{}', ?, 86400, ?)""",
            (int(tenant_id),
             json.dumps({"status": "active"}, ensure_ascii=False), now, now))


def _seed_active_license_only(tenant_id: int = 1) -> None:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'license', 'active', 'test://license',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id),
         json.dumps({"status": "active"}, ensure_ascii=False), now, now))


def _client(app, *, super_admin: bool = True):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=super_admin, tenant_id=1, permissions=["*"])
    return c


# ════════════════════════════════════════════════════════════════════════
# (1) منطق خالص — is_capability_granted (default-OFF)
# ════════════════════════════════════════════════════════════════════════
class TestCapabilityGrantedPure:

    def test_no_snapshot_means_not_granted(self, app_ctx):
        from app.radius.services import provider_grant
        # عكس is_service_disabled (fail-open): الغياب = غير ممنوح.
        assert not provider_grant.is_capability_granted(1, "sections")
        # ومع ذلك لا يُعدّ «موقوفًا» بمعنى الحظر العامّ.
        assert not provider_grant.is_service_disabled(1, "sections")

    def test_service_enabled_active_grants(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(services={"sections": {"enabled": True, "status": "active"}})
        assert provider_grant.is_capability_granted(1, "sections")

    def test_feature_enabled_grants(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(features={"sections": "enabled"})
        assert provider_grant.is_capability_granted(1, "sections")

    def test_disabled_not_granted(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(services={"sections": {"enabled": False, "status": "active"}})
        assert not provider_grant.is_capability_granted(1, "sections")

    def test_requires_upgrade_not_granted(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_snapshot(services={"sections": {"enabled": True,
                                                "status": "locked_upgrade"}})
        assert not provider_grant.is_capability_granted(1, "sections")


# ════════════════════════════════════════════════════════════════════════
# (2) خريطة provider_gate
# ════════════════════════════════════════════════════════════════════════
class TestCapabilityMapping:

    def test_sections_endpoints_map_to_sections_key(self):
        from app.radius.auth.provider_gate import capability_key_for_endpoint
        for ep in ("sections_admin_page", "sections_admin_save",
                   "sections_admin_reset", "radius.sections_admin_page"):
            assert capability_key_for_endpoint(ep) == "sections"

    def test_non_capability_endpoint_is_none(self):
        from app.radius.auth.provider_gate import capability_key_for_endpoint
        assert capability_key_for_endpoint("users_list") is None
        assert capability_key_for_endpoint("reports_home") is None

    def test_endpoint_capability_granted_default_off(self, app_ctx):
        from app.radius.auth.provider_gate import is_endpoint_capability_granted
        # لا لقطة → النقطة المحروسة غير ممنوحة، والمفتاح يُرجَع للتشخيص.
        granted, key = is_endpoint_capability_granted(1, "sections_admin_page")
        assert not granted and key == "sections"
        # نقطة غير محروسة بقدرة default-off → دائمًا (True, "").
        granted2, key2 = is_endpoint_capability_granted(1, "users_list")
        assert granted2 and key2 == ""

    def test_endpoint_capability_granted_when_provider_grants(self, app_ctx):
        from app.radius.auth.provider_gate import is_endpoint_capability_granted
        _seed_snapshot(services={"sections": {"enabled": True, "status": "active"}})
        granted, key = is_endpoint_capability_granted(1, "sections_admin_page")
        assert granted and key == "sections"


# ════════════════════════════════════════════════════════════════════════
# (3) حارس المسار — default-off
# ════════════════════════════════════════════════════════════════════════
class TestRouteGuard:

    def test_super_get_redirected_when_capability_off(self, app_ctx):
        # ترخيص نشط لكن لا منح «sections» → مُطفأة افتراضيًّا، حتى للسوبر.
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/sections", follow_redirects=False)
        assert rv.status_code in (302, 303)
        loc = rv.headers.get("Location", "")
        # إعادة توجيه للوحة التحكّم — لا صفحة الـsections ولا صفحة blocked.
        assert "/admin/radius/sections" not in loc
        assert "/_provider/blocked" not in loc

    def test_super_write_403_when_capability_off(self, app_ctx):
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        # GET أولًا لتوليد _csrf_token في الجلسة (CSRF عام مبسَّط) كي لا
        # يَعترض حارس الـCSRF قبل بلوغ حارس القدرة.
        client.get("/admin/radius/")
        with client.session_transaction() as s:
            token = s.get("_csrf_token") or ""
        rv = client.post("/admin/radius/sections/save",
                          data={"section": "reports", "hidden": "1",
                                "_csrf_token": token})
        assert rv.status_code == 403

    def test_super_get_200_when_capability_granted(self, app_ctx):
        _seed_snapshot(services={"sections": {"enabled": True, "status": "active"}})
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/sections", follow_redirects=False)
        assert rv.status_code == 200


# ════════════════════════════════════════════════════════════════════════
# (4) الشريط الجانبي — إخفاء/إظهار بند «إدارة الأقسام»
# ════════════════════════════════════════════════════════════════════════
class TestSidebarSectionsEntry:

    def test_hidden_when_capability_off(self, app_ctx):
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        body = client.get("/admin/radius/").get_data(as_text=True)
        assert "/admin/radius/sections" not in body
        assert "إدارة الأقسام (إخفاء/تعطيل)" not in body

    def test_shown_when_capability_granted(self, app_ctx):
        _seed_snapshot(services={"sections": {"enabled": True, "status": "active"}})
        client = _client(app_ctx, super_admin=True)
        body = client.get("/admin/radius/").get_data(as_text=True)
        assert "/admin/radius/sections" in body
        assert "إدارة الأقسام (إخفاء/تعطيل)" in body


# ════════════════════════════════════════════════════════════════════════
# (5) دمج «التواصل والحملات» داخل «الإشعارات والتواصل»
# ════════════════════════════════════════════════════════════════════════
class TestCommunicationsMerge:

    def test_communications_link_present_exactly_once(self, app_ctx):
        # ترخيص نشط فقط (لا قيود قدرة) — communications ليست default-off.
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        body = client.get("/admin/radius/").get_data(as_text=True)
        # رابط «التواصل والحملات» موجود ومرّة واحدة (لا تكرار بعد النقل).
        assert 'href="/admin/radius/communications"' in body
        assert body.count('href="/admin/radius/communications"') == 1
