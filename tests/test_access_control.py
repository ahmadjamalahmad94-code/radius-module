"""feat/access-control-blocking — اختبارات «الحظر والتحكم بالدخول».

تغطّي:
  * منطق نمط المدّة الثلاثي (permanent / daily_window / until) ونافذة العبور.
  * مطابقة كل نطاق (subscriber/group/plan/card_batch/ALL_*/ip/mac).
  * الإنفاذ في policy_engine (رفض المحظور، قبول الطبيعي).
  * الحظر التلقائي بعد N محاولات فاشلة + منع التكرار + التعطيل.
  * الانتهاء التلقائي (until) + رفع الحظر (clear).

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from app.radius.services import access_control as ac


# ════════════════════════════════════════════════════════════════════════
# (1) منطق المدّة — دوال خالصة (لا DB)
# ════════════════════════════════════════════════════════════════════════
class TestDurationLogic:

    def test_daily_window_same_day(self):
        assert ac.in_daily_window("08:00", "16:00", datetime(2026, 6, 15, 10, 0))
        assert not ac.in_daily_window("08:00", "16:00", datetime(2026, 6, 15, 17, 0))

    def test_daily_window_wraps_midnight(self):
        # 16:00 → 08:00 (يشمل الليل)
        assert ac.in_daily_window("16:00", "08:00", datetime(2026, 6, 15, 23, 0))
        assert ac.in_daily_window("16:00", "08:00", datetime(2026, 6, 15, 3, 0))
        assert not ac.in_daily_window("16:00", "08:00", datetime(2026, 6, 15, 12, 0))

    def test_daily_window_equal_is_24h(self):
        assert ac.in_daily_window("00:00", "00:00", datetime(2026, 6, 15, 12, 0))

    def test_daily_window_invalid_defaults_in_effect(self):
        assert ac.in_daily_window("", "", datetime(2026, 6, 15, 12, 0))

    def test_is_in_effect_permanent(self):
        b = {"active": 1, "duration_mode": "permanent"}
        assert ac.is_block_in_effect(b, datetime(2026, 6, 15, 12, 0))

    def test_is_in_effect_inactive_false(self):
        b = {"active": 0, "duration_mode": "permanent"}
        assert not ac.is_block_in_effect(b)

    def test_is_in_effect_until_before_and_after(self):
        future = (datetime(2026, 6, 15, 12, 0) + timedelta(hours=1)).isoformat()
        past = (datetime(2026, 6, 15, 12, 0) - timedelta(hours=1)).isoformat()
        now = datetime(2026, 6, 15, 12, 0)
        assert ac.is_block_in_effect(
            {"active": 1, "duration_mode": "until", "expires_at": future}, now)
        assert not ac.is_block_in_effect(
            {"active": 1, "duration_mode": "until", "expires_at": past}, now)

    def test_is_in_effect_daily_window(self):
        now_in = datetime(2026, 6, 15, 23, 0)
        now_out = datetime(2026, 6, 15, 12, 0)
        b = {"active": 1, "duration_mode": "daily_window",
             "window_start": "16:00", "window_end": "08:00"}
        assert ac.is_block_in_effect(b, now_in)
        assert not ac.is_block_in_effect(b, now_out)


# ════════════════════════════════════════════════════════════════════════
# (2) مطابقة النطاق — دوال خالصة
# ════════════════════════════════════════════════════════════════════════
class TestScopeMatching:

    def _ctx(self, **kw):
        base = dict(source="subscriber", username="u1", group="vip", plan_id=7,
                    card_batch_id=3, service_type="Hotspot", nas_ip="1.2.3.4",
                    mac="AA:BB:CC:DD:EE:FF")
        base.update(kw)
        return ac.AuthContext(**base)

    def test_subscriber(self):
        assert ac.block_matches({"block_type": "subscriber", "target": "u1"}, self._ctx())
        assert not ac.block_matches({"block_type": "subscriber", "target": "u2"}, self._ctx())

    def test_group_plan_batch(self):
        assert ac.block_matches({"block_type": "group", "target": "vip"}, self._ctx())
        assert ac.block_matches({"block_type": "plan", "target": "7"}, self._ctx())
        assert ac.block_matches({"block_type": "card_batch", "target": "3"}, self._ctx())
        assert not ac.block_matches({"block_type": "plan", "target": "9"}, self._ctx())

    def test_all_scopes(self):
        assert ac.block_matches({"block_type": "all_subscribers", "target": ""}, self._ctx())
        assert not ac.block_matches({"block_type": "all_cards", "target": ""}, self._ctx())
        assert ac.block_matches({"block_type": "all_cards", "target": ""},
                                self._ctx(source="card"))
        assert ac.block_matches({"block_type": "all_hotspot", "target": ""}, self._ctx())
        assert ac.block_matches({"block_type": "all_pppoe", "target": ""},
                                self._ctx(service_type="PPPoE"))

    def test_ip_and_mac(self):
        assert ac.block_matches({"block_type": "ip", "target": "1.2.3.4"}, self._ctx())
        # MAC تطابق بصرف النظر عن الصيغة (شرطات/حالة أحرف)
        assert ac.block_matches({"block_type": "mac", "target": "aa-bb-cc-dd-ee-ff"}, self._ctx())
        assert not ac.block_matches({"block_type": "ip", "target": "9.9.9.9"}, self._ctx())

    def test_normalize_mac(self):
        assert ac.normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"

    def test_layer_of(self):
        # النطاقات النطاقية = تعليق؛ IP/MAC = حظر
        for bt in ("subscriber", "group", "plan", "card_batch",
                   "all_subscribers", "all_hotspot", "all_cards", "all_pppoe"):
            assert ac.layer_of(bt) == ac.LAYER_SUSPENSION
        assert ac.layer_of("ip") == ac.LAYER_BLOCK
        assert ac.layer_of("mac") == ac.LAYER_BLOCK

    def test_user_message_for(self):
        # تعليق: رسالة مهذّبة بحسب النمط + إلحاق السبب
        m = ac.user_message_for({"block_type": "subscriber",
                                 "duration_mode": "permanent", "reason": "صيانة"})
        assert m.startswith(ac.MSG_SUSPENDED) and "صيانة" in m
        assert ac.user_message_for({"block_type": "subscriber",
                                    "duration_mode": "daily_window"}) == ac.MSG_SUSPENDED_WINDOW
        assert ac.user_message_for({"block_type": "subscriber",
                                    "duration_mode": "until"}) == ac.MSG_SUSPENDED_UNTIL
        # حظر: رسالة أمنية عامّة لا تُلحق سبب المشغّل
        assert ac.user_message_for({"block_type": "ip",
                                    "reason": "internal"}) == ac.MSG_BLOCKED

    def test_daily_window_applies_tz_offset(self):
        # نافذة 16:00→08:00 محلّية، إزاحة +3. الساعة 20:00 محلّي = 17:00 UTC.
        b = {"active": 1, "duration_mode": "daily_window",
             "window_start": "16:00", "window_end": "08:00"}
        now_utc = datetime(2026, 6, 15, 17, 0)        # = 20:00 محلّي (+3) → داخل
        assert ac.is_block_in_effect(b, now_utc, tz_offset_hours=3)
        # 12:00 UTC = 15:00 محلّي → خارج النافذة (تبدأ 16:00)
        assert not ac.is_block_in_effect(b, datetime(2026, 6, 15, 12, 0), tz_offset_hours=3)


# ════════════════════════════════════════════════════════════════════════
# (3) DB-backed
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "access_control.db")
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


def _ctx(**kw):
    base = dict(username="u1")
    base.update(kw)
    return ac.AuthContext(**base)


class TestRepoAndFind:

    def test_create_list_clear(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        bid = repo.create_block(tenant_id=1, block_type="subscriber", target="u1",
                                reason="test")
        assert any(b["id"] == bid for b in repo.list_blocks(1))
        assert repo.clear_block(1, bid, by=5)
        b = repo.get_block(1, bid)
        assert b["active"] == 0 and b["cleared_by"] == 5
        # رفع ثانٍ لا يغيّر شيئًا
        assert not repo.clear_block(1, bid)

    def test_list_filters_by_layer(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        repo.create_block(tenant_id=1, layer="suspension", block_type="subscriber", target="u1")
        repo.create_block(tenant_id=1, layer="block", block_type="ip", target="1.2.3.4")
        sus = repo.list_blocks(1, layer="suspension")
        blk = repo.list_blocks(1, layer="block")
        assert len(sus) == 1 and sus[0]["block_type"] == "subscriber"
        assert len(blk) == 1 and blk[0]["block_type"] == "ip"
        # create_block_from_input يشتقّ الطبقة تلقائيًا من النوع
        ac.create_block_from_input(tenant_id=1, block_type="mac",
                                   target="AA:BB:CC:DD:EE:FF")
        assert len(repo.list_blocks(1, layer="block")) == 2

    def test_find_active_block_matches_subscriber(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        repo.create_block(tenant_id=1, block_type="subscriber", target="u1")
        assert ac.find_active_block(1, _ctx(username="u1")) is not None
        assert ac.find_active_block(1, _ctx(username="other")) is None

    def test_find_ignores_cleared(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        bid = repo.create_block(tenant_id=1, block_type="ip", target="5.5.5.5")
        assert ac.find_active_block(1, _ctx(nas_ip="5.5.5.5")) is not None
        repo.clear_block(1, bid)
        assert ac.find_active_block(1, _ctx(nas_ip="5.5.5.5")) is None

    def test_until_auto_expires(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
        bid = repo.create_block(tenant_id=1, block_type="mac", target="AA:BB:CC:DD:EE:FF",
                                duration_mode="until", expires_at=past)
        # الإنفاذ لا يُعيد المنتهي (يُحتسب منطقيًّا) دون كتابة في DB
        assert ac.find_active_block(1, _ctx(mac="AA:BB:CC:DD:EE:FF")) is None
        assert repo.get_block(1, bid)["active"] == 1   # لم يُكنس بعد (مسار auth لا يكتب)
        # الكنس الصريح (صفحة الإدارة/مجدوِل) يعطّله
        assert repo.deactivate_expired(1) == 1
        assert repo.get_block(1, bid)["active"] == 0

    def test_daily_window_in_vs_out(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        repo.create_block(tenant_id=1, block_type="subscriber", target="u1",
                          duration_mode="daily_window", window_start="16:00",
                          window_end="08:00")
        in_window = datetime(2026, 6, 15, 23, 0)
        out_window = datetime(2026, 6, 15, 12, 0)
        assert ac.find_active_block(1, _ctx(username="u1"), now=in_window) is not None
        assert ac.find_active_block(1, _ctx(username="u1"), now=out_window) is None


class TestEnforcementInPolicyEngine:

    def _authorize(self, username="u1", password="pw1", nas_ip="", mac=""):
        from app.radius.services.policy_engine import AuthRequest, authorize
        return authorize(AuthRequest(username=username, password=password,
                                     tenant_id=1, nas_ip=nas_ip,
                                     calling_station_id=mac))

    def test_normal_subscriber_unaffected(self, app_ctx):
        _mk_sub()
        d = self._authorize()
        assert d.ok is True

    def test_suspended_subscriber_rejected_with_message(self, app_ctx):
        # نطاق المشترك = طبقة «تعليق الوصول» → reason=access_suspended + رسالة
        # مهذّبة تُحمَل في Reply-Message وتشمل سبب المشغّل.
        from app.radius.db.repos import access_blocks_repo as repo
        _mk_sub()
        repo.create_block(tenant_id=1, layer="suspension", block_type="subscriber",
                          target="u1", reason="صيانة مجدولة")
        d = self._authorize()
        assert d.ok is False and d.reason == "access_suspended"
        assert "معلّق" in d.message and "صيانة مجدولة" in d.message
        assert d.reply_attrs.get("Reply-Message") == d.message

    def test_all_subscribers_scope_is_suspension(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        _mk_sub()
        repo.create_block(tenant_id=1, layer="suspension", block_type="all_subscribers")
        assert self._authorize().reason == "access_suspended"

    def test_daily_window_message(self, app_ctx):
        # نافذة يومية → رسالة «لا يمكن تسجيل الدخول بهذا الوقت» (حين السريان).
        from app.radius.db.repos import access_blocks_repo as repo
        _mk_sub()
        repo.create_block(tenant_id=1, layer="suspension", block_type="subscriber",
                          target="u1", duration_mode="daily_window",
                          window_start="00:00", window_end="00:00")  # 24س
        d = self._authorize()
        assert d.reason == "access_suspended"
        assert d.message == ac.MSG_SUSPENDED_WINDOW

    def test_ip_scope_is_block(self, app_ctx):
        # IP/MAC = طبقة «الحظر» الأمني → reason=access_blocked برسالة عامّة.
        from app.radius.db.repos import access_blocks_repo as repo
        _mk_sub()
        repo.create_block(tenant_id=1, layer="block", block_type="ip", target="9.8.7.6")
        d = self._authorize(nas_ip="9.8.7.6")
        assert d.reason == "access_blocked" and d.message == ac.MSG_BLOCKED
        # IP مختلف غير متأثّر
        assert self._authorize(nas_ip="1.1.1.1").ok is True

    def test_mac_scope_is_block(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        _mk_sub()
        repo.create_block(tenant_id=1, layer="block", block_type="mac",
                          target="AA:BB:CC:DD:EE:FF")
        assert self._authorize(mac="aa:bb:cc:dd:ee:ff").reason == "access_blocked"

    def test_clear_unblocks(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        _mk_sub()
        bid = repo.create_block(tenant_id=1, layer="suspension",
                                block_type="subscriber", target="u1")
        assert self._authorize().reason == "access_suspended"
        repo.clear_block(1, bid)
        assert self._authorize().ok is True


class TestAutoBlockFail2ban:

    def _enable(self, threshold=3, window=300, duration=60, target="ip"):
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_ENABLED, "1")
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_THRESHOLD, str(threshold))
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_WINDOW_SEC, str(window))
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_DURATION_MIN, str(duration))
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_TARGET, target)

    def test_disabled_no_block(self, app_ctx):
        # افتراضيًا معطّل → لا حظر مهما تكرّر
        for _ in range(5):
            ac.register_failed_attempt(1, ip="2.2.2.2")
        assert ac.find_active_block(1, _ctx(username="x", nas_ip="2.2.2.2")) is None

    def test_auto_block_after_threshold(self, app_ctx):
        self._enable(threshold=3)
        assert ac.register_failed_attempt(1, ip="3.3.3.3") is None   # 1
        assert ac.register_failed_attempt(1, ip="3.3.3.3") is None   # 2
        bid = ac.register_failed_attempt(1, ip="3.3.3.3")            # 3 → block
        assert bid is not None
        blk = ac.find_active_block(1, _ctx(username="x", nas_ip="3.3.3.3"))
        assert blk is not None and blk["source"] == "auto" and blk["duration_mode"] == "until"

    def test_no_duplicate_auto_block(self, app_ctx):
        self._enable(threshold=2)
        ac.register_failed_attempt(1, ip="4.4.4.4")
        first = ac.register_failed_attempt(1, ip="4.4.4.4")          # creates
        second = ac.register_failed_attempt(1, ip="4.4.4.4")         # already blocked
        assert first is not None and second is None
        from app.radius.db.repos import access_blocks_repo as repo
        active = [b for b in repo.list_blocks(1, active_only=True)
                  if b["block_type"] == "ip" and b["target"] == "4.4.4.4"]
        assert len(active) == 1

    def test_auto_block_via_full_auth_path(self, app_ctx):
        """المسار الكامل: محاولات كلمة مرور خاطئة عبر authorize تُراكم حتى
        الحظر التلقائي، فتُرفض المحاولة التالية بـaccess_blocked."""
        from app.radius.services.policy_engine import AuthRequest, authorize
        self._enable(threshold=3, target="ip")
        _mk_sub(username="victim", password="right")
        for _ in range(3):
            d = authorize(AuthRequest(username="victim", password="wrong",
                                      tenant_id=1, nas_ip="7.7.7.7"))
            assert d.reason == "password_wrong"
        # الآن الـIP محظور تلقائيًا → حتى كلمة المرور الصحيحة تُرفض
        d = authorize(AuthRequest(username="victim", password="right",
                                  tenant_id=1, nas_ip="7.7.7.7"))
        assert d.reason == "access_blocked"

    def test_count_window_excludes_old(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        # سجّل محاولة قديمة يدويًا ثم عُدّ ضمن نافذة قصيرة → لا تُحتسب
        old = (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z"
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("INSERT INTO login_failure_tracker(tenant_id, ip, mac, username, created_at) "
                         "VALUES(1,'8.8.8.8','','x',?)", (old,))
        since = (datetime.utcnow() - timedelta(seconds=60)).isoformat() + "Z"
        assert repo.count_recent_failures(1, ip="8.8.8.8", since=since) == 0


class TestFail2banOnlyCountsAuthFailures:
    """FINDING 1: عدّاد fail2ban يُحسب فقط على فشل مصادقة حقيقي (كلمة مرور
    خاطئة / مستخدم غير موجود) — لا على رفض السياسة/التفويض، كي لا يَحظر
    مستخدم شرعي نفسه (مثل concurrent_limit بسبب واي‑فاي متقطّع)."""

    def _enable(self, threshold=2, target="ip"):
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_ENABLED, "1")
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_THRESHOLD, str(threshold))
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_WINDOW_SEC, "300")
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_DURATION_MIN, "60")
        tenants_repo.set_setting(1, ac.SK_AUTOBLOCK_TARGET, target)

    def _authorize(self, **kw):
        from app.radius.services.policy_engine import AuthRequest, authorize
        return authorize(AuthRequest(tenant_id=1, **kw))

    def _autoblocked(self, ip):
        return ac.find_active_block(1, _ctx(username="z", nas_ip=ip)) is not None

    def test_expired_does_not_autoblock(self, app_ctx):
        self._enable(threshold=2, target="ip")
        _mk_sub(username="exp", password="pw", status="expired")
        for _ in range(4):
            d = self._authorize(username="exp", password="pw", nas_ip="20.0.0.1")
            # قمع «انتهى اشتراكك» يعيد expired_captive (قبول مقيّد نحو بوابة
            # التجديد) بدل الرفض الصريح expired — كلاهما «سياسة» لا فشل مصادقة.
            assert d.reason in ("expired", "expired_captive")
        assert not self._autoblocked("20.0.0.1")     # رفض سياسة → لا حظر تلقائي

    def test_concurrent_limit_does_not_autoblock(self, app_ctx):
        self._enable(threshold=2, target="ip")
        _mk_sub(username="conc", password="pw", override_concurrent=1)
        # جلسة مفتوحة واحدة → الحدّ (1) متجاوَز عند كل محاولة لاحقة
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("INSERT INTO radacct(tenant_id, username) VALUES(1, 'conc')")
        for _ in range(4):
            d = self._authorize(username="conc", password="pw", nas_ip="20.0.0.2")
            assert d.reason == "concurrent_limit"
        assert not self._autoblocked("20.0.0.2")

    def test_quota_exhausted_does_not_autoblock(self, app_ctx):
        # باقة بكوتا صغيرة + استهلاك يتجاوزها → quota_exhausted (رفض سياسة)
        from app.radius.db.repos import plans_repo
        from app.radius.core.types import AccessPlan
        self._enable(threshold=2, target="ip")
        plan = plans_repo.upsert_plan(AccessPlan(
            id=None, name="tiny", tenant_id=1, quota_total_mb=1, limit_type="Data_Limit"))
        _mk_sub(username="q", password="pw", plan_id=plan.id,
                combined_quota_mb=1, quota_limit_enabled=True,
                used_bytes_in=5 * 1024 * 1024, used_bytes_out=5 * 1024 * 1024)
        for _ in range(4):
            d = self._authorize(username="q", password="pw", nas_ip="20.0.0.3")
            assert d.reason == "quota_exhausted"
        assert not self._autoblocked("20.0.0.3")

    def test_bad_password_does_autoblock(self, app_ctx):
        # المقابل: كلمة مرور خاطئة فشل مصادقة حقيقي → يُحظر عند العتبة
        self._enable(threshold=3, target="ip")
        _mk_sub(username="bp", password="right")
        for _ in range(3):
            d = self._authorize(username="bp", password="wrong", nas_ip="20.0.0.4")
            assert d.reason == "password_wrong"
        assert self._autoblocked("20.0.0.4")

    def test_unknown_user_does_autoblock(self, app_ctx):
        # مستخدم غير موجود أيضًا فشل مصادقة (brute-force أسماء)
        self._enable(threshold=2, target="ip")
        for _ in range(2):
            d = self._authorize(username="ghost", password="x", nas_ip="20.0.0.5")
            assert d.reason == "user_not_found"
        assert self._autoblocked("20.0.0.5")

    def test_allow_list_is_exactly_auth_failures(self):
        from app.radius.services import policy_engine as pe
        assert pe._FAIL2BAN_REASONS == frozenset({"password_wrong", "user_not_found"})


class TestSchemaHealLayer:
    """FINDING 2: قاعدة طبّقت migration 123 قبل عمود layer تُشفى ذاتيًّا."""

    def test_ensure_schema_adds_missing_layer(self, app_ctx):
        from app.radius.db.connection import db, transaction
        from app.radius.db.repos import access_blocks_repo as repo
        # حاكِ حالة ما قبل العمود: أعد بناء الجدول بلا عمود layer
        with transaction() as conn:
            conn.execute("DROP TABLE access_blocks")
            conn.execute("""
                CREATE TABLE access_blocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    block_type TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    duration_mode TEXT NOT NULL DEFAULT 'permanent',
                    window_start TEXT NOT NULL DEFAULT '',
                    window_end TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    cleared_at TEXT NOT NULL DEFAULT '',
                    cleared_by INTEGER NOT NULL DEFAULT 0
                )""")
            conn.execute("INSERT INTO access_blocks(tenant_id, block_type, target) "
                         "VALUES(1, 'ip', '9.9.9.9')")
            conn.execute("INSERT INTO access_blocks(tenant_id, block_type, target) "
                         "VALUES(1, 'subscriber', 'u1')")
        cols = {r["name"] for r in db().execute("PRAGMA table_info(access_blocks)").fetchall()}
        assert "layer" not in cols                       # محاكاة ما قبل العمود
        # الشفاء الذاتي
        repo.ensure_schema()
        cols = {r["name"] for r in db().execute("PRAGMA table_info(access_blocks)").fetchall()}
        assert "layer" in cols
        # الصفوف القديمة صُحّحت: IP=block، subscriber=suspension (الافتراضي)
        rows = {r["block_type"]: r["layer"]
                for r in db().execute("SELECT block_type, layer FROM access_blocks").fetchall()}
        assert rows["ip"] == "block" and rows["subscriber"] == "suspension"
        # ممتنع التكرار: استدعاء ثانٍ لا يفشل
        repo.ensure_schema()
        # القراءة عبر الـrepo تعمل بعد الشفاء
        assert any(b["block_type"] == "ip" for b in repo.list_blocks(1, layer="block"))

    def test_ensure_schema_noop_when_present(self, app_ctx):
        # على قاعدة حديثة (العمود موجود) لا يفعل شيئًا ولا يفشل
        from app.radius.db.repos import access_blocks_repo as repo
        repo.ensure_schema()
        repo.create_block(tenant_id=1, layer="block", block_type="ip", target="1.1.1.1")
        assert repo.list_blocks(1, layer="block")


class TestRoutePage:
    """يرندر صفحة الإدارة فعليًّا ويختبر إضافة/رفع حظر عبر test client.
    الصفحة محروسة بـsettings.view/edit؛ نضبط جلسة سوبر لتجاوز RBAC."""

    def _client(self, app_ctx):
        c = app_ctx.test_client()
        with c.session_transaction() as s:
            s["tenant_id"] = 1
            s["admin_id"] = 1
            s["is_super_admin"] = True
            s["_csrf_token"] = "tok"
        return c

    def test_page_renders(self, app_ctx):
        c = self._client(app_ctx)
        html = c.get("/admin/radius/access-control").get_data(as_text=True)
        assert "التحكم بالدخول" in html
        assert "تعليق الوصول" in html          # الطبقة A
        assert 'id="sus-form"' in html         # نموذج التعليق
        assert 'id="blk-form"' in html         # نموذج الحظر

    def test_add_and_clear_via_routes(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        c = self._client(app_ctx)
        r = c.post("/admin/radius/access-control/block",
                   data={"block_type": "subscriber", "target": "u1",
                         "duration_mode": "permanent", "reason": "abuse",
                         "_csrf_token": "tok"})
        assert r.status_code in (302, 303)
        blocks = repo.list_blocks(1, active_only=True)
        assert len(blocks) == 1 and blocks[0]["target"] == "u1"
        bid = blocks[0]["id"]
        r2 = c.post(f"/admin/radius/access-control/block/{bid}/clear",
                    data={"_csrf_token": "tok"})
        assert r2.status_code in (302, 303)
        assert repo.list_blocks(1, active_only=True) == []

    def test_invalid_block_rejected(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        c = self._client(app_ctx)
        # نمط until بلا تاريخ → خطأ تحقّق، لا يُنشأ حظر
        c.post("/admin/radius/access-control/block",
               data={"block_type": "ip", "target": "1.2.3.4",
                     "duration_mode": "until", "expires_at": "",
                     "_csrf_token": "tok"})
        assert repo.list_blocks(1, active_only=True) == []

    def test_invalid_ip_rejected(self, app_ctx):
        from app.radius.db.repos import access_blocks_repo as repo
        with pytest.raises(ac.AccessControlError):
            ac.create_block_from_input(tenant_id=1, block_type="ip", target="300.300.300.300")
        # IP صالح يمرّ
        ac.create_block_from_input(tenant_id=1, block_type="ip", target="10.0.0.1")
        assert any(b["target"] == "10.0.0.1" for b in repo.list_blocks(1))

    def test_until_normalized_to_utc(self, app_ctx):
        from app.radius.db.repos import tenants_repo, access_blocks_repo as repo
        tenants_repo.set_setting(1, "billing.timezone_offset", "3")
        # مُدخَل محلّي 14:00 بإزاحة +3 → يُخزَّن 11:00Z
        bid = ac.create_block_from_input(tenant_id=1, block_type="subscriber",
                                         target="u9", duration_mode="until",
                                         expires_at="2026-06-20T14:00")
        stored = repo.get_block(1, bid)["expires_at"]
        assert stored.startswith("2026-06-20T11:00") and stored.endswith("Z")

    def test_save_settings(self, app_ctx):
        from app.radius.db.repos import tenants_repo
        c = self._client(app_ctx)
        c.post("/admin/radius/access-control/settings",
               data={ac.SK_AUTOBLOCK_ENABLED: "1", ac.SK_AUTOBLOCK_THRESHOLD: "7",
                     ac.SK_AUTOBLOCK_WINDOW_SEC: "120", ac.SK_AUTOBLOCK_DURATION_MIN: "30",
                     ac.SK_AUTOBLOCK_TARGET: "both",
                     "security.block_random_mac_subscribers": "1",
                     "_csrf_token": "tok"})
        assert tenants_repo.get_setting(1, ac.SK_AUTOBLOCK_ENABLED, "0") == "1"
        assert tenants_repo.get_setting(1, ac.SK_AUTOBLOCK_THRESHOLD, "5") == "7"
        assert tenants_repo.get_setting(1, "security.block_random_mac_subscribers", "0") == "1"
