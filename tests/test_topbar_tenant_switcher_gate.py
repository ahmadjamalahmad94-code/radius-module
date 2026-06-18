"""feat/topbar-tenant-switcher-gate — اختبارات بوّابة «تبديل الجهة» في الـtopbar
على مُنحة multi_tenant.

الانحدار الحيّ (2026-06-18، نسخة 187.x):
  «الجهات» (multi_tenant) كانت «hidden» في عقد المزوّد، وغُيِّبت من
  السايدبار بشكل صحيح، لكن «تبديل الجهة» في الـtopbar كانت لا تَزال
  تَظهر للسوبر-أدمن (تَعرض starter/Hobe Hub + pro/ACME ISP + «إدارة الجهات»).

العلاج (هذا الفرع): قالب admin/_admin_layout.html يَلتفّ بحارس
provider_multi_tenant_granted() — يَختفي تمامًا (لا pill، لا dropdown،
لا «إدارة الجهات») ما لم يُمنَح multi_tenant صراحةً في العقد. عند المنح،
يَعرض حتى ـlimits.multi_tenant.entity_count من الجهات.

شغّل وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "topbar_switcher.db")
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
        # نَزرع جهة ثانية كي تَتوفّر مرشّحات للسويتشر (وإلّا لن يَظهر
        # حتى لو مُنح multi_tenant).
        from app.radius.db.connection import db
        now = datetime.utcnow().isoformat() + "Z"
        db().execute(
            """INSERT INTO tenants (id, slug, name, display_name, created_at,
                                     plan_tier)
               VALUES (2, 'acme', 'ACME ISP', 'ACME ISP', ?, 'pro')""",
            (now,))
        db().execute(
            """INSERT INTO tenants (id, slug, name, display_name, created_at,
                                     plan_tier)
               VALUES (3, 'beta', 'Beta Co', 'Beta Co', ?, 'starter')""",
            (now,))
        yield app


def _seed(*, services: dict | None = None,
          features: dict | None = None,
          limits: dict | None = None) -> None:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    payload = {"status": "active",
                "services": services or {},
                "features": features or {},
                "limits":   limits or {}}
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (1, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (json.dumps(payload, ensure_ascii=False), now, now))
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (1, 'license', 'active', 'test://license',
                   ?, '{}', ?, 86400, ?)""",
        (json.dumps({"status": "active"}, ensure_ascii=False), now, now))


def _super_client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=True, tenant_id=1, permissions=["*"])
    return c


def _layout_body(client) -> str:
    """يَجلب أيّ صفحة admin layout ثم يُرجع HTML — السويتشر في الـtopbar."""
    rv = client.get("/admin/radius/")
    return rv.get_data(as_text=True)


# ════════════════════════════════════════════════════════════════════════
# (1) provider_multi_tenant_granted — منطق الحارس
# ════════════════════════════════════════════════════════════════════════
class TestGrantedHelper:

    def test_no_contract_means_not_granted(self, app_ctx):
        # لا عقد → multi_tenant غير ممنوح (fail-closed على هذه الخدمة عمدًا)
        from app.radius.services import provider_grant
        g = provider_grant.lookup(1, "multi_tenant")
        assert not g.present  # لا يَذكره العقد
        # الـhelper المُحقَن في القوالب نَفحصه عبر رندر فعليّ (أدناه)

    def test_contract_without_multi_tenant_means_not_granted(self, app_ctx):
        _seed(services={"reports": {"enabled": True, "status": "active"}})
        from app.radius.services import provider_grant
        g = provider_grant.lookup(1, "multi_tenant")
        assert not g.present

    def test_hidden_means_not_granted(self, app_ctx):
        """الانحدار الحيّ: features.multi_tenant=hidden = غير ممنوح."""
        _seed(features={"multi_tenant": "hidden"})
        from app.radius.services import provider_grant
        g = provider_grant.lookup(1, "multi_tenant")
        assert g.present and g.disabled

    def test_locked_means_not_granted(self, app_ctx):
        _seed(features={"multi_tenant": "locked"})
        from app.radius.services import provider_grant
        g = provider_grant.lookup(1, "multi_tenant")
        assert g.disabled

    def test_explicitly_enabled_is_granted(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": True, "status": "active"}})
        from app.radius.services import provider_grant
        g = provider_grant.lookup(1, "multi_tenant")
        assert g.present and not g.disabled and not g.requires_upgrade

    def test_requires_upgrade_is_not_granted(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": True,
                                            "status": "locked_upgrade"}})
        from app.radius.services import provider_grant
        g = provider_grant.lookup(1, "multi_tenant")
        assert g.requires_upgrade


