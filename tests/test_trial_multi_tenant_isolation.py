"""trial/multi-tenant-vps — اختبارات عزل الجهات (نسخة التجريب المجاني).

تغطي الإصلاحات الحاجزة الأربعة:
  MT1 — استعلامات FreeRADIUS (radacct/radpostauth) تشتق tenant_id من جدول
        nas بدل تثبيت 1 (فحص محتوى ملف الإعداد deploy/freeradius).
  MT2 — ربط الراوتر→الجهة: Packet-Src-IP أولًا، fail-closed عند تعدد
        الجهات، وقيد «عنوان واحد لا يخدم جهتين» في nas_devices وnas.
  MT3 — بوابة المشترك تحلّ الجهة من ?t=<slug> بدل تثبيت 1.
  MT4 — الجهة المعلّقة/التجربة المنتهية تُرفض مصادقتها lazy.

شغّل وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "trial_mt_iso.db")
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


def _seed_tenant(tid: int, slug: str, *, status: str = "active",
                 trial_ends_at: str | None = None) -> None:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO tenants (id, slug, name, display_name, status,
                                 trial_ends_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (tid, slug, slug.upper(), slug.upper(), status, trial_ends_at, now))


def _seed_nas_device(tid: int, name: str, address: str) -> None:
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO nas_devices (tenant_id, name, address, secret, created_at)
           VALUES (?, ?, ?, 'sec', ?)""",
        (tid, name, address, now))


# ─────────────── MT2: ربط الراوتر→الجهة ───────────────

def test_resolve_tenant_prefers_packet_src_ip(app_ctx):
    _seed_tenant(2, "acme")
    _seed_nas_device(1, "r1", "10.10.0.5")
    _seed_nas_device(2, "r2", "10.10.0.7")
    from app.api.v1.internal_auth import _resolve_tenant_id
    # المايكروتيك يرسل NAS-IP-Address داخليًا (LAN) — الحلّ من مصدر الحزمة.
    assert _resolve_tenant_id({
        "Packet-Src-IP": "10.10.0.7",
        "NAS-IP-Address": "192.168.88.1",
    }) == 2
    assert _resolve_tenant_id({"Packet-Src-IP": "10.10.0.5"}) == 1


def test_resolve_tenant_fail_closed_with_multiple_tenants(app_ctx):
    _seed_tenant(2, "acme")
    from app.api.v1.internal_auth import _resolve_tenant_id
    # جهتان + راوتر غير معروف = None (رفض) — لا سقوط صامت للجهة 1.
    assert _resolve_tenant_id({"Packet-Src-IP": "203.0.113.9"}) is None
    assert _resolve_tenant_id({}) is None


def test_resolve_tenant_single_tenant_keeps_default(app_ctx):
    from app.api.v1.internal_auth import _resolve_tenant_id
    # تثبيت أحادي الجهة: السلوك القديم (default 1) محفوظ.
    assert _resolve_tenant_id({"Packet-Src-IP": "203.0.113.9"}) == 1


def test_resolve_tenant_falls_back_to_freeradius_nas_table(app_ctx):
    _seed_tenant(2, "acme")
    from app.radius.db.repos import freeradius_repo
    freeradius_repo.upsert_nas_client(2, nasname="10.10.0.9",
                                       shortname="r9", secret="s")
    from app.api.v1.internal_auth import _resolve_tenant_id
    assert _resolve_tenant_id({"Packet-Src-IP": "10.10.0.9"}) == 2


def test_nas_device_address_unique_across_tenants(app_ctx):
    _seed_tenant(2, "acme")
    _seed_nas_device(1, "r1", "10.10.0.5")
    from app.radius.core.errors import RadiusValidationError
    from app.radius.db.connection import db
    from app.radius.db.repos.nas_repo import guard_cross_tenant_address
    with pytest.raises(RadiusValidationError):
        guard_cross_tenant_address(db(), 2, "10.10.0.5")
    # داخل نفس الجهة مسموح (لا غموض في الحلّ).
    guard_cross_tenant_address(db(), 1, "10.10.0.5")
    # المحذوف ناعمًا لا يحجز العنوان.
    db().execute("UPDATE nas_devices SET deleted_at = '2026-01-01T00:00:00Z' "
                 "WHERE address = '10.10.0.5'")
    guard_cross_tenant_address(db(), 2, "10.10.0.5")


