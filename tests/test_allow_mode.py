"""feat/access-control — اختبارات «نمط السماح» (Allow-mode).

تغطّي:
  * Repo round-trip (upsert سياسة، إضافة جهاز، البحث، حدّ TOFU).
  * أولوية الحلّ (card_batch قبل plan).
  * النمط open: يمرّ بدون تحقّق (حدّ الجلسات من plan كما هو).
  * النمط tofu: أوّل MAC = ربط تلقائي + سماح؛ الثاني ضمن الحدّ = ربط؛
    تجاوز الحدّ = رفض مع reason=allow_mode_at_capacity.
  * النمط manual: غير المسجّل = رفض allow_mode_unknown_device؛
    المسجّل = سماح.
  * يتعايش مع L1 (تعليق الوصول) و L2 (الحظر/fail2ban) القائمَين.
  * Smoke لراوتات الإدارة (سياسة + جهاز + حذف + تعطيل).
  * AlertSpec + ACTION_ALERTS deep-link.

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os

import pytest

from app.radius.services import allow_mode as svc


# ════════════════════════════════════════════════════════════════════════
# Fixture
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "allow_mode.db")
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


def _mk_plan(name="P", **kw):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    base = dict(id=None, tenant_id=1, name=name, concurrent_sessions=1)
    base.update(kw)
    return plans_repo.upsert_plan(AccessPlan(**base))


def _mk_batch(code="B1"):
    from app.radius.core.types import CardBatch
    from app.radius.db.repos import cards_repo
    # CardBatch يحتاج batch_code + plan_id + count كحقول إلزامية
    plan = _mk_plan(name="بطاقات-١")
    return cards_repo.create_batch(CardBatch(
        id=None, tenant_id=1, batch_code=code, plan_id=plan.id, count=1))


def _mk_sub(username="u1", password="pw", plan_id=None, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = dict(id=None, username=username, password=password, tenant_id=1,
                status="enabled", plan_id=plan_id)
    base.update(kw)
    return subscribers_repo.upsert_subscriber(Subscriber(**base))


# ════════════════════════════════════════════════════════════════════════
# (1) Repo
# ════════════════════════════════════════════════════════════════════════
class TestRepo:

    def test_normalize_mac(self):
        from app.radius.db.repos import allow_mode_repo
        assert allow_mode_repo.normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"

    def test_upsert_policy_unique(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        p1 = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="tofu", max_devices=2)
        # نفس المفتاح → تحديث لا إنشاء
        p2 = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        assert p1["id"] == p2["id"]
        assert p2["mode"] == "manual"

    def test_add_device_dedup(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        d1 = allow_mode_repo.add_device(policy_id=pol["id"], username="u1",
                                         mac="aa:bb:cc:dd:ee:ff")
        d2 = allow_mode_repo.add_device(policy_id=pol["id"], username="u1",
                                         mac="AA:BB:CC:DD:EE:FF")
        assert d1["id"] == d2["id"]

    def test_find_device_match_personal_then_shared(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        # جهاز مشترك (username='')
        allow_mode_repo.add_device(policy_id=pol["id"], username="",
                                    mac="AA:BB:CC:DD:EE:01",
                                    label="مكتب")
        # جهاز شخصي لـu1
        allow_mode_repo.add_device(policy_id=pol["id"], username="u1",
                                    mac="AA:BB:CC:DD:EE:02",
                                    label="هاتف u1")
        # u1 يطابق الشخصي أو المشترك
        m1 = allow_mode_repo.find_device_match(pol["id"], username="u1",
                                                 mac="AA:BB:CC:DD:EE:01")
        assert m1 and m1["username"] == ""
        m2 = allow_mode_repo.find_device_match(pol["id"], username="u1",
                                                 mac="AA:BB:CC:DD:EE:02")
        assert m2 and m2["username"] == "u1"
        # u2 يطابق المشترك فقط، لا يطابق شخصي u1
        m3 = allow_mode_repo.find_device_match(pol["id"], username="u2",
                                                 mac="AA:BB:CC:DD:EE:02")
        assert m3 is None
        m4 = allow_mode_repo.find_device_match(pol["id"], username="u2",
                                                 mac="AA:BB:CC:DD:EE:01")
        assert m4 and m4["username"] == ""


# ════════════════════════════════════════════════════════════════════════
# (2) Resolve policy + precedence
# ════════════════════════════════════════════════════════════════════════
class TestResolvePolicy:

    def test_none_when_no_policy(self, app_ctx):
        assert svc.resolve_policy(1, plan_id=99, card_batch_id=None) is None

    def test_card_batch_wins_over_plan(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        batch = _mk_batch()
        allow_mode_repo.upsert_policy(tenant_id=1, scope_type="plan",
                                       scope_id=plan.id, mode="open",
                                       max_devices=0)
        allow_mode_repo.upsert_policy(tenant_id=1, scope_type="card_batch",
                                       scope_id=batch.id, mode="manual",
                                       max_devices=0)
        p = svc.resolve_policy(1, plan_id=plan.id, card_batch_id=batch.id)
        assert p["mode"] == "manual"
        assert p["scope_type"] == "card_batch"

    def test_only_active_policy_returned(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        allow_mode_repo.set_policy_active(1, pol["id"], False)
        assert svc.resolve_policy(1, plan_id=plan.id,
                                    card_batch_id=None) is None


# ════════════════════════════════════════════════════════════════════════
# (3) Evaluate — أنماط الثلاثة
# ════════════════════════════════════════════════════════════════════════
class TestEvaluate:

    def _make_policy(self, mode, max_devices=0):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode=mode, max_devices=max_devices)
        return plan, pol

    def test_open_mode_always_allow(self, app_ctx):
        plan, _ = self._make_policy("open")
        v = svc.evaluate(1, username="u1", plan_id=plan.id,
                          card_batch_id=None, mac="AA:BB:CC:DD:EE:FF")
        assert v.action == "allow"
        assert v.reason == "open_mode"

    def test_tofu_first_login_binds_and_allows(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan, pol = self._make_policy("tofu", max_devices=2)
        v = svc.evaluate(1, username="u1", plan_id=plan.id,
                          card_batch_id=None, mac="AA:BB:CC:DD:EE:01")
        assert v.action == "allow"
        assert v.reason == "tofu_bind"
        assert v.auto_bound_device_id
        # الجهاز محفوظ بـsource='auto' وusername='u1'
        devs = allow_mode_repo.list_devices(pol["id"], username="u1")
        assert len(devs) == 1
        assert devs[0]["source"] == "auto"
        assert devs[0]["username"] == "u1"

    def test_tofu_within_capacity_allows_second_device(self, app_ctx):
        plan, _ = self._make_policy("tofu", max_devices=2)
        svc.evaluate(1, username="u1", plan_id=plan.id,
                       card_batch_id=None, mac="AA:BB:CC:DD:EE:01")
        v2 = svc.evaluate(1, username="u1", plan_id=plan.id,
                            card_batch_id=None, mac="AA:BB:CC:DD:EE:02")
        assert v2.action == "allow"
        assert v2.reason == "tofu_bind"

    def test_tofu_third_device_over_cap_denied(self, app_ctx):
        plan, _ = self._make_policy("tofu", max_devices=2)
        svc.evaluate(1, username="u1", plan_id=plan.id, card_batch_id=None,
                      mac="AA:BB:CC:DD:EE:01")
        svc.evaluate(1, username="u1", plan_id=plan.id, card_batch_id=None,
                      mac="AA:BB:CC:DD:EE:02")
        v3 = svc.evaluate(1, username="u1", plan_id=plan.id,
                           card_batch_id=None, mac="AA:BB:CC:DD:EE:03")
        assert v3.action == "deny"
        assert v3.reason == "allow_mode_at_capacity"

    def test_tofu_cap_is_per_account_not_per_policy(self, app_ctx):
        """مهمّ: السقف لكل حساب لا للسياسة. لو 100 بطاقة تشارك العرض، أوّل 2
        أجهزة من كل بطاقة لها ربط مستقلّ — لا يحجز جهاز بطاقة A سقف بطاقة B."""
        plan, _ = self._make_policy("tofu", max_devices=1)
        v1 = svc.evaluate(1, username="A", plan_id=plan.id,
                            card_batch_id=None, mac="AA:BB:CC:DD:EE:01")
        assert v1.action == "allow" and v1.reason == "tofu_bind"
        v2 = svc.evaluate(1, username="B", plan_id=plan.id,
                            card_batch_id=None, mac="AA:BB:CC:DD:EE:02")
        assert v2.action == "allow" and v2.reason == "tofu_bind"
        # A يحاول جهازًا ثانيًا → رفض
        vA2 = svc.evaluate(1, username="A", plan_id=plan.id,
                             card_batch_id=None, mac="AA:BB:CC:DD:EE:99")
        assert vA2.action == "deny"
        assert vA2.reason == "allow_mode_at_capacity"

    def test_manual_unknown_device_denied(self, app_ctx):
        plan, _ = self._make_policy("manual")
        v = svc.evaluate(1, username="u1", plan_id=plan.id,
                          card_batch_id=None, mac="AA:BB:CC:DD:EE:FF")
        assert v.action == "deny"
        assert v.reason == "allow_mode_unknown_device"

    def test_manual_registered_device_allowed(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan, pol = self._make_policy("manual")
        allow_mode_repo.add_device(policy_id=pol["id"], username="u1",
                                    mac="AA:BB:CC:DD:EE:FF")
        v = svc.evaluate(1, username="u1", plan_id=plan.id,
                          card_batch_id=None, mac="AA:BB:CC:DD:EE:FF")
        assert v.action == "allow"
        assert v.reason == "device_allowed"

    def test_manual_shared_device_allows_any_user(self, app_ctx):
        """جهاز مشترك (username='') يَسمح لكل مستخدمي النطاق — للمكاتب."""
        from app.radius.db.repos import allow_mode_repo
        plan, pol = self._make_policy("manual")
        allow_mode_repo.add_device(policy_id=pol["id"], username="",
                                    mac="AA:BB:CC:DD:EE:OF",
                                    label="جهاز المكتب")
        for user in ("alice", "bob", "carol"):
            v = svc.evaluate(1, username=user, plan_id=plan.id,
                              card_batch_id=None,
                              mac="AA:BB:CC:DD:EE:OF")
            assert v.action == "allow", user
            assert v.reason == "device_allowed"


# ════════════════════════════════════════════════════════════════════════
# (4) Integration with policy_engine (full RADIUS auth)
# ════════════════════════════════════════════════════════════════════════
class TestPolicyEngineIntegration:

    def _auth(self, **kw):
        from app.radius.services.policy_engine import AuthRequest, authorize
        base = dict(username="u1", password="pw", tenant_id=1,
                     calling_station_id="AA:BB:CC:DD:EE:01",
                     nas_ip="10.0.0.1")
        base.update(kw)
        return authorize(AuthRequest(**base))

    def test_no_policy_no_effect(self, app_ctx):
        plan = _mk_plan(concurrent_sessions=0)
        _mk_sub(plan_id=plan.id)
        d = self._auth()
        assert d.ok

    def test_manual_unknown_rejects_at_radius(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan(concurrent_sessions=0)
        _mk_sub(plan_id=plan.id)
        allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        d = self._auth()
        assert not d.ok
        assert d.reason == "allow_mode_unknown_device"

    def test_manual_registered_accepts_at_radius(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan(concurrent_sessions=0)
        _mk_sub(plan_id=plan.id)
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        allow_mode_repo.add_device(policy_id=pol["id"], username="u1",
                                    mac="AA:BB:CC:DD:EE:01")
        d = self._auth()
        assert d.ok

    def test_tofu_binds_at_radius(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan(concurrent_sessions=0)
        _mk_sub(plan_id=plan.id)
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="tofu", max_devices=1)
        d = self._auth()
        assert d.ok
        # الجهاز سُجِّل تلقائيًّا
        devs = allow_mode_repo.list_devices(pol["id"], username="u1")
        assert len(devs) == 1 and devs[0]["source"] == "auto"
        # محاولة جهاز ثانٍ بنفس الحساب → رفض على حدود السعة (max=1)
        d2 = self._auth(calling_station_id="AA:BB:CC:DD:EE:02")
        assert not d2.ok
        assert d2.reason == "allow_mode_at_capacity"


# ════════════════════════════════════════════════════════════════════════
# (5) التعايش مع L1 (Suspension) و L2 (Block)
# ════════════════════════════════════════════════════════════════════════
class TestCoexistenceWithExistingLayers:

    def _auth(self, **kw):
        from app.radius.services.policy_engine import AuthRequest, authorize
        base = dict(username="u1", password="pw", tenant_id=1,
                     calling_station_id="AA:BB:CC:DD:EE:01",
                     nas_ip="10.0.0.1")
        base.update(kw)
        return authorize(AuthRequest(**base))

    def test_l1_suspension_overrides_allow_mode(self, app_ctx):
        """تعليق الوصول يفشل أوّلًا — حتى لو نمط السماح سيمرّر."""
        from app.radius.db.repos import access_blocks_repo, allow_mode_repo
        plan = _mk_plan()
        _mk_sub(plan_id=plan.id)
        # نمط سماح يسمح لهذا الجهاز
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        allow_mode_repo.add_device(policy_id=pol["id"], username="u1",
                                    mac="AA:BB:CC:DD:EE:01")
        # لكن تعليق وصول صريح على المشترك
        access_blocks_repo.create_block(tenant_id=1, block_type="subscriber",
                                         target="u1")
        d = self._auth()
        assert not d.ok
        # تعليق الوصول يُرجَع قبل allow-mode في سلسلة الفحوصات
        assert d.reason == "access_suspended"

    def test_l2_mac_block_overrides_allow_mode(self, app_ctx):
        """حظر MAC أمني يفشل أوّلًا."""
        from app.radius.db.repos import access_blocks_repo, allow_mode_repo
        plan = _mk_plan()
        _mk_sub(plan_id=plan.id)
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        allow_mode_repo.add_device(policy_id=pol["id"], username="u1",
                                    mac="AA:BB:CC:DD:EE:01")
        access_blocks_repo.create_block(tenant_id=1, block_type="mac",
                                         target="AA:BB:CC:DD:EE:01")
        d = self._auth()
        assert not d.ok
        assert d.reason == "access_blocked"


# ════════════════════════════════════════════════════════════════════════
# (6) AlertSpec + ACTION_ALERTS
# ════════════════════════════════════════════════════════════════════════
class TestAlertWiring:

    def test_admin_alerts_has_allow_mode_spec(self):
        from app.radius.services import admin_alerts
        spec = admin_alerts.get_spec("allow_mode_unknown_device")
        assert spec is not None
        text = admin_alerts.preview("allow_mode_unknown_device")
        assert "نمط السماح" in text

    def test_action_alerts_includes_allow_mode(self):
        from app.radius.services.alert_links import ACTION_ALERTS
        assert "allow_mode_unknown_device" in ACTION_ALERTS


# ════════════════════════════════════════════════════════════════════════
# (7) راوتات الإدارة (smoke)
# ════════════════════════════════════════════════════════════════════════
class TestAdminRoutes:

    def _login(self, client):
        with client.session_transaction() as s:
            s["admin_id"] = 1
            s["admin_user"] = "admin"
            s["is_super_admin"] = True

    def _csrf(self, client):
        client.get("/admin/radius/access-control")
        with client.session_transaction() as s:
            return s.get("_csrf_token") or ""

    def test_page_renders_with_allow_mode_section(self, app_ctx):
        client = app_ctx.test_client()
        self._login(client)
        rv = client.get("/admin/radius/access-control")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8")
        assert "نمط السماح" in body
        assert "allow-mode" in body  # رابط المرساة

    def test_upsert_policy_persists(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan(name="عرض-اختبار")
        client = app_ctx.test_client()
        self._login(client)
        token = self._csrf(client)
        rv = client.post("/admin/radius/access-control/allow-mode/policy",
                          data={
                              "_csrf_token": token,
                              "scope_type": "plan",
                              "scope_id":  str(plan.id),
                              "mode": "tofu",
                              "max_devices": "3",
                              "active": "1",
                              "note": "اختبار",
                          }, follow_redirects=False)
        assert rv.status_code in (302, 303)
        pol = allow_mode_repo.get_policy(1, "plan", plan.id)
        assert pol is not None
        assert pol["mode"] == "tofu"
        assert pol["max_devices"] == 3
        assert pol["note"] == "اختبار"

    def test_add_and_delete_device(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        client = app_ctx.test_client()
        self._login(client)
        token = self._csrf(client)
        rv = client.post("/admin/radius/access-control/allow-mode/device",
                          data={
                              "_csrf_token": token,
                              "policy_id": str(pol["id"]),
                              "username": "u1",
                              "mac": "aa:bb:cc:dd:ee:ff",
                              "label": "هاتف",
                          }, follow_redirects=False)
        assert rv.status_code in (302, 303)
        devs = allow_mode_repo.list_devices(pol["id"])
        assert len(devs) == 1
        assert devs[0]["mac"] == "AA:BB:CC:DD:EE:FF"
        # حذف
        rv2 = client.post(
            f"/admin/radius/access-control/allow-mode/device/{devs[0]['id']}/delete",
            data={"_csrf_token": token}, follow_redirects=False)
        assert rv2.status_code in (302, 303)
        assert allow_mode_repo.list_devices(pol["id"]) == []

    def test_invalid_mac_rejected_with_flash(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        client = app_ctx.test_client()
        self._login(client)
        token = self._csrf(client)
        rv = client.post("/admin/radius/access-control/allow-mode/device",
                          data={
                              "_csrf_token": token,
                              "policy_id": str(pol["id"]),
                              "username": "u1",
                              "mac": "not-a-mac",
                          }, follow_redirects=False)
        assert rv.status_code in (302, 303)
        # لا جهاز أُضيف
        assert allow_mode_repo.list_devices(pol["id"]) == []

    def test_toggle_and_delete_policy(self, app_ctx):
        from app.radius.db.repos import allow_mode_repo
        plan = _mk_plan()
        pol = allow_mode_repo.upsert_policy(
            tenant_id=1, scope_type="plan", scope_id=plan.id,
            mode="manual", max_devices=0)
        client = app_ctx.test_client()
        self._login(client)
        token = self._csrf(client)
        # تعطيل
        rv = client.post(
            f"/admin/radius/access-control/allow-mode/policy/{pol['id']}/toggle",
            data={"_csrf_token": token}, follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert not allow_mode_repo.get_policy_by_id(1, pol["id"])["active"]
        # حذف
        rv2 = client.post(
            f"/admin/radius/access-control/allow-mode/policy/{pol['id']}/delete",
            data={"_csrf_token": token}, follow_redirects=False)
        assert rv2.status_code in (302, 303)
        assert allow_mode_repo.get_policy_by_id(1, pol["id"]) is None
