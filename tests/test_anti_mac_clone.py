"""feat/anti-mac-clone — اختبارات «منع استنساخ MAC».

تغطّي:
  * بناء البصمة من إشارات الـauth (build_fingerprint).
  * المقارنة + درجة الخطورة + استنتاج الثقة (compare).
  * مطابقة النطاق scope (all / plans / groups).
  * كشف الجلسات المتزامنة (impossible-travel) من radacct.
  * Repo round-trip (upsert_binding / log_event / list_bindings / list_events).
  * الإنفاذ في policy_engine: مغلقة افتراضيًّا، sleep → bind، تحقّق ✓، استنساخ → رفض.
  * Monitor mode لا يرفض ويُسجّل فقط.
  * Scope plans يقصر على الـplan_ids المحدّدة.

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف — متابعة memory test-isolation-per-file).
"""
from __future__ import annotations

import os

import pytest

from app.radius.services import anti_mac_clone as svc


# ════════════════════════════════════════════════════════════════════════
# (1) منطق خالص — لا DB
# ════════════════════════════════════════════════════════════════════════
class TestPureLogic:

    def test_hash_user_agent_stable(self):
        h1 = svc.hash_user_agent("Mozilla/5.0 (iPhone)")
        h2 = svc.hash_user_agent("Mozilla/5.0 (iPhone)")
        assert h1 == h2 and len(h1) == 64

    def test_hash_user_agent_empty(self):
        assert svc.hash_user_agent("") == ""
        assert svc.hash_user_agent(None) == ""

    def test_ua_short_sample_no_pii_explosion(self):
        long_ua = "X" * 200
        s = svc.ua_short_sample(long_ua)
        assert 0 < len(s) <= 40

    def test_compare_identical_is_match(self):
        binding = {"os_family": "android", "device_brand": "Samsung",
                    "dhcp_class_id": "android-dhcp-13",
                    "hostname": "galaxy-s22"}
        live = svc.AuthFingerprint(os_family="android", device_brand="Samsung",
                                    dhcp_class_id="android-dhcp-13",
                                    hostname="galaxy-s22")
        cmp_ = svc.compare(binding, live)
        assert cmp_.score == 0
        assert not cmp_.diverged
        assert cmp_.matched

    def test_compare_os_family_diverges_high_confidence(self):
        # إشارة حاسمة — تغيير OS بنفس MAC مستحيل عتاديًّا → ثقة عالية مباشرة.
        binding = {"os_family": "ios", "device_brand": "Apple"}
        live = svc.AuthFingerprint(os_family="android", device_brand="Samsung")
        cmp_ = svc.compare(binding, live)
        assert cmp_.confidence == "high"
        assert "os_family" in cmp_.diverged
        assert "device_brand" in cmp_.diverged
        assert cmp_.score >= 30

    def test_compare_ignores_empty_signals(self):
        # غياب معلومة في أحد الطرفين ≠ تباين (لا نعاقب على الفقر بالإشارات).
        binding = {"os_family": "android", "hostname": "phone1"}
        live = svc.AuthFingerprint(os_family="android")  # hostname غائب
        cmp_ = svc.compare(binding, live)
        assert "hostname" not in cmp_.diverged

    def test_compare_only_context_change_low(self):
        # تغيير NAS فقط (تجوال بين راوترين) → ثقة منخفضة بدون إنذار قاطع.
        binding = {"os_family": "android", "nas_ip": "10.0.0.1"}
        live = svc.AuthFingerprint(os_family="android", nas_ip="10.0.0.2")
        cmp_ = svc.compare(binding, live)
        assert "nas_ip" in cmp_.diverged
        assert cmp_.confidence in ("low", "medium")  # nas_ip وزنه 10 فقط
        assert not cmp_.is_clone(threshold="high")

    def test_verdict_is_clone_threshold(self):
        cmp_ = svc.Comparison(confidence="medium", score=40)
        assert cmp_.is_clone(threshold="low")
        assert cmp_.is_clone(threshold="medium")
        assert not cmp_.is_clone(threshold="high")