def test_freeradius_nas_client_unique_across_tenants(app_ctx):
    _seed_tenant(2, "acme")
    from app.radius.core.errors import RadiusValidationError
    from app.radius.db.repos import freeradius_repo
    freeradius_repo.upsert_nas_client(1, nasname="10.10.0.5",
                                       shortname="r1", secret="s")
    with pytest.raises(RadiusValidationError):
        freeradius_repo.upsert_nas_client(2, nasname="10.10.0.5",
                                           shortname="rx", secret="s")
    # تحديث نفس الجهة يمرّ.
    freeradius_repo.upsert_nas_client(1, nasname="10.10.0.5",
                                       shortname="r1b", secret="s2")


# ─────────────── MT4: إنفاذ حالة الجهة ───────────────

def test_tenant_block_reason_matrix(app_ctx):
    from app.radius.core.tenant import Tenant, tenant_block_reason
    past = datetime.utcnow() - timedelta(days=1)
    future = datetime.utcnow() + timedelta(days=1)
    mk = lambda **kw: Tenant(id=9, slug="x", name="x", **kw)
    assert tenant_block_reason(mk()) == ""
    assert tenant_block_reason(mk(status="suspended")) == "suspended"
    assert tenant_block_reason(mk(status="closed")) == "closed"
    assert tenant_block_reason(mk(status="trial", trial_ends_at=future)) == ""
    assert tenant_block_reason(mk(status="trial", trial_ends_at=past)) == "trial_expired"
    assert tenant_block_reason(None) == ""


def test_authorize_rejects_expired_trial_tenant(app_ctx):
    past = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
    _seed_tenant(2, "acme", status="trial", trial_ends_at=past)
    from app.radius.services.policy_engine import AuthRequest, authorize
    d = authorize(AuthRequest(username="user1", password="p", tenant_id=2))
    assert not d.ok
    assert d.reason == "tenant_trial_expired"


def test_authorize_active_trial_passes_tenant_gate(app_ctx):
    future = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"
    _seed_tenant(2, "acme", status="trial", trial_ends_at=future)
    from app.radius.services.policy_engine import AuthRequest, authorize
    d = authorize(AuthRequest(username="nosuch", password="p", tenant_id=2))
    # عبَر بوابة الجهة ووصل لفحص المستخدم — الرفض بسبب عدم وجوده فقط.
    assert not d.ok
    assert d.reason == "user_not_found"


# ─────────────── MT3: بوابة المشترك ───────────────

def test_portal_login_tenant_resolved_from_slug(app_ctx):
    _seed_tenant(2, "acme")
    from app.radius.routes.customer_portals import _login_tenant_id
    with app_ctx.test_request_context("/portal/subscriber/login?t=acme"):
        assert _login_tenant_id() == 2
    with app_ctx.test_request_context("/portal/subscriber/login"):
        assert _login_tenant_id() == 1
    with app_ctx.test_request_context("/portal/subscriber/login?t=nosuch"):
        assert _login_tenant_id() == 1


def test_portal_login_blocked_for_expired_trial(app_ctx):
    past = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
    _seed_tenant(2, "acme", status="trial", trial_ends_at=past)
    client = app_ctx.test_client()
    # طبقة CSRF الخادمية: GET أولًا لتوليد التوكن (يُحقن في النموذج).
    page = client.get("/portal/subscriber/login?t=acme").get_data(as_text=True)
    import re
    m = re.search(r'name="_csrf_token" value="([^"]+)"', page)
    assert m, "توكن CSRF لم يُحقن في صفحة الدخول"
    r = client.post("/portal/subscriber/login?t=acme",
                    data={"username": "u", "password": "p",
                          "_csrf_token": m.group(1)})
    assert r.status_code == 403


# ─────────────── MT6/MT7: إنشاء الجهة التجريبية وحد العقد ───────────────

