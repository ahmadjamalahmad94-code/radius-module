"""fix/license-gate-paid-services — اختبارات الإصلاحَين العاجلَين.

FIX 1 (blocker): فتح اللوحة عند وجود لقطة capacity_contract نشطة حتى
لو لم تصل لقطة SNAPSHOT_LICENSE منفصلة. هذا ما يَكسر شركتي حاليًّا
(44 خدمة في العقد + لا license-block منفصل → كان never_activated).

FIX 2: locked_upgrade (مدفوعة-غير-مفعّلة) منفصلة عن disabled (موقوفة):
  • disabled  → hide من السايدبار + 403/redirect إلى blocked
  • locked_upgrade → visible + lock badge + redirect إلى upgrade page
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "lic_fix.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # نَكشف الحارس الحقيقي (نُلغي تجاوز الاختبار العام)
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


def _seed_capacity(*, tenant_id: int = 1, payload: dict) -> None:
    """يكتب لقطة capacity_contract مباشرة في DB."""
    from app.radius.db.connection import db
    now = _iso(datetime.utcnow())
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))


def _client(app, *, super_admin: bool = True):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=super_admin, tenant_id=1, permissions=["*"])
    return c


# ════════════════════════════════════════════════════════════════════════
# FIX 1 — Active license inferred from capacity contract
# ════════════════════════════════════════════════════════════════════════
class TestFix1ActiveViaCapacity:
    """يَكشف باج «شركتي»: عقد قدرات حيّ بدون license-block منفصل كان
    يَقفل اللوحة على never_activated. بعد الفيكس: مفتوحة."""

    def test_capacity_with_services_implies_active_no_license_block(
            self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        # عقد فيه 3 خدمات + لا license-block — هذا شكل بيانات «شركتي».
        _seed_capacity(payload={
            "status": "active",
            "services": {
                "reports":     {"enabled": True, "status": "active"},
                "subscribers": {"enabled": True, "status": "active"},
                "cards":       {"enabled": True, "status": "active"},
            },
        })
        d = evaluate(1)
        assert d.state == LifecycleState.ACTIVE
        assert not d.blocks_panel
        assert d.reason == "active_via_capacity_grants"

    def test_capacity_with_limits_only_also_implies_active(self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_capacity(payload={
            "status": "active",
            "limits": {"subscribers": {"max_total": 1000}},
        })
        d = evaluate(1)
        assert d.state == LifecycleState.ACTIVE
        assert d.reason == "active_via_capacity_grants"

    def test_empty_capacity_payload_does_not_imply_active(self, app_ctx):
        """عقد فارغ (لا services ولا limits ولا features) لا يَكفي."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_capacity(payload={"status": "active"})
        d = evaluate(1)
        assert d.state == LifecycleState.NEVER_ACTIVATED

    def test_capacity_with_embedded_license_block_active(self, app_ctx):
        """شكل آخر متوقّع: license-block مدمج داخل capacity-contract."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_capacity(payload={
            "status": "active",
            "license": {
                "status": "active",
                "expires_at": _iso(datetime.utcnow() + timedelta(days=60)),
            },
            "services": {"x": {"enabled": True, "status": "active"}},
        })
        d = evaluate(1)
        assert d.state == LifecycleState.ACTIVE
        assert d.reason == "active_via_capacity_license_block"
        assert d.expires_at  # نُمرّره للقالب

    def test_capacity_with_license_block_activated_true(self, app_ctx):
        """نَقبل license.activated=True كصيغة بديلة."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_capacity(payload={
            "status": "active",
            "license": {"activated": True},
            "services": {"x": {"status": "active"}},
        })
        d = evaluate(1)
        assert d.state == LifecycleState.ACTIVE

    def test_capacity_with_license_block_expired_locks(self, app_ctx):
        """license-block مدمج بحالة expired يَقفل."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_capacity(payload={
            "status": "active",
            "license": {"status": "expired"},
            "services": {"x": {"status": "active"}},
        })
        d = evaluate(1)
        assert d.state == LifecycleState.EXPIRED
        assert d.blocks_panel

    def test_capacity_with_license_block_expires_at_past_locks(self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        past = _iso(datetime.utcnow() - timedelta(days=5))
        _seed_capacity(payload={
            "status": "active",
            "license": {"status": "active", "expires_at": past},
            "services": {"x": {"status": "active"}},
        })
        d = evaluate(1)
        assert d.state == LifecycleState.EXPIRED
        assert d.reason == "expires_at_passed"

    def test_contract_dot_services_path_also_implies_active(self, app_ctx):
        """عقد قد يُلفّ services داخل contract.services — نَلتقطه."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_capacity(payload={
            "status": "active",
            "contract": {
                "services": {"x": {"status": "active"}},
                "limits": {"y": 10},
            },
        })
        d = evaluate(1)
        assert d.state == LifecycleState.ACTIVE
        assert d.reason == "active_via_capacity_grants"

    def test_no_snapshot_at_all_stays_never_activated(self, app_ctx):
        """بلا أيّ لقطة (capacity أو license) — لا تَنفعل الـfallback."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        d = evaluate(1)
        assert d.state == LifecycleState.NEVER_ACTIVATED


# ════════════════════════════════════════════════════════════════════════
# FIX 2 — locked_upgrade ≠ disabled
# ════════════════════════════════════════════════════════════════════════
class TestFix2LockedUpgrade:

    def test_locked_upgrade_status_marks_requires_upgrade(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_capacity(payload={
            "status": "active",
            "services": {
                "reports": {"enabled": True, "status": "locked_upgrade"},
            },
        })
        g = provider_grant.lookup(1, "reports")
        assert g.requires_upgrade is True
        assert g.disabled is False   # ليست موقوفة

    def test_alternate_status_names_recognized(self, app_ctx):
        from app.radius.services import provider_grant
        for variant in ("requires_activation", "paid_not_active",
                         "upgrade_required", "pending_activation"):
            _seed_capacity(payload={
                "status": "active",
                "services": {
                    "any_key": {"enabled": True, "status": variant},
                },
            })
            g = provider_grant.lookup(1, "any_key")
            assert g.requires_upgrade is True, \
                f"variant '{variant}' not recognized as locked_upgrade"
            # تنظيف للدورة التالية
            from app.radius.db.connection import db
            db().execute("DELETE FROM license_admin_bridge_snapshots")

    def test_feature_state_locked_upgrade_marked(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_capacity(payload={
            "status": "active",
            "features": {"reports": "locked_upgrade"},
        })
        g = provider_grant.lookup(1, "reports")
        assert g.requires_upgrade is True
        assert g.disabled is False

    def test_disabled_takes_precedence_over_upgrade(self, app_ctx):
        """خدمة موقوفة صريحة — disabled لها أولوية."""
        from app.radius.services import provider_grant
        _seed_capacity(payload={
            "status": "active",
            "services": {"x": {"enabled": False, "status": "disabled"}},
        })
        g = provider_grant.lookup(1, "x")
        assert g.disabled is True
        assert g.requires_upgrade is False

    def test_active_service_neither_disabled_nor_upgrade(self, app_ctx):
        from app.radius.services import provider_grant
        _seed_capacity(payload={
            "status": "active",
            "services": {"x": {"enabled": True, "status": "active"}},
        })
        g = provider_grant.lookup(1, "x")
        assert g.disabled is False
        assert g.requires_upgrade is False

    def test_provider_gate_routes_locked_upgrade_to_upgrade_page(self, app_ctx):
        """طلب GET على endpoint خدمة locked_upgrade → redirect لـ /_provider/upgrade."""
        # نُفعّل اللوحة بـimplied-active capacity
        _seed_capacity(payload={
            "status": "active",
            "services": {
                "reports": {"enabled": True, "status": "locked_upgrade"},
            },
        })
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/reports/login_states",
                          follow_redirects=False)
        assert rv.status_code in (302, 303)
        loc = rv.headers.get("Location", "")
        assert "/admin/radius/_provider/upgrade" in loc
        assert "service=reports" in loc

    def test_provider_gate_routes_disabled_to_blocked_page(self, app_ctx):
        """تأكّد ما زلنا نُوجّه disabled إلى blocked (لا upgrade)."""
        _seed_capacity(payload={
            "status": "active",
            "services": {
                "reports": {"enabled": True, "status": "disabled"},
            },
        })
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/reports/login_states",
                          follow_redirects=False)
        assert rv.status_code in (302, 303)
        loc = rv.headers.get("Location", "")
        assert "/admin/radius/_provider/blocked" in loc

    def test_locked_upgrade_write_returns_403(self, app_ctx):
        _seed_capacity(payload={
            "status": "active",
            "services": {"cards": {"enabled": True, "status": "locked_upgrade"}},
        })
        client = _client(app_ctx, super_admin=True)
        client.get("/admin/radius/")  # CSRF seed
        with client.session_transaction() as s:
            token = s.get("_csrf_token") or ""
        rv = client.post("/admin/radius/cards/generate",
                          data={"plan_id": "1", "count": "5",
                                "_csrf_token": token})
        assert rv.status_code == 403

    def test_sidebar_keeps_locked_upgrade_visible_with_badge(self, app_ctx):
        """البند يَبقى مرئيًّا في الشريط الجانبي + شارة قفل."""
        _seed_capacity(payload={
            "status": "active",
            "services": {
                "reports": {"enabled": True, "status": "locked_upgrade"},
            },
        })
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/")
        body = rv.get_data(as_text=True)
        # البند ما زال مرئيًّا (href موجود)
        assert "/admin/radius/reports/" in body
        # شارة الترقية موجودة
        assert "hb-side-upgrade-badge" in body or "data-provider-upgrade" in body

    def test_sidebar_hides_disabled_keeps_upgrade(self, app_ctx):
        """نَختبر التمييز: خدمة disabled تَختفي، locked_upgrade تبقى."""
        _seed_capacity(payload={
            "status": "active",
            "services": {
                # cards موقوفة → يجب أن تَختفي روابطها
                "cards":   {"enabled": True, "status": "disabled"},
                # reports مدفوعة-تحتاج تفعيل → يجب أن تَبقى
                "reports": {"enabled": True, "status": "locked_upgrade"},
            },
        })
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/")
        body = rv.get_data(as_text=True)
        # cards مخفية
        assert "/admin/radius/cards/checker" not in body
        # reports باقية
        assert "/admin/radius/reports/" in body

    def test_upgrade_page_renders_with_service_details(self, app_ctx):
        _seed_capacity(payload={
            "status": "active",
            "services": {"reports": {"enabled": True,
                                       "status": "locked_upgrade"}},
        })
        client = _client(app_ctx, super_admin=True)
        rv = client.get(
            "/admin/radius/_provider/upgrade?service=reports")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "طلب تفعيل / ترقية" in body
        assert "reports" in body
        # CTAs تَظهر
        assert "ترخيص النظام" in body

    def test_upgrade_page_reachable_even_when_all_services_locked(self, app_ctx):
        """صفحة upgrade لا يَنبغي أن يُعاد توجيهها لنفسها."""
        _seed_capacity(payload={
            "status": "active",
            "services": {"reports": {"enabled": True,
                                       "status": "locked_upgrade"}},
        })
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/_provider/upgrade?service=reports",
                          follow_redirects=False)
        assert rv.status_code == 200


# ════════════════════════════════════════════════════════════════════════
# (3) Flutter API يَعكس الإصلاحَين
# ════════════════════════════════════════════════════════════════════════
class TestFlutterApiReflectsFixes:

    AUTH = {"Authorization": "Bearer dev-token-please-change"}

    def test_api_reports_active_via_capacity(self, app_ctx):
        """تطبيق Flutter يَرى license.state=active حتى بلا license-snapshot منفصل."""
        _seed_capacity(payload={
            "status": "active",
            "services": {"x": {"status": "active"}},
        })
        rv = app_ctx.test_client().get(
            "/api/v1/provider/grants", headers=self.AUTH)
        assert rv.status_code == 200
        data = rv.get_json()["data"]
        assert data["license"]["state"] == "active"
        assert data["license"]["blocks_panel"] is False
        assert data["license"]["reason"] == "active_via_capacity_grants"

    def test_api_exposes_requires_upgrade_field(self, app_ctx):
        _seed_capacity(payload={
            "status": "active",
            "services": {
                "reports": {"enabled": True, "status": "locked_upgrade"},
            },
        })
        rv = app_ctx.test_client().get(
            "/api/v1/provider/grants", headers=self.AUTH)
        data = rv.get_json()["data"]
        reports = next(s for s in data["services"] if s["key"] == "reports")
        assert reports["requires_upgrade"] is True
        assert reports["disabled"] is False

    def test_api_schema_version_bumped_to_2(self, app_ctx):
        _seed_capacity(payload={"status": "active",
                                  "services": {"x": {"status": "active"}}})
        rv = app_ctx.test_client().get(
            "/api/v1/provider/grants", headers=self.AUTH)
        assert rv.get_json()["data"]["schema_version"] == 2
