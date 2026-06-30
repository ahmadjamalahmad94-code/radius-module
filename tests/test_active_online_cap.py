"""feat/active-online-cap — اختبارات سقف «اكتف» (concurrent online) في policy_engine.

تعريف المالك: «اكتف» = إجمالي الجلسات المفتوحة عبر كل أنواع الاتصال
(cards + subscribers + PPPoE + hotspot). كل جلسة حيّة في radacct = 1.
السقف من العقد (limits.active_online.max). يُفرَض auth-time في policy_engine.

تَغطّي:
  • get_active_online_cap: يَقرأ من active_online.max و البدائل
  • count_active_sessions: يَعدّ كل أنواع الجلسات في radacct
  • policy_engine: at-cap reject, below-cap allow, mixed types, re-auth,
    unlimited (None/0/-1), no contract
  • النظام لا يَكسر authentication عند خطأ provider (fail-safe)
  • subscribers لم يَعد سقفًا (الـusers_create لا يَرفض بعدُ)
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "active_cap.db")
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


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _seed_cap(tenant_id: int, payload: dict) -> None:
    """يَكتب لقطة capacity_contract مباشرة (مع license نشط للـlifecycle)."""
    from app.radius.db.connection import db
    now = _iso(datetime.utcnow())
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'capacity_contract', 'active', 'test://provider',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id), json.dumps(payload, ensure_ascii=False), now, now))
    # نَسعد license نشط كي لا يَقفل الـlifecycle gate
    db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'license', 'active', 'test://license',
                   ?, '{}', ?, 86400, ?)""",
        (int(tenant_id),
         json.dumps({"status": "active"}, ensure_ascii=False), now, now))


def _ensure_tenant(tenant_id: int) -> None:
    """يَضمن وجود سجلّ tenant — FK في radacct يَتطلّبه."""
    from app.radius.db.connection import db
    row = db().execute("SELECT 1 FROM tenants WHERE id=?",
                        (int(tenant_id),)).fetchone()
    if row:
        return
    now = _iso(datetime.utcnow())
    db().execute(
        """INSERT INTO tenants (id, slug, name, created_at)
           VALUES (?, ?, ?, ?)""",
        (int(tenant_id), f"t{int(tenant_id)}", f"Tenant {int(tenant_id)}", now))