# ════════════════════════════════════════════════════════════════════════
# (2) كشف السياق المتباعد (impossible-travel) — دالة خالصة
# ════════════════════════════════════════════════════════════════════════
class TestDivergentContext:

    def _live(self, **kw):
        base = dict(nas_ip="10.0.0.1", called_station="AA:11:22:33:44:55")
        base.update(kw)
        return svc.AuthFingerprint(**base)

    def _sess(self, **kw):
        base = dict(radacctid=1, acctsessionid="s1", username="u",
                     nasipaddress="10.0.0.1", nasportid="1",
                     calledstationid="AA:11:22:33:44:55",
                     callingstationid="AA:BB:CC:DD:EE:FF",
                     framedipaddress="")
        base.update(kw)
        return svc.ConcurrentSession(**base)

    def test_same_nas_same_ap_not_divergent(self):
        assert not svc.is_divergent_context(self._sess(), self._live())

    def test_different_nas_is_divergent(self):
        s = self._sess(nasipaddress="10.0.0.2")
        assert svc.is_divergent_context(s, self._live())

    def test_same_nas_different_ap_is_divergent(self):
        s = self._sess(calledstationid="BB:11:22:33:44:55")
        assert svc.is_divergent_context(s, self._live())


# ════════════════════════════════════════════════════════════════════════
# (3) DB-backed — مع تطبيق Flask كامل
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "anti_mac_clone.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _mk_sub(username="u1", password="pw1", **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = dict(id=None, username=username, password=password, tenant_id=1,
                status="enabled")
    base.update(kw)
    return subscribers_repo.upsert_subscriber(Subscriber(**base))


# ════════════════════════════════════════════════════════════════════════
# (4) Repo round-trip
# ════════════════════════════════════════════════════════════════════════
class TestRepo:

    def test_normalize_mac_consistent(self):
        from app.radius.db.repos import mac_clone_repo
        assert mac_clone_repo.normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"

    def test_upsert_binding_creates_then_updates(self, app_ctx):
        from app.radius.db.repos import mac_clone_repo
        b1 = mac_clone_repo.upsert_binding(
            tenant_id=1, username="u1", mac="aa:bb:cc:dd:ee:ff",
            os_family="android", device_brand="Samsung")
        assert b1["status"] == "active"
        assert b1["os_family"] == "android"
        assert b1["verify_count"] == 0  # أوّل ربط لا يحسب verify

        b2 = mac_clone_repo.upsert_binding(
            tenant_id=1, username="u1", mac="AA-BB-CC-DD-EE-FF",
            hostname="galaxy-s22")
        assert b2["verify_count"] == 1
        assert b2["os_family"] == "android"   # لم يُكتب فوقها بقيمة فارغة
        assert b2["hostname"] == "galaxy-s22"

    def test_bump_mismatch(self, app_ctx):
        from app.radius.db.repos import mac_clone_repo
        mac_clone_repo.upsert_binding(tenant_id=1, username="u1",
                                       mac="AA:BB:CC:DD:EE:FF",
                                       os_family="android")
        mac_clone_repo.bump_mismatch(1, "u1", "AA:BB:CC:DD:EE:FF")
        mac_clone_repo.bump_mismatch(1, "u1", "AA:BB:CC:DD:EE:FF")
        b = mac_clone_repo.get_binding(1, "u1", "AA:BB:CC:DD:EE:FF")
        assert b["mismatch_count"] == 2

    def test_set_status_and_delete(self, app_ctx):
        from app.radius.db.repos import mac_clone_repo
        b = mac_clone_repo.upsert_binding(tenant_id=1, username="u1",
                                           mac="AA:BB:CC:DD:EE:FF")
        assert mac_clone_repo.set_binding_status(1, b["id"], "suspended")
        assert mac_clone_repo.get_binding(1, "u1",
                                           "AA:BB:CC:DD:EE:FF")["status"] == "suspended"
        assert mac_clone_repo.delete_binding(1, b["id"])
        assert mac_clone_repo.get_binding(1, "u1", "AA:BB:CC:DD:EE:FF") is None

    def test_log_event_round_trip(self, app_ctx):
        from app.radius.db.repos import mac_clone_repo
        eid = mac_clone_repo.log_event(
            tenant_id=1, username="u1", mac="AA:BB:CC:DD:EE:FF",
            event_type="clone_detected", decision="deny",
            confidence="high", score=82,
            signals={"diverged": ["os_family", "device_brand"]},
            nas_ip="10.0.0.1", reason="x")
        assert eid > 0
        events = mac_clone_repo.list_events(1, username="u1")
        assert len(events) == 1
        assert events[0]["confidence"] == "high"
        assert events[0]["signals_obj"]["diverged"] == ["os_family", "device_brand"]

    def test_count_events_by_type(self, app_ctx):
        from app.radius.db.repos import mac_clone_repo
        for ev in ("bind", "verify_ok", "verify_ok", "clone_detected"):
            mac_clone_repo.log_event(tenant_id=1, username="u1",
                                      mac="AA:BB:CC:DD:EE:FF", event_type=ev)
        counts = mac_clone_repo.count_events_by_type(1)
        assert counts["bind"] == 1
        assert counts["verify_ok"] == 2
        assert counts["clone_detected"] == 1


