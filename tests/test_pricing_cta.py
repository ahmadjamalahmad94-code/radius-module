"""feat/provider-gate-enforce — اختبارات CTA «اعرض الباقات / جدّد اشتراكك»
على صفحتي قفل الترخيص (activate + expired).

تغطّي:
  • resolve_pricing_url: من العقد > من env_settings > الافتراضي.
  • template يَعرض الزرّ وlink target=_blank ويَحوي الرابط الصحيح.
  • صفحتا activate/expired تَبقيان مَكشوفتين عند قفل اللوحة (تَبقى
    قابلة للوصول للسوبر — الـCTA يَجب أن يَكون مَرئيًّا).
  • تَدويل: نَصّ الزرّ عربيّ.
  • أمان: target=_blank يَأتي مع rel=noopener.

شغّل وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "pricing_cta.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_PRICING_URL", raising=False)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # تَفعيل lifecycle gate (تَجاوزه يَلغي قفل اللوحة فلن تَظهر صفحتا
    # activate/expired)
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


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=True, tenant_id=1, permissions=["*"])
    return c


def _seed_contract(payload: dict | None = None) -> None:
    """يَزرع lifecycle: لقطة license نشطة + capacity (مع payload المُعطى)."""
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    if payload is None:
        payload = {"status": "active"}
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (1, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (json.dumps(payload, ensure_ascii=False), now, now))


def _seed_expired_license() -> None:
    """يَزرع license expired (يَدفع decision.blocks_panel = True →
    redirect إلى license_expired_page)."""
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (1, 'license', 'expired', 'test://license',
                   ?, '{}', ?, 86400, ?)""",
        (json.dumps({"status": "expired"}, ensure_ascii=False), now, now))


# ════════════════════════════════════════════════════════════════════════
# (1) resolve_pricing_url — أولويّات
# ════════════════════════════════════════════════════════════════════════
class TestPricingUrlResolution:

    def test_default_when_no_contract_no_env(self, app_ctx):
        from app.radius.routes.license_lifecycle_pages import resolve_pricing_url
        # لا عقد، لا env → الافتراضي
        url = resolve_pricing_url(1)
        assert url.startswith("https://"), f"got {url!r}"
        assert "hoberadius.com" in url

    def test_env_value_used_when_set(self, app_ctx, monkeypatch):
        from app.radius.routes.license_lifecycle_pages import resolve_pricing_url
        # env override (دون قيمة DB) — يُؤخذ
        monkeypatch.setenv("HOBERADIUS_PRICING_URL",
                            "https://billing.example.com/plans")
        url = resolve_pricing_url(1)
        assert url == "https://billing.example.com/plans"

    def test_contract_value_overrides_env(self, app_ctx, monkeypatch):
        from app.radius.routes.license_lifecycle_pages import resolve_pricing_url
        monkeypatch.setenv("HOBERADIUS_PRICING_URL",
                            "https://billing.example.com/plans")
        _seed_contract({"status": "active",
                          "pricing_url": "https://provider.example.com/pricing"})
        url = resolve_pricing_url(1)
        # العقد يَتقدّم على env
        assert url == "https://provider.example.com/pricing"

    def test_contract_alias_renew_url(self, app_ctx):
        from app.radius.routes.license_lifecycle_pages import resolve_pricing_url
        _seed_contract({"status": "active",
                          "renew_url": "https://provider.example.com/renew"})
        url = resolve_pricing_url(1)
        assert url == "https://provider.example.com/renew"

    def test_invalid_contract_url_falls_back(self, app_ctx):
        from app.radius.routes.license_lifecycle_pages import resolve_pricing_url
        _seed_contract({"status": "active",
                          "pricing_url": "not-a-url-just-garbage"})
        url = resolve_pricing_url(1)
        # العقد بقيمة بلا http/https → نَتجاوز إلى env/default
        assert url.startswith("https://")
        assert "hoberadius.com" in url

    def test_db_setting_used_when_set(self, app_ctx):
        from app.radius.routes.license_lifecycle_pages import resolve_pricing_url
        from app.radius.core import env_settings
        env_settings.set_value("HOBERADIUS_PRICING_URL",
                                 "https://db.example.com/pricing")
        url = resolve_pricing_url(1)
        assert url == "https://db.example.com/pricing"


