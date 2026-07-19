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