# ════════════════════════════════════════════════════════════════════════
# (5) النطاق (scope)
# ════════════════════════════════════════════════════════════════════════
class TestScope:

    def test_scope_all_default(self, app_ctx):
        ctx = svc.ScopeContext(plan_id=99, group="x")
        assert svc.scope_applies(1, ctx)

    def test_scope_plans(self, app_ctx):
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, svc.SK_SCOPE, "plans")
        tenants_repo.set_setting(1, svc.SK_SCOPE_PLAN_IDS, "3,7,11")
        assert svc.scope_applies(1, svc.ScopeContext(plan_id=7))
        assert not svc.scope_applies(1, svc.ScopeContext(plan_id=5))
        assert not svc.scope_applies(1, svc.ScopeContext(plan_id=None))

    def test_scope_groups(self, app_ctx):
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, svc.SK_SCOPE, "groups")
        tenants_repo.set_setting(1, svc.SK_SCOPE_GROUP_NAMES, "VIP,البرنزي")
        assert svc.scope_applies(1, svc.ScopeContext(group="VIP"))
        assert svc.scope_applies(1, svc.ScopeContext(group="البرنزي"))
        assert not svc.scope_applies(1, svc.ScopeContext(group="عادي"))


# ════════════════════════════════════════════════════════════════════════
# (6) set_settings — تطبيع/تحقّق
# ════════════════════════════════════════════════════════════════════════
class TestSettingsNormalization:

    def test_toggles_strict_boolean(self, app_ctx):
        changed = svc.set_settings(1, {svc.SK_ENABLED: "yes"})
        assert svc.SK_ENABLED in changed
        assert svc.is_enabled(1)
        svc.set_settings(1, {svc.SK_ENABLED: "0"})
        assert not svc.is_enabled(1)

    def test_invalid_mode_falls_back(self, app_ctx):
        # نضع قيمة سيّئة بعد قيمة صالحة لإجبار الكتابة (يمنع set_settings الكتابة
        # عند تطابق الجديد مع القديم — والقيمة المُطبَّعة هي الافتراضية هنا).
        svc.set_settings(1, {svc.SK_MODE: "monitor"})  # قيمة صالحة أولًا
        svc.set_settings(1, {svc.SK_MODE: "evil"})     # سيّئة → ترجع enforce
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, svc.SK_MODE, "") == "enforce"

    def test_plan_ids_strip_non_digits(self, app_ctx):
        svc.set_settings(1, {svc.SK_SCOPE_PLAN_IDS: "1, abc, 3 ,،5"})
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, svc.SK_SCOPE_PLAN_IDS, "") == "1,3,5"

    def test_raw_limit_clamped(self, app_ctx):
        svc.set_settings(1, {svc.SK_RAW_LIMIT: "99999"})
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, svc.SK_RAW_LIMIT, "") == "2000"
        svc.set_settings(1, {svc.SK_RAW_LIMIT: "1"})
        assert tenants_repo.get_setting(1, svc.SK_RAW_LIMIT, "") == "10"


