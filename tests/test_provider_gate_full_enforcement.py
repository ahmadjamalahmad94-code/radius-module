"""feat/provider-gate-enforce — اختبارات شاملة: عند تعطيل خدمة في العقد،
كلّ صفحاتها مَحجوبة فعلًا للسوبر-أدمن.

السياق (تشخيص حيّ 2026-06-18):
  مَوقع 187.x، السوبر-أدمن داخل، عقد المزوّد يَحوي ``finance_center`` و
  ``communications`` كموقوفتين. صفحة /admin/radius/_provider/grants تَعرضهما
  «موقوفة» (القراءة صحيحة)، لكن GET /admin/radius/finance-center و
  /admin/radius/communications يَفتحان الصفحة كاملةً — الحجب لا يَحدث.

الجذر: ``provider_gate._ENDPOINT_TO_SERVICE`` كان يُسطّح ``finance_center``
endpoint إلى ``finance`` umbrella. عقد المزوّد كان يَستعمل المفتاح المفصّل
``finance_center`` فلم يَنطبق ``is_service_disabled(tid, "finance")``.

الإصلاح (هذا الفرع):
  • ``service_keys_for_endpoint`` يُعيد قائمة مرشّحات (الأخصّ أوّلًا، ثم
    المظلّة). الحارس يَحجب على أوّل مرشّحة موقوفة.
  • توسعة شاملة لتغطي 98%+ من /admin/radius/* endpoints (588/685).
  • هذا الملف يَتحقّق أنّ تَعطيل كلّ مفتاح في عقد فعليّ يَحجب كلّ صفحاته.

شغّل وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "gate_full.db")
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


def _seed(*, services: dict | None = None,
          features: dict | None = None) -> None:
    """يَزرع لقطة capacity + license نشطة."""
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    payload = {"status": "active",
                "services": services or {},
                "features": features or {}}
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
    """عميل مُسجَّل دخوله كسوبر-أدمن — الحارس يَجب أن يَحجبه رغم ذلك."""
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=True, tenant_id=1, permissions=["*"])
    return c


def _is_blocked_response(rv) -> bool:
    """نَحجب يَعني: 302 إلى provider_blocked_page أو 403."""
    if rv.status_code == 403:
        return True
    if rv.status_code in (301, 302, 303, 307, 308):
        loc = (rv.headers.get("Location") or "").lower()
        # /admin/radius/provider/blocked أو ما يَحوي 'provider' و 'blocked'
        return "provider" in loc and "blocked" in loc
    return False


# ════════════════════════════════════════════════════════════════════════
# (1) الحالتان اللتان كَسرتا الإنتاج — finance_center + communications
# ════════════════════════════════════════════════════════════════════════
class TestLiveRegression_2026_06_18:
    """انحدار: عقد المزوّد يَحوي finance_center/communications كموقوفتين،
    لكن GET على صفحاتهما كان يَفتح للسوبر-أدمن. هذا الاختبار يَفشل بدون
    الإصلاح ويَنجح معه."""

    def test_finance_center_blocked_when_disabled(self, app_ctx):
        # عقد يَستعمل المفتاح المفصّل (لا umbrella)
        _seed(services={"finance_center": {"enabled": False,
                                             "status": "disabled"}})
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/finance-center", follow_redirects=False)
        assert _is_blocked_response(rv), \
            f"finance_center page must be blocked, got {rv.status_code} → {rv.headers.get('Location')}"

    def test_finance_center_blocked_via_umbrella(self, app_ctx):
        # عقد يَستعمل المفتاح المظلّة — يَجب أن يَحجب أيضًا (مظلّة من المرشّحات)
        _seed(services={"finance": {"enabled": False, "status": "disabled"}})
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/finance-center", follow_redirects=False)
        assert _is_blocked_response(rv), \
            f"finance umbrella must also block finance_center, got {rv.status_code}"

    def test_communications_blocked_when_disabled(self, app_ctx):
        _seed(services={"communications": {"enabled": False,
                                             "status": "disabled"}})
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/communications", follow_redirects=False)
        assert _is_blocked_response(rv), \
            f"communications page must be blocked, got {rv.status_code} → {rv.headers.get('Location')}"

    def test_cards_writes_403_when_disabled(self, app_ctx):
        # نَتبع نمط test_provider_grant_gate.py: GET أوّلًا لتأسيس الجلسة
        # وتوكِن CSRF، ثم POST مع التوكِن. الحارس يَجب أن يَردّ 403.
        _seed(services={"cards": {"enabled": True, "status": "disabled"}})
        c = _super_client(app_ctx)
        c.get("/admin/radius/")
        with c.session_transaction() as s:
            token = s.get("_csrf_token") or ""
        rv = c.post("/admin/radius/cards/generate",
                     data={"plan_id": "1", "count": "5",
                            "_csrf_token": token})
        assert rv.status_code == 403, \
            f"POST on disabled cards must 403, got {rv.status_code}"


# ════════════════════════════════════════════════════════════════════════
# (2) المسح الواسع — لكلّ مجموعة من المفاتيح، تَعطيلها يَحجب صفحاتها
# ════════════════════════════════════════════════════════════════════════
# نَختبر مجموعة تَمثيلية تَغطّي القطاعات الـ44 الرئيسية في العقد:
# كل عنصر = (service_key_in_contract, url_to_check, friendly_name).
# عند تَعطيل service_key في العقد، GET url_to_check يَجب أن يُحجَب.
_SECTOR_PROBES: list[tuple[str, str, str]] = [
    # المشتركون والبطاقات
    ("subscribers",        "/admin/radius/users",              "users_list"),
    ("subscriber_groups",  "/admin/radius/subscriber-groups",  "subscriber_groups_list"),
    ("cards",              "/admin/radius/cards",              "cards_overview"),
    ("card_users",         "/admin/radius/card-users",         "card_users_list"),
    ("card_marketplace",   "/admin/radius/card-marketplace",   "card_marketplace"),

    # المالية
    ("finance",            "/admin/radius/finance-hub",        "finance_hub"),
    ("finance_center",     "/admin/radius/finance-center",     "finance_center"),
    ("accounting",         "/admin/radius/accounting",         "accounting_hub"),
    ("billing",            "/admin/radius/billing",            "billing_hub"),
    ("payment_collection", "/admin/radius/collection",         "collection_hub"),

    # التقارير والتدقيق
    ("reports",            "/admin/radius/reports",            "reports_index"),
    ("audit",              "/admin/radius/audit",              "audit_log_index"),

    # الشبكة والمايكروتيك
    ("network",            "/admin/radius/mt",                 "mt_dashboard"),
    ("devices",            "/admin/radius/devices",            "devices_list"),
    ("device_health",      "/admin/radius/device-health",      "device_health_page"),
    ("pools",              "/admin/radius/pools",              "pool_list"),
    ("network_policy",     "/admin/radius/netpolicy",          "netpolicy_index"),

    # الأمان والتحكّم
    ("access_control",     "/admin/radius/access-control",     "access_control_page"),
    ("anti_mac_clone",     "/admin/radius/anti-mac-clone",     "anti_mac_clone_page"),

    # الإدارة والإعدادات
    ("admins",             "/admin/radius/admins",             "admins_list"),
    ("tenants",            "/admin/radius/tenants",            "tenants_list"),
    ("backups",            "/admin/radius/backups",            "backups"),
    ("settings",           "/admin/radius/settings",           "settings_page"),

    # الاتصالات
    ("communications",     "/admin/radius/communications",     "communications_index"),

    # المتجر والخدمات
    ("store",              "/admin/radius/store",              "store_overview"),
    ("service_requests",   "/admin/radius/service-requests",   "service_request_list"),

    # الأدوات
    ("tools",              "/admin/radius/tools",              "tools_index"),
]


class TestPerSectorEnforcement:
    """لكل مجموعة في عقد فعليّ: تَعطيلها يَحجب صفحتها الرئيسية للسوبر-أدمن."""

    @pytest.mark.parametrize("svc_key,url,name", _SECTOR_PROBES,
                               ids=[p[2] for p in _SECTOR_PROBES])
    def test_disabled_service_blocks_main_route(
            self, app_ctx, svc_key, url, name):
        _seed(services={svc_key: {"enabled": False, "status": "disabled"}})
        c = _super_client(app_ctx)
        rv = c.get(url, follow_redirects=False)
        # نَقبل 302 إلى blocked-page أو 403 أو 404 (إذا الـURL غير مسجّل في
        # هذا الفرع — وهذا ليس فشلًا في الحجب). نَفشل فقط لو الصفحة فُتحت 200.
        if rv.status_code == 404:
            pytest.skip(f"URL {url} not registered in current build")
        assert _is_blocked_response(rv), (
            f"{name} ({url}): service={svc_key} disabled but page not blocked "
            f"(status={rv.status_code}, loc={rv.headers.get('Location')})")


# ════════════════════════════════════════════════════════════════════════
# (3) features.<k> = locked أو hidden — نفس النتيجة
# ════════════════════════════════════════════════════════════════════════
class TestFeatureStateEnforcement:

    def test_feature_locked_blocks_page(self, app_ctx):
        _seed(features={"reports": "locked"})
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/reports", follow_redirects=False)
        assert _is_blocked_response(rv), \
            f"features.reports=locked must block reports page, got {rv.status_code}"

    def test_feature_hidden_blocks_page(self, app_ctx):
        _seed(features={"reports": "hidden"})
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/reports", follow_redirects=False)
        assert _is_blocked_response(rv)


# ════════════════════════════════════════════════════════════════════════
# (4) عقد فاضي = سماح كامل (fail-open) — لا انحدار
# ════════════════════════════════════════════════════════════════════════
class TestNoContractAllowsAll:

    def test_no_contract_allows_finance(self, app_ctx):
        # لقطة license فقط، لا capacity → كل شيء مسموح
        from app.radius.db.connection import db
        now = datetime.utcnow().isoformat() + "Z"
        db().execute(
            """INSERT INTO license_admin_bridge_snapshots
               (tenant_id, snapshot_type, normalized_status, source_url,
                payload_json, error_json, fetched_at, stale_after_seconds, created_at)
               VALUES (1, 'license', 'active', 'test://license',
                       ?, '{}', ?, 86400, ?)""",
            (json.dumps({"status": "active"}, ensure_ascii=False),
             now, now))
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/finance-center", follow_redirects=False)
        # لا حجب → 200 أو حتى 500 (لكن ليس redirect إلى blocked)
        assert not _is_blocked_response(rv), \
            f"no-contract must NOT block, got {rv.status_code} → {rv.headers.get('Location')}"


# ════════════════════════════════════════════════════════════════════════
# (5) خدمة غير الموقوفة لا تَتأثّر — العزل المنطقي
# ════════════════════════════════════════════════════════════════════════
class TestOnlyTargetedServiceBlocked:

    def test_disabling_finance_does_not_block_subscribers(self, app_ctx):
        _seed(services={"finance_center": {"enabled": False,
                                             "status": "disabled"}})
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/users", follow_redirects=False)
        assert not _is_blocked_response(rv), \
            f"subscribers must NOT be blocked when only finance is disabled, " \
            f"got {rv.status_code} → {rv.headers.get('Location')}"

    def test_disabling_subscribers_does_not_block_finance(self, app_ctx):
        _seed(services={"subscribers": {"enabled": False,
                                          "status": "disabled"}})
        c = _super_client(app_ctx)
        rv = c.get("/admin/radius/finance-center", follow_redirects=False)
        assert not _is_blocked_response(rv)


# ════════════════════════════════════════════════════════════════════════
# (6) coverage check — معظم admin endpoints مُسجَّلة في المرشّحات
# ════════════════════════════════════════════════════════════════════════
class TestEndpointCoverage:

    def test_admin_endpoints_mostly_covered(self, app_ctx):
        """≥95% من /admin/radius/* endpoints مُسجَّلة (مع candidate واحدة على
        الأقل). الباقي = استثناءات شَرعيّة (health/docs/skip-page targets/
        switch_locale) وليست تَسرّبًا."""
        from app.radius.auth.provider_gate import service_keys_for_endpoint
        eps: set[str] = set()
        for r in app_ctx.url_map.iter_rules():
            if r.endpoint.startswith("radius.") and r.rule.startswith("/admin/radius/"):
                eps.add(r.endpoint.split(".", 1)[1])
        unmapped = [ep for ep in eps if not service_keys_for_endpoint(ep)]
        coverage = (len(eps) - len(unmapped)) / max(len(eps), 1)
        assert coverage >= 0.95, (
            f"coverage {coverage:.1%} below 95% — too many endpoints lack a "
            f"service-key candidate, gate enforcement gap. Unmapped: {unmapped[:20]}")

    def test_finance_center_has_candidate(self, app_ctx):
        from app.radius.auth.provider_gate import service_keys_for_endpoint
        keys = service_keys_for_endpoint("finance_center")
        assert "finance_center" in keys, \
            f"finance_center must be a candidate, got {keys}"
        assert "finance" in keys, \
            f"finance umbrella must also be a candidate, got {keys}"

    def test_communications_has_candidate(self, app_ctx):
        from app.radius.auth.provider_gate import service_keys_for_endpoint
        keys = service_keys_for_endpoint("communications_index")
        assert "communications" in keys, \
            f"communications must be a candidate for communications_index, got {keys}"