def test_create_trial_seeds_non_super_operator(app_ctx):
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.services.tenants import get_tenants_service
    admins_repo.ensure_default_roles()
    t = Tenant(id=None, slug="acme", name="ACME", status="trial")
    result = get_tenants_service().create_trial(
        actor="tester", tenant=t, trial_days=10,
        operator_username="acme-admin", operator_password="")
    saved = result["tenant"]
    assert saved.status == "trial"
    assert result["trial_ends_at"] is not None
    # كلمة مولّدة تلقائيًا وتُعرض مرة واحدة
    assert len(result["operator_password"]) >= 8
    admin = admins_repo.get_by_username("acme-admin")
    assert admin and not admin.is_super_admin
    # عضوية واحدة في الجهة الجديدة فقط
    tenants = tenants_repo.tenants_for_admin(admin.id)
    assert [x.id for x in tenants] == [saved.id]
    # تُخزَّن نهاية التجربة فعلًا (datetime → ISO → datetime)
    reloaded = tenants_repo.get_tenant(saved.id)
    assert reloaded.trial_ends_at is not None


def test_create_trial_duplicate_operator_rejected(app_ctx):
    from app.radius.core.errors import RadiusValidationError
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import admins_repo
    from app.radius.services.tenants import get_tenants_service
    admins_repo.ensure_default_roles()
    admins_repo.create_admin(username="taken", password="secret123")
    t = Tenant(id=None, slug="beta", name="Beta", status="trial")
    with pytest.raises(RadiusValidationError):
        get_tenants_service().create_trial(
            actor="tester", tenant=t, operator_username="taken")


def test_entity_count_enforced_on_create(app_ctx, monkeypatch):
    from app.radius.core.errors import RadiusValidationError
    from app.radius.core.tenant import Tenant
    from app.radius.services import tenants as tenants_svc
    monkeypatch.setattr(tenants_svc, "_install_entity_limit", lambda: 2)
    svc = tenants_svc.get_tenants_service()
    svc.create(actor="t", tenant=Tenant(id=None, slug="one", name="One"))
    # الجهة الافتراضية + one = 2 → الثالثة تُرفض
    with pytest.raises(RadiusValidationError):
        svc.create(actor="t", tenant=Tenant(id=None, slug="two", name="Two"))


# ─────────────── MT9: الثغرات الثانوية ───────────────

def test_rtr_username_unique_across_tenants(app_ctx):
    _seed_tenant(2, "acme")
    from app.radius.core.errors import RadiusValidationError
    from app.radius.db.repos import freeradius_repo
    freeradius_repo.replace_user_check(1, "rtr-main",
                                        [("Cleartext-Password", ":=", "x")])
    with pytest.raises(RadiusValidationError):
        freeradius_repo.replace_user_check(2, "rtr-main",
                                            [("Cleartext-Password", ":=", "y")])
    # نفس الجهة (إعادة تزويد idempotent) تمرّ، واسم غير rtr- حرّ عبر الجهات.
    freeradius_repo.replace_user_check(1, "rtr-main",
                                        [("Cleartext-Password", ":=", "z")])
    freeradius_repo.replace_user_check(1, "user9", [("Cleartext-Password", ":=", "a")])
    freeradius_repo.replace_user_check(2, "user9", [("Cleartext-Password", ":=", "b")])


def test_npc_script_versions_scoped_by_policy_tenant(app_ctx):
    _seed_tenant(2, "acme")
    from datetime import datetime as _dt
    from flask import g
    from app.radius.db.connection import db
    from app.radius.db.repos import npc_scripts_repo
    now = _dt.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO npc_remote_access_policies
               (id, tenant_id, router_id, name, slug, created_at, updated_at)
           VALUES (77, 2, 1, 'p', 'p', ?, ?)""", (now, now))
    vid = npc_scripts_repo.record(service="remote_access", policy_id=77,
                                   script_body="/ip service enable winbox")
    with app_ctx.test_request_context("/"):
        g.tenant_id = 1
        assert npc_scripts_repo.get_by_id(vid) is None
        assert npc_scripts_repo.latest_for_policy(
            service="remote_access", policy_id=77) is None
        assert npc_scripts_repo.list_for_policy(
            service="remote_access", policy_id=77) == []
    with app_ctx.test_request_context("/"):
        g.tenant_id = 2
        assert npc_scripts_repo.get_by_id(vid) is not None


# ─────────────── MT12/MT13: سقوف الجهة وحجب اللوحة ───────────────

def test_tenant_capacity_caps(app_ctx):
    from datetime import datetime as _dt
    from app.radius.db.connection import db
    from app.radius.services.tenants import tenant_capacity_block_reason
    now = _dt.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO tenants (id, slug, name, display_name, status,
                                 max_subscribers, max_nas, created_at)
           VALUES (2, 'acme', 'ACME', 'ACME', 'active', 1, 1, ?)""", (now,))
    assert tenant_capacity_block_reason(2, "subscriber") == ""
    db().execute(
        "INSERT INTO subscribers (tenant_id, username, created_at) VALUES (2, 'u1', ?)",
        (now,))
    assert "سقف المشتركين" in tenant_capacity_block_reason(2, "subscriber")
    # الكروت لا تُحتسب في العدّاد
    db().execute(
        "INSERT INTO subscribers (tenant_id, username, user_type, created_at) "
        "VALUES (2, 'c1', 'card', ?)", (now,))
    assert "سقف المشتركين" in tenant_capacity_block_reason(2, "subscriber")
    # سقف الأجهزة
    assert tenant_capacity_block_reason(2, "nas") == ""
    _seed_nas_device(2, "r1", "10.10.0.7")
    assert "سقف أجهزة" in tenant_capacity_block_reason(2, "nas")
    # سقف 0 = بلا حد (الجهة الافتراضية بسقف الفئة، نصفّره يدويًا)
    db().execute("UPDATE tenants SET max_subscribers = 0 WHERE id = 2")
    assert tenant_capacity_block_reason(2, "subscriber") == ""