# ════════════════════════════════════════════════════════════════════════
# (2) Templates — صفحتا قفل الترخيص تَعرضان CTA الباقات بشكل بارز
# ════════════════════════════════════════════════════════════════════════
class TestExpiredPageCTA:

    def test_expired_page_renders_pricing_cta(self, app_ctx):
        # نَزرع license منتهٍ → الـlifecycle gate يَفتح هذه الصفحة بدل اللوحة
        _seed_expired_license()
        c = _client(app_ctx)
        rv = c.get("/admin/radius/_license/expired")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        # data attribute نُعلِّمها للاختبار + الـCTA يَظهر
        assert 'data-pricing-cta="expired"' in body
        assert "data-pricing-link" in body
        # رابط افتراضي
        assert "hoberadius.com/pricing" in body
        # نَصّ عربي للزرّ
        assert "اعرض الباقات" in body or "جدّد اشتراكك" in body

    def test_expired_page_pricing_link_target_blank(self, app_ctx):
        _seed_expired_license()
        c = _client(app_ctx)
        body = c.get("/admin/radius/_license/expired").get_data(as_text=True)
        # target=_blank ضروري + noopener أمنيّ
        # نَتحقّق ضمن block CTA (نَستعمل data-pricing-link)
        # نُلاحظ السطر يَحوي both target و noopener
        idx = body.find("data-pricing-link")
        assert idx >= 0
        # ابحث في المنطقة المحيطة (200 حرف بعد)
        snippet = body[idx:idx + 400]
        assert 'target="_blank"' in snippet
        assert "noopener" in snippet

    def test_expired_page_uses_contract_url(self, app_ctx):
        from app.radius.db.connection import db
        now = datetime.utcnow().isoformat() + "Z"
        # license منتهٍ + contract بـpricing_url
        db().execute(
            """INSERT INTO license_admin_bridge_snapshots
               (tenant_id, snapshot_type, normalized_status, source_url,
                payload_json, error_json, fetched_at, stale_after_seconds, created_at)
               VALUES (1, 'license', 'expired', 'test://license',
                       ?, '{}', ?, 86400, ?)""",
            (json.dumps({"status": "expired"}, ensure_ascii=False), now, now))
        _seed_contract({"status": "active",
                          "pricing_url": "https://provider.test/plans/123"})
        c = _client(app_ctx)
        body = c.get("/admin/radius/_license/expired").get_data(as_text=True)
        assert "https://provider.test/plans/123" in body


class TestActivatePageCTA:

    def test_activate_page_renders_pricing_cta(self, app_ctx):
        # لا أيّ لقطة → NEVER_ACTIVATED → redirect إلى activate_page
        c = _client(app_ctx)
        rv = c.get("/admin/radius/_license/activate")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert 'data-pricing-cta="activate"' in body
        assert "data-pricing-link" in body
        assert "hoberadius.com/pricing" in body
        assert "اعرض الباقات" in body or "اشترك" in body

    def test_activate_page_pricing_link_safe(self, app_ctx):
        c = _client(app_ctx)
        body = c.get("/admin/radius/_license/activate").get_data(as_text=True)
        idx = body.find("data-pricing-link")
        assert idx >= 0
        snippet = body[idx:idx + 400]
        assert 'target="_blank"' in snippet
        assert "noopener" in snippet


# ════════════════════════════════════════════════════════════════════════
# (3) login + license + diagnostics تَبقى مَكشوفة على نسخة مَقفولة
# ════════════════════════════════════════════════════════════════════════
class TestLockoutReachability:

    def test_login_reachable_on_expired(self, app_ctx):
        _seed_expired_license()
        c = app_ctx.test_client()  # بلا session
        rv = c.get("/admin/radius/login", follow_redirects=False)
        # 200 أو 302 إلى login (المهم: ليس قفلًا)
        assert rv.status_code in (200, 302, 303)

    def test_license_file_reachable_on_expired(self, app_ctx):
        _seed_expired_license()
        c = _client(app_ctx)
        rv = c.get("/admin/radius/license-file", follow_redirects=False)
        assert rv.status_code in (200, 302), \
            f"license_file must remain reachable, got {rv.status_code}"

    def test_provider_grants_status_reachable_on_expired(self, app_ctx):
        _seed_expired_license()
        c = _client(app_ctx)
        rv = c.get("/admin/radius/_provider/grants",
                     follow_redirects=False)
        assert rv.status_code == 200, \
            f"provider grants diagnostic must remain reachable, got {rv.status_code}"


# ════════════════════════════════════════════════════════════════════════
# (4) عقد فاضي → CTA يَستعمل الافتراضي (لا يَختفي أبدًا)
# ════════════════════════════════════════════════════════════════════════
class TestCtaNeverDisappears:

    def test_cta_always_present_on_expired(self, app_ctx):
        _seed_expired_license()
        c = _client(app_ctx)
        body = c.get("/admin/radius/_license/expired").get_data(as_text=True)
        # حتى بلا أيّ تخصيص، الـCTA يَظهر بقيمة افتراضيّة
        assert 'data-pricing-cta="expired"' in body
        assert "data-pricing-link" in body

    def test_cta_always_present_on_activate(self, app_ctx):
        c = _client(app_ctx)
        body = c.get("/admin/radius/_license/activate").get_data(as_text=True)
        assert 'data-pricing-cta="activate"' in body
        assert "data-pricing-link" in body