# ════════════════════════════════════════════════════════════════════════
# (7) الإنفاذ في policy_engine
# ════════════════════════════════════════════════════════════════════════
class TestPolicyEngineEnforcement:

    def _auth(self, **kw):
        from app.radius.services.policy_engine import AuthRequest, authorize
        base = dict(username="u1", password="pw1", tenant_id=1,
                     calling_station_id="AA:BB:CC:DD:EE:FF",
                     called_station_id="0C:11:22:33:44:55",
                     nas_ip="10.0.0.1", nas_port="ether1",
                     nas_port_type="Wireless-802.11")
        base.update(kw)
        return authorize(AuthRequest(**base))

    def test_feature_off_no_effect(self, app_ctx):
        _mk_sub()
        d = self._auth()
        assert d.ok
        # لا binding يُكتب طالما الميزة مغلقة
        from app.radius.db.repos import mac_clone_repo
        assert mac_clone_repo.get_binding(1, "u1", "AA:BB:CC:DD:EE:FF") is None

    def test_first_login_creates_binding(self, app_ctx):
        _mk_sub()
        from app.radius.db.repos import tenants_repo, mac_clone_repo
        tenants_repo.set_setting(1, svc.SK_ENABLED, "1")
        d = self._auth()
        assert d.ok
        b = mac_clone_repo.get_binding(1, "u1", "AA:BB:CC:DD:EE:FF")
        assert b is not None
        assert b["status"] == "active"
        assert b["nas_ip"] == "10.0.0.1"

    def test_second_login_same_context_passes_silently(self, app_ctx):
        _mk_sub()
        from app.radius.db.repos import tenants_repo, mac_clone_repo
        tenants_repo.set_setting(1, svc.SK_ENABLED, "1")
        self._auth()
        self._auth()
        b = mac_clone_repo.get_binding(1, "u1", "AA:BB:CC:DD:EE:FF")
        assert b["verify_count"] >= 1  # تحقّق ناجح زاد العدّاد
        events = mac_clone_repo.list_events(1, username="u1")
        types = {e["event_type"] for e in events}
        assert "bind" in types
        assert "verify_ok" in types

    def test_clone_detected_in_enforce_rejects(self, app_ctx, monkeypatch):
        """نمط enforce: بعد ربط جهاز iOS، دخول لاحق ببصمة Android على نفس MAC =
        رفض + سجلّ clone_detected."""
        _mk_sub()
        from app.radius.db.repos import tenants_repo, device_fingerprints_repo, \
            mac_clone_repo
        tenants_repo.set_setting(1, svc.SK_ENABLED, "1")
        # نضع device fingerprint لـiOS فيلتقطه build_fingerprint.
        device_fingerprints_repo.upsert(
            tenant_id=1, mac="AA:BB:CC:DD:EE:FF",
            os_family="ios", device_brand="Apple", hostname="iphone-of-x")
        d1 = self._auth()
        assert d1.ok  # ربط أوّل
        # نُبدّل البصمة الحيّة إلى Android (الجهاز الجديد بنفس MAC).
        device_fingerprints_repo.upsert(
            tenant_id=1, mac="AA:BB:CC:DD:EE:FF",
            os_family="android", device_brand="Samsung", hostname="galaxy")
        d2 = self._auth()
        assert not d2.ok
        assert d2.reason == "mac_clone_detected"
        # العدّاد يرتفع، لا يُكتب فوق البصمة الشرعية.
        b = mac_clone_repo.get_binding(1, "u1", "AA:BB:CC:DD:EE:FF")
        assert b["mismatch_count"] >= 1
        assert b["os_family"] == "ios"
        events = mac_clone_repo.list_events(1, username="u1",
                                             event_type="clone_detected")
        assert events

    def test_clone_detected_in_monitor_allows(self, app_ctx):
        """نمط monitor: نفس السيناريو لكن لا رفض — السجلّ فقط."""
        _mk_sub()
        from app.radius.db.repos import tenants_repo, device_fingerprints_repo, \
            mac_clone_repo
        tenants_repo.set_setting(1, svc.SK_ENABLED, "1")
        tenants_repo.set_setting(1, svc.SK_MODE, "monitor")
        device_fingerprints_repo.upsert(
            tenant_id=1, mac="AA:BB:CC:DD:EE:FF",
            os_family="ios", device_brand="Apple")
        d1 = self._auth()
        assert d1.ok
        device_fingerprints_repo.upsert(
            tenant_id=1, mac="AA:BB:CC:DD:EE:FF",
            os_family="android", device_brand="Samsung")
        d2 = self._auth()
        assert d2.ok  # لا رفض في monitor
        events = mac_clone_repo.list_events(1, username="u1",
                                             event_type="clone_detected")
        assert events  # لكن السجلّ موجود

    def test_scope_off_skips_user(self, app_ctx):
        """عند تحديد scope=groups + قائمة لا تحوي مجموعة المشترك → الميزة
        مُسكَتة لهذا المستخدم: لا رفض حتى مع وجود تباين بصمة."""
        _mk_sub(group="عادي")
        from app.radius.db.repos import tenants_repo, device_fingerprints_repo
        tenants_repo.set_setting(1, svc.SK_ENABLED, "1")
        tenants_repo.set_setting(1, svc.SK_SCOPE, "groups")
        tenants_repo.set_setting(1, svc.SK_SCOPE_GROUP_NAMES, "VIP,الذهبي")
        device_fingerprints_repo.upsert(
            tenant_id=1, mac="AA:BB:CC:DD:EE:FF",
            os_family="ios", device_brand="Apple")
        self._auth()
        device_fingerprints_repo.upsert(
            tenant_id=1, mac="AA:BB:CC:DD:EE:FF",
            os_family="android", device_brand="Samsung")
        d2 = self._auth()
        # لا رفض — الميزة خارج النطاق لهذا المشترك
        assert d2.ok

    def test_random_mac_no_binding(self, app_ctx):
        """عناوين MAC العشوائية تُعالَج بمسار آخر (random_mac_blocked)؛ نتأكّد
        أنّ الميزة لا تكسر السيناريو ولا تَلْزَم binding للعشوائي."""
        # ميزة منع MAC العشوائي مغلقة هنا — anti-mac-clone لا تعتمد عليها.
        _mk_sub()
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, svc.SK_ENABLED, "1")
        d = self._auth(calling_station_id="")  # بلا MAC = لا فحص ولا binding
        assert d.ok