def test_blocked_tenant_admin_locked_out(app_ctx):
    past = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
    _seed_tenant(2, "acme", status="trial", trial_ends_at=past)
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import admins_repo, tenants_repo
    admins_repo.ensure_default_roles()
    op_role = admins_repo.get_role_by_name("operator")
    admin = admins_repo.create_admin(username="acme-admin", password="secret123",
                                      role_id=op_role.id, is_super_admin=False)
    tenants_repo.add_membership(TenantMembership(
        id=None, tenant_id=2, admin_id=admin.id, role_id=op_role.id, status="active"))
    client = app_ctx.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id
        s["admin_user"] = "acme-admin"
        s["is_super_admin"] = False
        s["tenant_id"] = 2
    r = client.get("/admin/radius/tenants")
    assert r.status_code == 403
    assert "انتهت الفترة التجريبية" in r.get_data(as_text=True)
    # الخروج غير محجوب (المسار المستثنى)
    r2 = client.get("/admin/radius/logout")
    assert r2.status_code in (200, 302)


def test_blocked_tenant_super_admin_not_locked(app_ctx):
    past = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
    _seed_tenant(2, "acme", status="trial", trial_ends_at=past)
    client = app_ctx.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "admin"
        s["is_super_admin"] = True
        s["tenant_id"] = 2
    r = client.get("/admin/radius/tenants")
    # ليس 403 الحجب — السوبر يمرّ (قد يقابل بوابة المنحة 302، وهذا شأن آخر)
    assert r.status_code != 403


# ─────────────── MT15: عزل الجهة عبر X-Tenant وأسطح المزوّد ───────────────

def _seed_operator(tid: int):
    """يبذر مدير operator غير-سوبر بعضوية في الجهة tid ويعيد admin."""
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import admins_repo, tenants_repo
    admins_repo.ensure_default_roles()
    role = admins_repo.get_role_by_name("operator")
    admin = admins_repo.create_admin(username=f"op{tid}", password="secret123",
                                      role_id=role.id, is_super_admin=False)
    tenants_repo.add_membership(TenantMembership(
        id=None, tenant_id=tid, admin_id=admin.id, role_id=role.id, status="active"))
    return admin


def test_x_tenant_header_ignored_for_non_member(app_ctx):
    _seed_tenant(2, "acme")
    _seed_tenant(3, "beta")
    admin = _seed_operator(2)
    from app.radius.middleware.tenant_resolver import _resolve_from_request
    from app.radius.stores.tenants_store import TenantsStore
    store = TenantsStore.instance()
    with app_ctx.test_request_context("/admin/radius/users",
                                      headers={"X-Tenant": "beta"}):
        from flask import session
        session["admin_id"] = admin.id
        session["is_super_admin"] = False
        session["tenant_id"] = 2
        # يطلب beta (3) وهو ليس عضوًا → يُتجاهل ويرتدّ لجهته (2)
        resolved = _resolve_from_request(store)
        assert resolved.id == 2


