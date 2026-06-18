"""feat/activation-link-flow — اختبارات الربط/التفعيل/فكّ الربط.

يَغطّي:
  • النموذج البسيط: validate URL + license_key
  • Link الناجح: يَحفظ config + يَجلب أوّل لقطة + يَفتح اللوحة
  • Link الفاشل: رسالة عربية واضحة، الإعدادات تَبقى محفوظة
  • sync_now: يَنفّذ بدون لمس الإعدادات
  • reset: يَمسح config + snapshots → نَرجع لـnever_activated
  • الدورة الكاملة: pending → link → active → reset → pending → link → active
  • الصفحة + الـactions متاحة حتى لو كانت اللوحة مقفلة (lifecycle skip)
  • CTA «ربط وتفعيل النسخة» يَظهر في شاشة activate المقفلة
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import patch

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "connect.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # نَكشف الـlifecycle gate الحقيقي
    monkeypatch.delenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", raising=False)
    # نَنظِّف أيّ env-override للجسر كي تَأتي الإعدادات من DB حصرًا
    for name in ("HOBERADIUS_ADMIN_BASE_URL", "HOBERADIUS_LICENSE_KEY",
                  "INSTANCE_LICENSE_KEY", "HOBERADIUS_ADMIN_BRIDGE_ENABLED"):
        monkeypatch.delenv(name, raising=False)
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


def _client(app, super_admin: bool = True):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=super_admin, tenant_id=1, permissions=["*"])
    return c


def _csrf(client):
    """احصل على token الـCSRF بعد GET أوّل (نمط custom CSRF check)."""
    client.get("/admin/radius/_license/connect")
    with client.session_transaction() as s:
        return s.get("_csrf_token") or ""


# Mock يَحاكي رحلة AdminPanelClient الكاملة (يَكتب لقطات DB كما تَفعل
# النسخة الحقيقية). يَتحكّم بالنجاح/الفشل عبر الحقن.
class _FakeClient:
    def __init__(self, *, license_ok=True, capacity_ok=True,
                 license_status="active", expires_at=None,
                 services=None, raises=False):
        self.license_ok = license_ok
        self.capacity_ok = capacity_ok
        self.license_status = license_status
        self.expires_at = expires_at
        self.services = services or {"reports": {"enabled": True, "status": "active"}}
        self.raises = raises
        self.calls = []

    def _save(self, tenant_id, snapshot_type, payload, normalized):
        from app.radius.db.connection import db
        now = _iso(datetime.utcnow())
        db().execute(
            """INSERT INTO license_admin_bridge_snapshots
               (tenant_id, snapshot_type, normalized_status, source_url,
                payload_json, error_json, fetched_at, stale_after_seconds, created_at)
               VALUES (?, ?, ?, 'test://provider', ?, '{}', ?, 86400, ?)""",
            (int(tenant_id), snapshot_type, normalized,
             json.dumps(payload, ensure_ascii=False), now, now))

    def fetch_license_snapshot(self, *, tenant_id):
        self.calls.append(("license", tenant_id))
        if self.raises:
            raise RuntimeError("simulated network failure")
        if not self.license_ok:
            return {"ok": False, "status": "fingerprint_denied",
                    "error": {"code": "fp_mismatch", "message": "bad key"}}
        payload = {"status": self.license_status}
        if self.expires_at:
            payload["expires_at"] = self.expires_at
        self._save(tenant_id, "license",
                    payload,
                    self.license_status if self.license_status in
                    ("active", "valid", "grace", "ok", "healthy") else self.license_status)
        return {"ok": True, "status": "active"}

    def fetch_capacity_contract(self, *, tenant_id):
        self.calls.append(("capacity", tenant_id))
        if self.raises:
            raise RuntimeError("simulated network failure")
        if not self.capacity_ok:
            return {"ok": False, "status": "config_missing",
                    "error": {"code": "bad_config"}}
        self._save(tenant_id, "capacity_contract",
                    {"status": "active", "services": self.services},
                    "active")
        return {"ok": True, "status": "active"}


# ════════════════════════════════════════════════════════════════════════
# (1) Validation
# ════════════════════════════════════════════════════════════════════════
class TestValidation:

    def test_empty_url_rejected(self, app_ctx):
        from app.radius.services.activation_connect import link_and_activate
        r = link_and_activate(1, base_url="", license_key="some-real-key-123456")
        assert not r.ok
        assert r.code == "validation_url"
        assert "رابط" in r.message_ar

    def test_url_must_be_http_or_https(self, app_ctx):
        from app.radius.services.activation_connect import link_and_activate
        r = link_and_activate(1, base_url="ftp://provider.example",
                                license_key="some-real-key-123456")
        assert not r.ok
        assert r.code == "validation_url"

    def test_url_with_spaces_rejected(self, app_ctx):
        from app.radius.services.activation_connect import link_and_activate
        r = link_and_activate(1, base_url="https://prov ider.com",
                                license_key="some-real-key-123456")
        assert not r.ok and r.code == "validation_url"

    def test_short_license_key_rejected(self, app_ctx):
        from app.radius.services.activation_connect import link_and_activate
        r = link_and_activate(1, base_url="https://hoberadius.com",
                                license_key="abc")
        assert not r.ok
        assert r.code == "validation_license_key"

    def test_very_long_license_key_rejected(self, app_ctx):
        from app.radius.services.activation_connect import link_and_activate
        r = link_and_activate(1, base_url="https://hoberadius.com",
                                license_key="x" * 1000)
        assert not r.ok and r.code == "validation_license_key"


# ════════════════════════════════════════════════════════════════════════
# (2) Link succeeds + persists config + fetches snapshots
# ════════════════════════════════════════════════════════════════════════
class TestLinkSuccess:

    def test_link_saves_config_and_fetches_snapshots(self, app_ctx):
        from app.radius.services import activation_connect as ac
        fake = _FakeClient(license_ok=True, capacity_ok=True)
        with patch("app.radius.services.activation_connect.AdminPanelClient"
                    if False else "app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=fake):
            r = ac.link_and_activate(1, base_url="https://hoberadius.com",
                                       license_key="test-license-key-12345")
        assert r.ok
        assert r.code == "activated"
        # config محفوظ
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, "license_admin_bridge.base_url", "") == "https://hoberadius.com"
        assert tenants_repo.get_setting(1, "license_admin_bridge.license_key", "") == "test-license-key-12345"
        assert tenants_repo.get_setting(1, "license_admin_bridge.enabled", "") == "1"
        # كلا اللقطتين كُتبت
        assert any(c[0] == "license" for c in fake.calls)
        assert any(c[0] == "capacity" for c in fake.calls)
        # lifecycle.evaluate → active بعد الـlink
        from app.radius.services.license_lifecycle import evaluate, LifecycleState
        assert evaluate(1).state == LifecycleState.ACTIVE

    def test_link_trims_trailing_slash_from_url(self, app_ctx):
        from app.radius.services import activation_connect as ac
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            ac.link_and_activate(1, base_url="https://hoberadius.com//  ",
                                  license_key="test-license-key-12345")
        from app.radius.db.repos import tenants_repo
        saved = tenants_repo.get_setting(1, "license_admin_bridge.base_url", "")
        assert saved == "https://hoberadius.com"


# ════════════════════════════════════════════════════════════════════════
# (3) Link partial / failure modes
# ════════════════════════════════════════════════════════════════════════
class TestLinkFailures:

    def test_link_capacity_ok_license_fails_is_partial_success(self, app_ctx):
        """capacity وحدها = ربط جزئي (license-block المضمَّن قد يَكفي)."""
        from app.radius.services import activation_connect as ac
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient(license_ok=False, capacity_ok=True)):
            r = ac.link_and_activate(1, base_url="https://hoberadius.com",
                                       license_key="test-license-key-12345")
        # نَجاح جزئي
        assert r.ok
        assert "جزئيًّا" in r.message_ar or "تمّ الربط" in r.message_ar

    def test_link_both_fail_keeps_config_for_retry(self, app_ctx):
        from app.radius.services import activation_connect as ac
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient(license_ok=False, capacity_ok=False)):
            r = ac.link_and_activate(1, base_url="https://hoberadius.com",
                                       license_key="test-license-key-12345")
        assert not r.ok
        assert r.code == "sync_failed"
        # رغم الفشل، الإعدادات محفوظة كي يَستطيع المستخدم «إعادة المحاولة»
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, "license_admin_bridge.base_url", "") == "https://hoberadius.com"
        assert tenants_repo.get_setting(1, "license_admin_bridge.license_key", "") == "test-license-key-12345"

    def test_link_network_exception_arabic_message(self, app_ctx):
        from app.radius.services import activation_connect as ac
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient(raises=True)):
            r = ac.link_and_activate(1, base_url="https://hoberadius.com",
                                       license_key="test-license-key-12345")
        assert not r.ok
        assert r.code == "sync_exception"
        assert "تعذّر الاتصال" in r.message_ar


# ════════════════════════════════════════════════════════════════════════
# (4) sync_now بعد ربط محفوظ
# ════════════════════════════════════════════════════════════════════════
class TestSyncNow:

    def test_sync_now_fetches_without_touching_config(self, app_ctx):
        from app.radius.services import activation_connect as ac
        from app.radius.db.repos import tenants_repo
        # نَضع config محفوظ يدويًّا
        tenants_repo.set_setting(1, "license_admin_bridge.base_url", "https://x.test")
        tenants_repo.set_setting(1, "license_admin_bridge.license_key", "kkkkkkkkkk")
        fake = _FakeClient()
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=fake):
            r = ac.sync_now(1)
        assert r.ok
        # config لم يَتغيّر
        assert tenants_repo.get_setting(1, "license_admin_bridge.base_url", "") == "https://x.test"


# ════════════════════════════════════════════════════════════════════════
# (5) Reset (فكّ الربط) — يَرجع للحالة pending
# ════════════════════════════════════════════════════════════════════════
class TestReset:

    def test_reset_clears_settings_and_snapshots(self, app_ctx):
        from app.radius.services import activation_connect as ac
        from app.radius.services.license_lifecycle import evaluate, LifecycleState
        # ربط أوّلاً
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            r = ac.link_and_activate(1, base_url="https://hoberadius.com",
                                       license_key="test-license-key-12345")
        assert r.ok
        assert evaluate(1).state == LifecycleState.ACTIVE

        # reset
        rr = ac.reset_link(1)
        assert rr.ok
        assert rr.code == "reset_done"
        # الإعدادات مُسحت
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, "license_admin_bridge.base_url", "") == ""
        assert tenants_repo.get_setting(1, "license_admin_bridge.license_key", "") == ""
        # اللقطات حُذفت
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS n FROM license_admin_bridge_snapshots WHERE tenant_id = ?",
            (1,)).fetchone()
        assert int(row["n"]) == 0
        # lifecycle → never_activated
        assert evaluate(1).state == LifecycleState.NEVER_ACTIVATED

    def test_reset_with_no_prior_link_is_safe_noop(self, app_ctx):
        from app.radius.services import activation_connect as ac
        # لا config ولا snapshots — reset لا يَرفع خطأ
        r = ac.reset_link(1)
        assert r.ok


# ════════════════════════════════════════════════════════════════════════
# (6) دورة كاملة: pending → link → active → reset → pending → link → active
# ════════════════════════════════════════════════════════════════════════
class TestFullCycle:

    def test_full_remove_pending_reactivate_cycle(self, app_ctx):
        from app.radius.services import activation_connect as ac
        from app.radius.services.license_lifecycle import evaluate, LifecycleState

        # (1) pending
        assert evaluate(1).state == LifecycleState.NEVER_ACTIVATED

        # (2) link → active
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            r1 = ac.link_and_activate(1, base_url="https://hoberadius.com",
                                        license_key="key-cycle-aaaa")
        assert r1.ok
        assert evaluate(1).state == LifecycleState.ACTIVE

        # (3) reset → pending
        ac.reset_link(1)
        assert evaluate(1).state == LifecycleState.NEVER_ACTIVATED

        # (4) re-link → active (مفتاح مختلف لإثبات استقلال الدورة)
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            r2 = ac.link_and_activate(1, base_url="https://hoberadius.com",
                                        license_key="key-cycle-bbbb-new")
        assert r2.ok
        assert evaluate(1).state == LifecycleState.ACTIVE
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, "license_admin_bridge.license_key", "") == "key-cycle-bbbb-new"


# ════════════════════════════════════════════════════════════════════════
# (7) Activation state (للعرض في الصفحة)
# ════════════════════════════════════════════════════════════════════════
class TestActivationState:

    def test_state_pending_when_fresh(self, app_ctx):
        from app.radius.services.activation_connect import activation_state
        s = activation_state(1)
        assert s["phase"] == "pending"
        assert s["phase_ar"] == "بانتظار التفعيل"
        assert s["blocks_panel"] is True
        assert s["has_config"] is False
        assert s["has_snapshot"] is False

    def test_state_active_after_link(self, app_ctx):
        from app.radius.services.activation_connect import (
            link_and_activate, activation_state)
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            link_and_activate(1, base_url="https://hoberadius.com",
                                license_key="test-license-key-12345")
        s = activation_state(1)
        assert s["phase"] == "active"
        assert s["phase_ar"] == "مفعّل ✓"
        assert s["blocks_panel"] is False
        assert s["has_config"] is True
        assert s["has_snapshot"] is True

    def test_env_overrides_reported(self, app_ctx, monkeypatch):
        from app.radius.services.activation_connect import activation_state
        monkeypatch.setenv("HOBERADIUS_ADMIN_BASE_URL", "https://overridden.example")
        s = activation_state(1)
        assert "HOBERADIUS_ADMIN_BASE_URL" in s["env_overrides"]


# ════════════════════════════════════════════════════════════════════════
# (8) Routes — حتى عند قفل اللوحة
# ════════════════════════════════════════════════════════════════════════
class TestRoutesAccessibleWhenLocked:

    def test_connect_page_reachable_when_never_activated(self, app_ctx):
        # لا snapshot → اللوحة مقفلة، لكن connect لا يُعاد توجيهها
        client = _client(app_ctx)
        rv = client.get("/admin/radius/_license/connect", follow_redirects=False)
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "ربط وتفعيل النسخة" in body
        assert "بانتظار التفعيل" in body

    def test_link_action_works_when_locked(self, app_ctx):
        client = _client(app_ctx)
        token = _csrf(client)
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            rv = client.post("/admin/radius/_license/connect/link",
                              data={"_csrf_token": token,
                                    "base_url": "https://hoberadius.com",
                                    "license_key": "test-key-link-from-route-12345"},
                              follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert "/_license/connect" in rv.headers.get("Location", "")
        from app.radius.services.license_lifecycle import evaluate, LifecycleState
        assert evaluate(1).state == LifecycleState.ACTIVE

    def test_reset_action_works_when_locked_or_active(self, app_ctx):
        # ربط → reset عبر الـroute
        client = _client(app_ctx)
        token = _csrf(client)
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            client.post("/admin/radius/_license/connect/link",
                         data={"_csrf_token": token,
                               "base_url": "https://hoberadius.com",
                               "license_key": "test-key-for-reset-12345"})
        rv = client.post("/admin/radius/_license/connect/reset",
                          data={"_csrf_token": token},
                          follow_redirects=False)
        assert rv.status_code in (302, 303)
        from app.radius.services.license_lifecycle import evaluate, LifecycleState
        assert evaluate(1).state == LifecycleState.NEVER_ACTIVATED

    def test_activate_lockout_page_links_to_connect(self, app_ctx):
        client = _client(app_ctx)
        rv = client.get("/admin/radius/_license/activate")
        body = rv.get_data(as_text=True)
        assert "/admin/radius/_license/connect" in body
        assert "ربط وتفعيل النسخة الآن" in body

    def test_expired_lockout_page_links_to_connect(self, app_ctx):
        # نَسعد لقطة منتهية
        from app.radius.db.connection import db
        now = _iso(datetime.utcnow())
        db().execute(
            """INSERT INTO license_admin_bridge_snapshots
               (tenant_id, snapshot_type, normalized_status, source_url,
                payload_json, error_json, fetched_at, stale_after_seconds, created_at)
               VALUES (1, 'license', 'expired', 'test',
                       ?, '{}', ?, 86400, ?)""",
            (json.dumps({"status": "expired"}), now, now))
        client = _client(app_ctx)
        rv = client.get("/admin/radius/_license/expired")
        body = rv.get_data(as_text=True)
        assert "/admin/radius/_license/connect" in body
        assert "فتح صفحة الربط والمزامنة" in body

    def test_connect_link_in_sidebar(self, app_ctx):
        # نُفعّل النسخة كي تَفتح الـdashboard ويُرى الـsidebar
        with patch("app.radius.services.admin_panel_client.AdminPanelClient",
                    return_value=_FakeClient()):
            from app.radius.services import activation_connect as ac
            ac.link_and_activate(1, base_url="https://hoberadius.com",
                                  license_key="seed-key-for-sidebar-12345")
        client = _client(app_ctx)
        rv = client.get("/admin/radius/")
        body = rv.get_data(as_text=True)
        assert "/admin/radius/_license/connect" in body
        assert "ربط وتفعيل النسخة" in body
