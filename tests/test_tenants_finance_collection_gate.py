"""«المستأجرون» (tenants) و«التحصيل» (finance_collection) — قدرتان provider
default-off، نفس معاملة «sections».

يُثبّت العقد:
  * الخريطة: capability_key_for_endpoint(tenants_*) == "tenants"؛
    (collection_hub + payment_collection_*) == "finance_collection".
  * حارس المسار: بلا منح → السوبر يُعاد توجيهه (GET) والكتابة 403؛
    مع منح صريح → 200. (السوبر لا يَتجاوز.)
  * الشريط الجانبي: بندا «المستأجرون» و«التحصيل» مخفيّان ما لم تُمنَح
    القدرة، ويَظهران بعد المنح.

شغّل هذا الملف وحده (عزل لكل ملف). يُحاكي نمط test_sections_capability_gate.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "tenants_fincol_cap.db")
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


def _seed_grant(*service_keys: str, tenant_id: int = 1) -> None:
    """يَمنح قدرات default-off المذكورة عبر لقطة عقد المزوّد (capacity_contract)
    + لقطة ترخيص نشطة كي لا تَحجب بوّابة دورة الحياة."""
    from app.radius.db.connection import db
    services = {k: {"enabled": True, "status": "active"} for k in service_keys}
    payload = {"status": "active", "services": services,
               "features": {}, "limits": {}}
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'license', 'active', 'test://license',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps({"status": "active"}), now, now))


def _seed_active_license_only(tenant_id: int = 1) -> None:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'license', 'active', 'test://license',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps({"status": "active"}), now, now))


def _client(app, *, super_admin: bool = True):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=super_admin, tenant_id=1, permissions=["*"])
    return c


# ════════════════════════════════════════════════════════════════════════
# (1) الخريطة
# ════════════════════════════════════════════════════════════════════════
class TestMapping:

    def test_tenants_endpoints_map_to_tenants_key(self):
        from app.radius.auth.provider_gate import capability_key_for_endpoint
        for ep in ("tenants_list", "tenants_new", "tenants_create",
                   "tenants_edit", "tenants_update", "radius.tenants_list"):
            assert capability_key_for_endpoint(ep) == "tenants"

    def test_collection_endpoints_map_to_finance_collection_key(self):
        from app.radius.auth.provider_gate import capability_key_for_endpoint
        for ep in ("collection_hub", "payment_collection_settings",
                   "payment_collection_requests", "payment_collection_review_queue_web",
                   "payment_collection_reconciliation_web",
                   "payment_collection_approve_web", "radius.collection_hub"):
            assert capability_key_for_endpoint(ep) == "finance_collection"

    def test_unrelated_endpoint_is_none(self):
        from app.radius.auth.provider_gate import capability_key_for_endpoint
        assert capability_key_for_endpoint("users_list") is None


# ════════════════════════════════════════════════════════════════════════
# (2) حارس المسار — السوبر لا يَتجاوز
# ════════════════════════════════════════════════════════════════════════
class TestRouteGuard:

    def test_tenants_super_redirected_when_off(self, app_ctx):
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/tenants", follow_redirects=False)
        assert rv.status_code in (302, 303)
        loc = rv.headers.get("Location", "")
        assert "/admin/radius/tenants" not in loc
        assert "/_provider/blocked" not in loc

    def test_collection_super_redirected_when_off(self, app_ctx):
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/finance/collection", follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert "/admin/radius/finance/collection" not in rv.headers.get("Location", "")

    def test_tenants_super_write_403_when_off(self, app_ctx):
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        client.get("/admin/radius/")
        with client.session_transaction() as s:
            token = s.get("_csrf_token") or ""
        rv = client.post("/admin/radius/tenants",
                         data={"name": "Acme", "_csrf_token": token})
        assert rv.status_code == 403

    def test_tenants_super_200_when_granted(self, app_ctx):
        _seed_grant("tenants")
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/tenants", follow_redirects=False)
        assert rv.status_code == 200

    def test_collection_super_200_when_granted(self, app_ctx):
        _seed_grant("finance_collection")
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/finance/collection", follow_redirects=False)
        assert rv.status_code == 200


# ════════════════════════════════════════════════════════════════════════
# (3) الشريط الجانبي
# ════════════════════════════════════════════════════════════════════════
class TestSidebar:

    def test_links_hidden_when_off(self, app_ctx):
        _seed_active_license_only()
        client = _client(app_ctx, super_admin=True)
        body = client.get("/admin/radius/").get_data(as_text=True)
        assert 'href="/admin/radius/tenants"' not in body
        assert "/admin/radius/finance/collection" not in body

    def test_tenants_link_shown_when_granted(self, app_ctx):
        _seed_grant("tenants")
        client = _client(app_ctx, super_admin=True)
        body = client.get("/admin/radius/").get_data(as_text=True)
        assert 'href="/admin/radius/tenants"' in body

    def test_collection_link_shown_when_granted(self, app_ctx):
        _seed_grant("finance_collection")
        client = _client(app_ctx, super_admin=True)
        body = client.get("/admin/radius/").get_data(as_text=True)
        assert "/admin/radius/finance/collection" in body