def test_x_tenant_header_honored_for_super(app_ctx):
    _seed_tenant(2, "acme")
    from app.radius.middleware.tenant_resolver import _resolve_from_request
    from app.radius.stores.tenants_store import TenantsStore
    store = TenantsStore.instance()
    with app_ctx.test_request_context("/admin/radius/users",
                                      headers={"X-Tenant": "acme"}):
        from flask import session
        session["admin_id"] = 1
        session["is_super_admin"] = True
        session["tenant_id"] = 1
        assert _resolve_from_request(store).id == 2


def test_forged_session_tenant_rejected_for_non_member(app_ctx):
    _seed_tenant(2, "acme")
    _seed_tenant(3, "beta")
    admin = _seed_operator(2)
    from app.radius.middleware.tenant_resolver import _resolve_from_request
    from app.radius.stores.tenants_store import TenantsStore
    store = TenantsStore.instance()
    with app_ctx.test_request_context("/admin/radius/users"):
        from flask import session
        session["admin_id"] = admin.id
        session["is_super_admin"] = False
        session["tenant_id"] = 3   # جلسة مزوّرة لجهة غير عضو فيها
        assert _resolve_from_request(store).id == 2


def test_provider_surfaces_blocked_for_operator(app_ctx):
    admin = _seed_operator(1)  # عضو في الجهة الافتراضية، لكنه ليس سوبر
    client = app_ctx.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = admin.id
        s["admin_user"] = "op1"
        s["is_super_admin"] = False
        s["tenant_id"] = 1
    for path in ("/admin/radius/monitoring",
                 "/admin/radius/vpn-accounts",
                 "/admin/radius/wg-data",
                 "/admin/radius/_status"):
        r = client.get(path)
        assert r.status_code in (403, 302), f"{path} => {r.status_code}"


# ─────────────── MT16: وضع الاستضافة المفتوحة ───────────────