def _add_session(tenant_id: int, username: str, *,
                  nas_port_type: str = "Ethernet",
                  calling_station_id: str = "",
                  open: bool = True) -> None:
    """يَضيف جلسة في radacct (مفتوحة = acctstoptime IS NULL).

    ``calling_station_id`` = MAC الجهاز كما تُرسله جلسات النطاق العريض (PPPoE)
    والهوت سبوت الحقيقيّة دائمًا؛ يُستعمَل لاستبعاد «نفس الجهاز» عند إعادة
    المصادقة في device_limit.active_other_devices.
    """
    from app.radius.db.connection import db
    _ensure_tenant(tenant_id)
    db().execute(
        """INSERT INTO radacct
           (tenant_id, username, acctstarttime, acctstoptime, nasporttype,
            callingstationid)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (int(tenant_id), username, _iso(datetime.utcnow()),
         None if open else _iso(datetime.utcnow()),
         nas_port_type, calling_station_id))


def _mk_sub(username="u1", password="pw1", **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = dict(id=None, username=username, password=password, tenant_id=1,
                status="enabled")
    base.update(kw)
    return subscribers_repo.upsert_subscriber(Subscriber(**base))


# ════════════════════════════════════════════════════════════════════════
# (1) get_active_online_cap — قراءة من العقد
# ════════════════════════════════════════════════════════════════════════
class TestCapReader:

    def test_preferred_path_active_online_max(self, app_ctx):
        from app.radius.services.provider_grant import get_active_online_cap
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 100}}})
        assert get_active_online_cap(1) == 100

    def test_fallback_path_active_max(self, app_ctx):
        from app.radius.services.provider_grant import get_active_online_cap
        _seed_cap(1, {"status": "active", "limits": {"active": {"max": 250}}})
        assert get_active_online_cap(1) == 250

    def test_fallback_path_concurrent_online(self, app_ctx):
        from app.radius.services.provider_grant import get_active_online_cap
        _seed_cap(1, {"status": "active",
                       "limits": {"concurrent_online": {"max": 500}}})
        assert get_active_online_cap(1) == 500

    def test_fallback_path_active_subscribers(self, app_ctx):
        """تَوافقية مع الاسم القديم — كان يُفهَم خطأً كـtotal, الآن concurrent."""
        from app.radius.services.provider_grant import get_active_online_cap
        _seed_cap(1, {"status": "active",
                       "limits": {"active_subscribers": {"max": 1000}}})
        assert get_active_online_cap(1) == 1000

    def test_preferred_wins_over_fallback(self, app_ctx):
        from app.radius.services.provider_grant import get_active_online_cap
        _seed_cap(1, {"status": "active", "limits": {
            "active_online": {"max": 50},
            "active": {"max": 100},
        }})
        assert get_active_online_cap(1) == 50

    def test_no_contract_returns_none(self, app_ctx):
        from app.radius.services.provider_grant import get_active_online_cap
        assert get_active_online_cap(1) is None

    def test_no_active_online_field_returns_none(self, app_ctx):
        from app.radius.services.provider_grant import get_active_online_cap
        _seed_cap(1, {"status": "active", "limits": {"cards": {"max_total": 99}}})
        assert get_active_online_cap(1) is None

    def test_negative_value_treated_as_unlimited(self, app_ctx):
        from app.radius.services.provider_grant import get_active_online_cap
        _seed_cap(1, {"status": "active",
                       "limits": {"active_online": {"max": -1}}})
        assert get_active_online_cap(1) is None  # get_limit rejects negatives


# ════════════════════════════════════════════════════════════════════════
# (2) count_active_sessions — عدّ radacct
# ════════════════════════════════════════════════════════════════════════
class TestSessionCounter:

    def test_counts_only_open_sessions(self, app_ctx):
        from app.radius.services.provider_grant import count_active_sessions
        _add_session(1, "u1", open=True)
        _add_session(1, "u2", open=True)
        _add_session(1, "u3", open=False)  # مُغلقة
        assert count_active_sessions(1) == 2

    def test_counts_across_all_nas_types(self, app_ctx):
        """تعريف المالك: cards + subscribers + PPPoE + hotspot."""
        from app.radius.services.provider_grant import count_active_sessions
        _add_session(1, "card1",   nas_port_type="Ethernet")
        _add_session(1, "pppoe1",  nas_port_type="PPPoE")
        _add_session(1, "hotspot1", nas_port_type="Wireless-802.11")
        _add_session(1, "sub1",    nas_port_type="Virtual")
        assert count_active_sessions(1) == 4

    def test_tenant_isolation(self, app_ctx):
        from app.radius.services.provider_grant import count_active_sessions
        _add_session(1, "u_t1")
        _add_session(2, "u_t2")
        assert count_active_sessions(1) == 1
        assert count_active_sessions(2) == 1

    def test_exclude_username_skips_self(self, app_ctx):
        from app.radius.services.provider_grant import count_active_sessions
        _add_session(1, "u1")
        _add_session(1, "u2")
        _add_session(1, "u3")
        assert count_active_sessions(1) == 3
        assert count_active_sessions(1, exclude_username="u2") == 2

    def test_empty_radacct_returns_zero(self, app_ctx):
        from app.radius.services.provider_grant import count_active_sessions
        assert count_active_sessions(1) == 0


# ════════════════════════════════════════════════════════════════════════
# (3) user_has_open_session — لإعفاء re-auth
# ════════════════════════════════════════════════════════════════════════
class TestUserHasOpenSession:

    def test_true_when_session_exists(self, app_ctx):
        from app.radius.services.provider_grant import user_has_open_session
        _add_session(1, "u1", open=True)
        assert user_has_open_session(1, "u1") is True

    def test_false_when_no_session(self, app_ctx):
        from app.radius.services.provider_grant import user_has_open_session
        assert user_has_open_session(1, "u1") is False

    def test_false_when_session_closed(self, app_ctx):
        from app.radius.services.provider_grant import user_has_open_session
        _add_session(1, "u1", open=False)
        assert user_has_open_session(1, "u1") is False


# ════════════════════════════════════════════════════════════════════════
# (4) policy_engine._check_provider_active_cap — auth-time enforcement
# ════════════════════════════════════════════════════════════════════════
class TestPolicyEngineEnforcement:

    def _auth(self, username="newcomer", password="pw1",
              calling_station_id=""):
        from app.radius.services.policy_engine import AuthRequest, authorize
        return authorize(AuthRequest(username=username, password=password,
                                       tenant_id=1,
                                       calling_station_id=calling_station_id))

    def test_no_contract_no_cap_allows(self, app_ctx):
        # لا عقد → unlimited → السماح
        _mk_sub("newcomer", "pw1")
        d = self._auth()
        assert d.ok

    def test_unlimited_via_missing_field_allows(self, app_ctx):
        _seed_cap(1, {"status": "active", "limits": {"cards": {"max_total": 50}}})
        _mk_sub("newcomer", "pw1")
        # نَملأ radacct بـ100 جلسة من مستخدمين آخرين — لا يَهمّ، لا cap
        for i in range(100):
            _add_session(1, f"other_{i}")
        d = self._auth()
        assert d.ok

    def test_below_cap_allows(self, app_ctx):
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 10}}})
        _mk_sub("newcomer", "pw1")
        # 9 جلسات أخرى → 9 < 10، السماح للجلسة العاشرة
        for i in range(9):
            _add_session(1, f"other_{i}")
        d = self._auth()
        assert d.ok

    def test_at_cap_rejects_new_user(self, app_ctx):
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 10}}})
        _mk_sub("newcomer", "pw1")
        # 10 جلسات أخرى → عند السقف، رفض الجلسة الجديدة
        for i in range(10):
            _add_session(1, f"other_{i}")
        d = self._auth()
        assert not d.ok
        assert d.reason == "provider_active_cap"
        assert "الحدّ الأقصى للمتصلين المتزامنين" in d.message

    def test_above_cap_rejects(self, app_ctx):
        # حالة يَجب أن لا تَحدث، لكن لو حدثت (cleanup متأخّر) لا نَسمح بالمزيد
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 10}}})
        _mk_sub("newcomer", "pw1")
        for i in range(15):
            _add_session(1, f"other_{i}")
        d = self._auth()
        assert not d.ok and d.reason == "provider_active_cap"

    def test_reauth_at_cap_allowed_when_user_already_has_session(self, app_ctx):
        """re-auth لنفس الجهاز (نفس MAC) لمستخدم له جلسة قائمة لا يُحتسب —
        لن يَزيد العدد فعليًّا. جلسات النطاق العريض/الهوت سبوت الحقيقيّة تَحمل
        دائمًا Calling-Station-Id، فإعادة الاتصال من نفس الجهاز تُستبعَد."""
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 10}}})
        _mk_sub("returning_user", "pw1")
        # 9 جلسات أخرى + 1 لمستخدمنا = 10 على السقف بالضبط
        for i in range(9):
            _add_session(1, f"other_{i}", calling_station_id=f"AA:BB:CC:00:00:{i:02X}")
        mac = "AA:BB:CC:DD:EE:01"
        _add_session(1, "returning_user", open=True, calling_station_id=mac)
        # نفس الجهاز (نفس MAC) يُعيد المصادقة → جلسته القائمة تُستبعَد → سماح
        d = self._auth(username="returning_user", calling_station_id=mac)
        assert d.ok, f"reauth should pass, got {d.reason}: {d.message}"

    def test_reauth_from_different_device_over_limit_rejected(self, app_ctx):
        """جهازٌ مختلف فعلاً (MAC مختلف) لمستخدمٍ بلغ حدّه الفرديّ (device_count
        الافتراضيّ = 1) يُرفَض — لا يُستبعَد لأنّه ليس «نفس الجهاز»."""
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 10}}})
        _mk_sub("returning_user", "pw1")
        for i in range(9):
            _add_session(1, f"other_{i}", calling_station_id=f"AA:BB:CC:00:00:{i:02X}")
        _add_session(1, "returning_user", open=True,
                     calling_station_id="AA:BB:CC:DD:EE:01")
        # MAC مختلف = جهاز ثانٍ حقيقيّ → لا يُستبعَد → يَتجاوز حدّ الجهاز الواحد
        d = self._auth(username="returning_user",
                       calling_station_id="AA:BB:CC:DD:EE:02")
        assert not d.ok and d.reason == "concurrent_limit", \
            f"different device should be rejected, got {d.reason}: {d.message}"

    def test_mixed_session_types_all_count(self, app_ctx):
        """تعريف المالك: كل أنواع الاتصال تُحتسب (cards + PPPoE + hotspot)."""
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 5}}})
        _mk_sub("newcomer", "pw1")
        _add_session(1, "card1",    nas_port_type="Ethernet")
        _add_session(1, "pppoe1",   nas_port_type="PPPoE")
        _add_session(1, "hotspot1", nas_port_type="Wireless-802.11")
        _add_session(1, "sub1",     nas_port_type="Virtual")
        _add_session(1, "sub2",     nas_port_type="Virtual")
        # 5 جلسات في انواع مختلفة → عند السقف
        d = self._auth()
        assert not d.ok and d.reason == "provider_active_cap"

    def test_closed_sessions_dont_count_toward_cap(self, app_ctx):
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 3}}})
        _mk_sub("newcomer", "pw1")
        for i in range(2):
            _add_session(1, f"open_{i}", open=True)
        for i in range(100):
            _add_session(1, f"closed_{i}", open=False)
        # 2 مفتوحة، 100 مغلقة → 2 < 3، السماح
        d = self._auth()
        assert d.ok

    def test_rejection_not_in_fail2ban(self, app_ctx):
        """provider_active_cap رفض سعة لا فشل auth → خارج fail2ban."""
        from app.radius.services.policy_engine import _FAIL2BAN_REASONS
        assert "provider_active_cap" not in _FAIL2BAN_REASONS

    def test_other_tenant_sessions_dont_count(self, app_ctx):
        """عزل المستأجر: جلسات tenant 2 لا تَستهلك سقف tenant 1."""
        _seed_cap(1, {"status": "active", "limits": {"active_online": {"max": 3}}})
        _mk_sub("newcomer", "pw1")
        # نَضع 10 جلسات على tenant 2 — لا تؤثر
        for i in range(10):
            _add_session(2, f"t2_user_{i}")
        d = self._auth()
        assert d.ok


# ════════════════════════════════════════════════════════════════════════
# (5) Fail-safe — أي خطأ يَسمح (لا يَكسر auth)
# ════════════════════════════════════════════════════════════════════════
class TestFailSafe:

    def test_check_returns_none_on_exception(self, app_ctx, monkeypatch):
        """لو فشل قراءة العقد أو radacct، الـcheck يَسمح (لا يَرفض)."""
        from app.radius.services import policy_engine
        # نَكسر provider_grant.get_active_online_cap عمدًا
        from app.radius.services import provider_grant
        def _broken(*a, **k):
            raise RuntimeError("simulated DB outage")
        monkeypatch.setattr(provider_grant, "get_active_online_cap", _broken)
        from app.radius.core.types import Subscriber
        from app.radius.services.policy_engine import AuthRequest
        sub = Subscriber(id=1, username="u", password="p", tenant_id=1,
                          status="enabled")
        req = AuthRequest(username="u", password="p", tenant_id=1)
        # الـcheck يَجب أن يُرجع None (سماح)، لا يَرمي
        result = policy_engine._check_provider_active_cap(sub, req)
        assert result is None


# ════════════════════════════════════════════════════════════════════════
# (6) subscribers لم يَعد سقف create-time
# ════════════════════════════════════════════════════════════════════════
class TestSubscribersCreateTimeRemoved:

    def test_users_create_no_longer_consults_subscribers_limit(self, app_ctx):
        """قبل: users_create كان يَرفض عند subscribers.max_total. الآن يَتجاهله
        (السقف الرئيسي concurrent-online، يُفرَض auth-time)."""
        # حتى لو العقد يَحوي subscribers.max_total = 0، الإنشاء يَجب أن يَنجح
        _seed_cap(1, {"status": "active",
                       "limits": {"subscribers": {"max_total": 0},
                                  "active_online": {"max": 100}}})
        # users.users_create POST يَحتاج CSRF + جلسة. أبسط: نَستدعي الـservice
        # مباشرة (الذي كان يُلَفّ بفحص سقف). نَتأكّد أنّ هذا الفحص ذَهب.
        # نَفتح الملف ونَتأكّد أنّ السطر القديم مَحذوف:
        import inspect
        from app.radius.routes import users as users_routes
        src = inspect.getsource(users_routes.users_create)
        # السطر القديم كان يَستدعي check_limit مع "subscribers"
        assert 'check_limit(_tid(), "subscribers"' not in src

    def test_subscribers_no_longer_in_LIMIT_PATHS(self, app_ctx):
        from app.radius.services.provider_grant import LIMIT_PATHS
        assert "subscribers" not in LIMIT_PATHS
        assert "active_online" in LIMIT_PATHS