# ════════════════════════════════════════════════════════════════════════
# (8) AlertSpec موجودة + ACTION_ALERTS deep-link
# ════════════════════════════════════════════════════════════════════════
class TestAlertWiring:

    def test_admin_alerts_catalogue_has_mac_clone(self):
        from app.radius.services import admin_alerts
        keys = {a.key for a in admin_alerts.ALERTS}
        assert "mac_clone_detected" in keys
        spec = admin_alerts.get_spec("mac_clone_detected")
        assert spec is not None
        # القالب يستخدم الحقول الجديدة دون كسر التصيير.
        text = admin_alerts.preview("mac_clone_detected")
        assert "كشف استنساخ MAC" in text

    def test_action_alerts_includes_mac_clone(self):
        from app.radius.services.alert_links import ACTION_ALERTS
        assert "mac_clone_detected" in ACTION_ALERTS


# ════════════════════════════════════════════════════════════════════════
# (9) راوتات الإدارة (smoke)
# ════════════════════════════════════════════════════════════════════════
class TestAdminRoutes:

    def test_page_get_renders(self, app_ctx):
        client = app_ctx.test_client()
        with client.session_transaction() as s:
            s["admin_id"] = 1
            s["admin_user"] = "admin"
            s["is_super_admin"] = True
        rv = client.get("/admin/radius/anti-mac-clone")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        assert "منع استنساخ MAC" in body

    def test_save_settings_persists(self, app_ctx):
        client = app_ctx.test_client()
        with client.session_transaction() as s:
            s["admin_id"] = 1
            s["admin_user"] = "admin"
            s["is_super_admin"] = True
        # GET أوّلًا لتوليد _csrf_token في الجلسة (CSRF عام).
        client.get("/admin/radius/anti-mac-clone")
        with client.session_transaction() as s:
            token = s.get("_csrf_token") or ""
        assert token, "CSRF token لم يُولَّد"
        rv = client.post("/admin/radius/anti-mac-clone/settings", data={
            "_csrf_token": token,
            svc.SK_ENABLED: "1",
            svc.SK_MODE: "monitor",
            svc.SK_CONFIDENCE_MIN: "medium",
            svc.SK_SCOPE: "all",
            svc.SK_CONCURRENT_GUARD: "1",
            svc.SK_ALERT_ENABLED: "1",
            svc.SK_COA_DISCONNECT: "1",
            svc.SK_RAW_LIMIT: "300",
        }, follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert svc.is_enabled(1)
        from app.radius.db.repos import tenants_repo
        assert tenants_repo.get_setting(1, svc.SK_MODE, "") == "monitor"