def test_open_hosting_grants_all_provider_gates(app_ctx, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_OPEN_HOSTING", "1")
    from app.radius.services import provider_grant
    # كل خدمة/قدرة ممنوحة بلا عقد
    assert not provider_grant.is_service_disabled(1, "multi_tenant")
    assert not provider_grant.requires_upgrade(1, "anything")
    assert provider_grant.is_capability_granted(1, "tenants")
    assert provider_grant.is_capability_granted(1, "sections")
    g = provider_grant.lookup(1, "multi_tenant")
    assert g.present and g.enabled and not g.disabled


def test_open_hosting_bypasses_license_lifecycle(app_ctx, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_OPEN_HOSTING", "1")
    # لا بوابة LICENSE_GATE_TEST_BYPASS هنا — الاعتماد على open_hosting وحده
    monkeypatch.delenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", raising=False)
    from app.radius.services.license_lifecycle import evaluate
    d = evaluate(2)   # جهة بلا أي لقطة ترخيص
    assert not d.blocks_panel
    assert d.reason == "open_hosting"


def test_open_hosting_unlimited_tenants(app_ctx, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_OPEN_HOSTING", "1")
    from app.radius.services.tenants import _install_entity_limit
    assert _install_entity_limit() >= 1_000_000


def test_open_hosting_off_by_default_keeps_gates(app_ctx):
    # بلا المتغيّر: القدرات default-off تبقى محجوبة (لا تسريب سلوك)
    from app.radius.services import provider_grant
    assert not provider_grant.is_capability_granted(1, "tenants")


def test_open_hosting_keeps_per_tenant_caps(app_ctx, monkeypatch):
    # الحدود لكل جهة (تحكّم المالك) تبقى مُنفَّذة حتى في الوضع المفتوح
    monkeypatch.setenv("HOBERADIUS_OPEN_HOSTING", "1")
    from datetime import datetime as _dt
    from app.radius.db.connection import db
    from app.radius.services.tenants import tenant_capacity_block_reason
    now = _dt.utcnow().isoformat() + "Z"
    db().execute(
        """INSERT INTO tenants (id, slug, name, display_name, status,
                                 max_subscribers, created_at)
           VALUES (2, 'acme', 'ACME', 'ACME', 'active', 1, ?)""", (now,))
    db().execute(
        "INSERT INTO subscribers (tenant_id, username, created_at) VALUES (2,'u1',?)",
        (now,))
    assert "سقف المشتركين" in tenant_capacity_block_reason(2, "subscriber")


# ─────────────── MT22: توجيه المسار باسم الشبكة ───────────────

def test_path_routing_admin_and_portal(app_ctx):
    from app.radius.middleware.tenant_path import invalidate_slug_cache
    _seed_tenant(2, "ahmad1")
    invalidate_slug_cache()
    c = app_ctx.test_client()
    # لوحة المدير والبوابة تحت بادئة الشبكة
    assert c.get("/ahmad1/admin/radius/login").status_code == 200
    assert c.get("/ahmad1/portal/subscriber/login").status_code == 200
    # الروابط تُولَّد ببادئة /ahmad1 (SCRIPT_NAME)
    body = c.get("/ahmad1/admin/radius/login").get_data(as_text=True)
    assert "/ahmad1/" in body
    # مسار بلا slug ما زال يعمل (المزوّد)
    assert c.get("/admin/radius/login").status_code == 200
    # اسم غير موجود = تمرير عاديّ → 404
    assert c.get("/nosuch/admin/radius/login").status_code == 404


def test_path_routing_operator_isolation(app_ctx):
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.middleware.tenant_path import invalidate_slug_cache
    _seed_tenant(2, "ahmad1")
    _seed_tenant(3, "mohamed2")
    invalidate_slug_cache()
    admins_repo.ensure_default_roles()
    role = admins_repo.get_role_by_name("operator")
    op = admins_repo.create_admin(username="ah-op", password="secret123",
                                   role_id=role.id, is_super_admin=False)
    tenants_repo.add_membership(TenantMembership(
        id=None, tenant_id=2, admin_id=op.id, role_id=role.id, status="active"))
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = op.id
        s["is_super_admin"] = False
        s["tenant_id"] = 2
    # شبكته سليمة
    assert c.get("/ahmad1/admin/radius/").status_code == 200
    # شبكة أخرى → 403 (عزل)
    assert c.get("/mohamed2/admin/radius/").status_code == 403


# ─────────────── MT24: النسخ الاحتياطي المعزول لكل شبكة ───────────────

def test_tenant_backup_isolation(app_ctx):
    import gzip
    import json
    from app.radius.db.connection import db
    from app.radius.services import tenant_backup
    _seed_tenant(2, "neta")
    _seed_tenant(3, "netb")
    now = "2026-01-01T00:00:00Z"
    for u in ("a1", "a2", "a3"):
        db().execute("INSERT INTO subscribers(tenant_id,username,password,status,created_at) "
                     "VALUES(2,?,'x','enabled',?)", (u, now))
    db().execute("INSERT INTO subscribers(tenant_id,username,password,status,created_at) "
                 "VALUES(3,'b1','x','enabled',?)", (now,))
    db().commit()
    # النسخة تحتوي بيانات الشبكة فقط
    info = tenant_backup.export_tenant(2, actor="t")
    assert info["rows"] == 3
    name = tenant_backup.list_tenant_backups(2)[0]["name"]
    raw = tenant_backup.read_backup_bytes(2, name)
    payload = json.loads(gzip.decompress(raw))
    assert sorted(r["username"] for r in payload["tables"]["subscribers"]) == ["a1", "a2", "a3"]
    assert all(r["tenant_id"] == 2 for tbl in payload["tables"].values() for r in tbl)
    # الاستعادة تعيد الشبكة ولا تمسّ غيرها
    db().execute("DELETE FROM subscribers WHERE tenant_id=2 AND username='a1'")
    db().execute("INSERT INTO subscribers(tenant_id,username,password,status,created_at) "
                 "VALUES(3,'b2','x','enabled',?)", (now,))
    db().commit()
    tenant_backup.restore_tenant(2, name, actor="t")
    a = sorted(r["username"] for r in db().execute(
        "SELECT username FROM subscribers WHERE tenant_id=2").fetchall())
    b = sorted(r["username"] for r in db().execute(
        "SELECT username FROM subscribers WHERE tenant_id=3").fetchall())
    assert a == ["a1", "a2", "a3"]      # الشبكة عادت
    assert b == ["b1", "b2"]            # الشبكة الأخرى لم تتأثر


def test_tenant_backup_rejects_cross_tenant_restore(app_ctx):
    from app.radius.services import tenant_backup
    _seed_tenant(2, "neta")
    _seed_tenant(3, "netb")
    tenant_backup.export_tenant(2)
    name = tenant_backup.list_tenant_backups(2)[0]["name"]
    # محاولة استعادة نسخة neta في netb → رفض (ملف الشبكة في مجلدها فقط)
    import pytest as _pt
    with _pt.raises((ValueError, FileNotFoundError)):
        tenant_backup.restore_tenant(3, name)


# ─────────────── MT26: عزل قائمة/إدارة المدراء لكل شبكة ───────────────

def test_admins_list_scoped_per_tenant(app_ctx):
    from flask import g
    from app.radius.core.tenant import TenantMembership
    from app.radius.db.repos import admins_repo, tenants_repo
    _seed_tenant(2, "neta")
    _seed_tenant(3, "netb")
    admins_repo.ensure_default_roles()
    role = admins_repo.get_role_by_name("operator")
    a = admins_repo.create_admin(username="neta-mgr", password="secret123",
                                  role_id=role.id, is_super_admin=False)
    b = admins_repo.create_admin(username="netb-mgr", password="secret123",
                                  role_id=role.id, is_super_admin=False)
    tenants_repo.add_membership(TenantMembership(id=None, tenant_id=2, admin_id=a.id,
                                                 role_id=role.id, status="active"))
    tenants_repo.add_membership(TenantMembership(id=None, tenant_id=3, admin_id=b.id,
                                                 role_id=role.id, status="active"))
    with app_ctx.test_request_context("/"):
        g.tenant_id = 2
        names = {x.username for x in admins_repo.list_admins()}
        assert "neta-mgr" in names and "netb-mgr" not in names
    with app_ctx.test_request_context("/"):
        g.tenant_id = 3
        names = {x.username for x in admins_repo.list_admins()}
        assert "netb-mgr" in names and "neta-mgr" not in names
    # all_tenants=True يُرجع الكلّ (للمزوّد)
    alln = {x.username for x in admins_repo.list_admins(all_tenants=True)}
    assert {"neta-mgr", "netb-mgr"} <= alln


def test_business_operators_scoped_and_session_synced(app_ctx):
    """MT26/MT27 — صفحة «المدراء والموزعون» تعرض مدراء الشبكة فقط، وجهة
    المسار تُزامن الجلسة (المسارات القديمة تقرأ session["tenant_id"])."""
    import re as _re
    from app.radius.core.tenant import Tenant
    from app.radius.db.repos import admins_repo
    from app.radius.middleware.tenant_path import invalidate_slug_cache
    from app.radius.services.tenants import get_tenants_service
    admins_repo.ensure_default_roles()
    get_tenants_service().create_trial(
        actor="a", tenant=Tenant(id=None, slug="neta", name="NetA", status="trial"),
        trial_days=14, operator_username="neta-op", operator_password="pass123456")
    get_tenants_service().create_trial(
        actor="a", tenant=Tenant(id=None, slug="netb", name="NetB", status="trial"),
        trial_days=14, operator_username="netb-op", operator_password="pass123456")
    invalidate_slug_cache()
    c = app_ctx.test_client()
    h = c.get("/admin/radius/login").get_data(as_text=True)
    tok = _re.search(r'name="_csrf_token" value="([^"]+)"', h).group(1)
    c.post("/admin/radius/login",
           data={"username": "admin", "password": "123456789", "_csrf_token": tok})
    body = c.get("/neta/admin/radius/business-operators").get_data(as_text=True)
    assert "neta-op" in body          # مدراء الشبكة
    assert "netb-op" not in body      # لا تسريب من شبكة أخرى
    # جهة المسار زامنت الجلسة
    with c.session_transaction() as s:
        assert int(s.get("tenant_id") or 0) != 1


# ─────────────── MT1: إعداد FreeRADIUS ───────────────

def test_freeradius_sql_config_is_tenant_aware():
    cfg = (Path(__file__).resolve().parents[1]
           / "deploy" / "freeradius" / "mods-enabled" / "sql").read_text(
               encoding="utf-8")
    # radacct start + radpostauth كلاهما يشتق tenant_id من جدول nas.
    assert cfg.count("COALESCE((SELECT tenant_id FROM nas") >= 2
    # لا يبقى tenant_id مثبّتًا على 1 في أي INSERT.
    assert "(1, '%{Acct-Session-Id}'" not in cfg
    assert "(1, '%{User-Name}'" not in cfg