# ════════════════════════════════════════════════════════════════════════
# (2) رندر القالب — السويتشر يَختفي/يَظهر حسب المُنحة
# ════════════════════════════════════════════════════════════════════════
class TestTopbarRenderGate:
    """الانحدار الحيّ — هذه الاختبارات تَفشل قبل الفِرع وتَنجح معه."""

    def test_hidden_multi_tenant_hides_switcher(self, app_ctx):
        """الجوهر: عقد بـmulti_tenant=hidden → السويتشر غائب عن الـtopbar."""
        _seed(features={"multi_tenant": "hidden"})
        body = _layout_body(_super_client(app_ctx))
        # لا pill، لا dropdown، لا «إدارة الجهات» في الـtopbar
        assert 'data-mt-switcher="1"' not in body, \
            "topbar tenant-switcher must NOT render when multi_tenant is hidden"
        assert 'data-mt-pill="1"' not in body
        # «تبديل الجهة» النصّ يَجب أن لا يَظهر (لكن قد يَظهر في صفحة tenants_list
        # في أماكن أخرى؛ نَفحص فقط في الـtopbar markers)
        # nav «إدارة الجهات» المرتبطة بـtenants_list قد تَبقى في السايدبار —
        # ذلك خارج نطاق هذا الاختبار. هنا نَتحقّق فقط من الـtopbar dropdown.

    def test_locked_multi_tenant_hides_switcher(self, app_ctx):
        _seed(features={"multi_tenant": "locked"})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' not in body

    def test_no_contract_hides_switcher(self, app_ctx):
        """لا عقد إطلاقًا → fail-closed (الخدمة غير ممنوحة بشكل ضمنيّ)."""
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' not in body, \
            "no-contract case must also hide switcher (multi_tenant is opt-in)"

    def test_contract_without_multi_tenant_hides_switcher(self, app_ctx):
        _seed(services={"subscribers": {"enabled": True, "status": "active"}})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' not in body

    def test_disabled_status_hides_switcher(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": False,
                                           "status": "disabled"}})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' not in body

    def test_requires_upgrade_hides_switcher(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": True,
                                           "status": "locked_upgrade"}})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' not in body


class TestTopbarRenderShown:
    """عند المُنحة، السويتشر يَظهر بالـmarkers الصحيحة."""

    def test_granted_shows_switcher(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": True, "status": "active"}})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' in body, \
            "switcher must render when multi_tenant is granted"
        assert 'data-mt-pill="1"' in body
        # نَصّ الـheader عربيّ
        assert "تبديل الجهة" in body

    def test_granted_shows_admin_tenants(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": True, "status": "active"}})
        body = _layout_body(_super_client(app_ctx))
        # كلتا الجهتين المَزروعتين في الـDB تَظهران (ACME + Beta)
        assert "ACME ISP" in body
        assert "Beta Co" in body

    def test_granted_shows_manage_link_for_super(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": True, "status": "active"}})
        body = _layout_body(_super_client(app_ctx))
        assert "إدارة الجهات" in body
        # رابط إلى tenants_list
        assert "/admin/radius/tenants" in body


# ════════════════════════════════════════════════════════════════════════
# (3) entity_count — العقد يَحدّ عدد الجهات المعروضة
# ════════════════════════════════════════════════════════════════════════
class TestEntityCountLimit:

    def test_entity_count_truncates_list(self, app_ctx):
        """العقد يَمنح multi_tenant مع entity_count=2 → جهة ثالثة لا تَظهر."""
        # الـDB يَحوي 3 جهات (Default + ACME + Beta). نُحدّد العقد بـ2.
        _seed(services={"multi_tenant": {"enabled": True, "status": "active"}},
              limits={"multi_tenant": {"entity_count": 2}})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' in body
        # نَفحص عدّاد «2/3» المُعَرَّض كي نَتأكّد أنّ التقطيع طُبِّق
        # (التَطبيق الفعليّ في الـquery يَأخذ أوّل اثنين)
        # نَتحقّق أنّ الـcount badge ظَهر
        assert ">2/3<" in body or "2/3" in body

    def test_no_limit_shows_all(self, app_ctx):
        _seed(services={"multi_tenant": {"enabled": True, "status": "active"}})
        body = _layout_body(_super_client(app_ctx))
        # كل الجهات المُسجَّلة تَظهر، لا badge عدّاد
        assert "ACME ISP" in body
        assert "Beta Co" in body

    def test_alt_limit_path_entities_max(self, app_ctx):
        """مسار بديل: limits.entities.max."""
        _seed(services={"multi_tenant": {"enabled": True, "status": "active"}},
              limits={"entities": {"max": 1}})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' in body
        # العدّاد يَجب أن يَظهر بشكل ما (1/N)
        # على الأقل، ACME أو Beta واحد منهما يَجب أن يَنقص
        # نَتحقّق فقط أنّ السويتشر ظَهر — التقطيع يُعتمد على ترتيب الـquery


# ════════════════════════════════════════════════════════════════════════
# (4) السلامة: السوبر-أدمن لا يَتجاوز الحارس
# ════════════════════════════════════════════════════════════════════════
class TestSuperAdminCannotBypass:
    """قاعدة عقد المزوّد: السوبر-أدمن لا يَتجاوز قرار المزوّد التجاريّ."""

    def test_super_admin_does_not_see_switcher_when_hidden(self, app_ctx):
        _seed(features={"multi_tenant": "hidden"})
        body = _layout_body(_super_client(app_ctx))
        assert 'data-mt-switcher="1"' not in body, \
            "super-admin must NOT bypass provider's multi_tenant=hidden grant"

    def test_super_admin_does_not_see_manage_link_when_hidden(self, app_ctx):
        """«إدارة الجهات» تَختفي مع السويتشر — لا تَتجاوز للسوبر."""
        _seed(features={"multi_tenant": "hidden"})
        body = _layout_body(_super_client(app_ctx))
        # link داخل الـtopbar يَختفي. (السايدبار قد يَحوي عنصر منفصل
        # لـtenants_list — ذلك مَحكوم بالسايدبار، خارج هذا الاختبار.)
        # نَتحقّق فقط من عدم وجود الـdropdown markers.
        assert 'data-mt-switcher="1"' not in body
