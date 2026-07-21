"""MT33 — حذف شبكة نهائيًّا: يمحو شبكته وحدها، بحذرٍ ثلاثيّ.

يُثبت أن الحذف: (١) يَرفض بلا تأكيدٍ نصّيّ صحيح، (٢) يَرفض جهة المزوّد،
(٣) يأخذ نسخة أمان تبقى بعد الاختفاء، (٤) **لا يمسّ الشبكة الأخرى** في
أي جدول، (٥) يَحذف مدراء الشبكة وحدهم ويُبقي المدير المشترك، (٦) يُبطل
رابط /slug/ فورًا، (٧) مقصورٌ على المالك الرئيسي.
"""
from __future__ import annotations

import os
import re

import pytest

PW = "netpass12345"
NOW = "2026-01-01T00:00:00Z"


@pytest.fixture
def app_two(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "del.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_OPEN_HOSTING", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.core.tenant import Tenant
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        from app.radius.middleware.tenant_path import invalidate_slug_cache
        from app.radius.services.tenants import get_tenants_service
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        svc = get_tenants_service()
        for slug in ("gone", "keep"):
            svc.create_trial(
                actor="provider",
                tenant=Tenant(id=None, slug=slug, name=slug.upper(),
                              display_name=slug.upper(), status="trial"),
                trial_days=30, operator_username=f"{slug}-own", operator_password=PW)
        invalidate_slug_cache()
        app.tids = {t.slug: t.id for t in tenants_repo.list_tenants()}  # type: ignore[attr-defined]
        for slug, tag in (("gone", "G"), ("keep", "K")):
            _seed(app.tids[slug], tag)
    return app


def _seed(tid: int, tag: str) -> None:
    from app.radius.db.connection import db
    c = db()
    c.execute("INSERT INTO access_plans(tenant_id,name,plan_type,service_type,created_at) "
              "VALUES(?,?,'time','Hotspot',?)", (tid, f"plan-{tag}", NOW))
    pid = c.execute("SELECT id FROM access_plans WHERE tenant_id=?", (tid,)).fetchone()["id"]
    c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,created_at) "
              "VALUES(?,?,?,?,2,?)", (tid, f"B-{tag}", f"pkg-{tag}", pid, NOW))
    bid = c.execute("SELECT id FROM card_batches WHERE tenant_id=?", (tid,)).fetchone()["id"]
    for i in (1, 2):
        c.execute("INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,created_at) "
                  "VALUES(?,?,?,'p',?,?)", (tid, bid, f"card-{tag}{i}", pid, NOW))
    c.execute("INSERT INTO subscribers(tenant_id,username,password,user_type,status,plan_id,created_at) "
              "VALUES(?,?,'p','subscriber','enabled',?,?)", (tid, f"sub-{tag}", pid, NOW))
    c.execute("INSERT INTO nas_devices(tenant_id,name,address,secret,created_at) "
              "VALUES(?,?,?,'s',?)", (tid, f"rtr-{tag}", f"10.5{0 if tag=='G' else 1}.0.1", NOW))
    c.commit()


def _login_owner(app):
    """المالك الرئيسي (bootstrap admin id=1) عبر جلسة مباشرة."""
    from app.radius.db.repos.admins_repo import get_admin
    c = app.test_client()
    with app.app_context():
        a = get_admin(1)
        from app.radius.services.admins import get_admins_service
        perms = list(get_admins_service().permissions_of(a))
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user=a.username, admin_name=a.full_name,
                 is_super_admin=True, tenant_id=1, permissions=perms)
    return c


def _login(app, slug, user, pw):
    c = app.test_client()
    html = c.get(f"/{slug}/admin/radius/login").get_data(as_text=True)
    tok = re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)
    c.post(f"/{slug}/admin/radius/login",
           data={"username": user, "password": pw, "_csrf_token": tok})
    return c


# ─────────────── الحماية ───────────────

def test_wrong_confirmation_deletes_nothing(app_two):
    app = app_two
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import tenant_delete
        with pytest.raises(tenant_delete.TenantDeleteError):
            tenant_delete.delete_tenant(app.tids["gone"], confirm_slug="", actor="t")
        with pytest.raises(tenant_delete.TenantDeleteError):
            tenant_delete.delete_tenant(app.tids["gone"], confirm_slug="GONE", actor="t")
        assert db().execute("SELECT 1 FROM tenants WHERE slug='gone'").fetchone()
        assert db().execute("SELECT COUNT(*) n FROM subscribers WHERE tenant_id=?",
                            (app.tids["gone"],)).fetchone()["n"] == 1


def test_provider_tenant_is_undeletable(app_two):
    app = app_two
    with app.app_context():
        from app.radius.services import tenant_delete
        with pytest.raises(tenant_delete.TenantDeleteError):
            tenant_delete.delete_tenant(1, confirm_slug="default", actor="t")


def test_only_primary_owner_may_delete(app_two):
    """مالك شبكة لا يَحذف شبكته ولا غيرها — ولا يرى صفحة التأكيد."""
    app = app_two
    gone = app.tids["gone"]
    c = _login(app, "gone", "gone-own", PW)
    assert c.get(f"/gone/admin/radius/tenants/{gone}/delete").status_code == 403
    with c.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    assert c.post(f"/gone/admin/radius/tenants/{gone}/delete",
                  data={"confirm_slug": "gone", "_csrf_token": tok}).status_code == 403
    with app.app_context():
        from app.radius.db.connection import db
        assert db().execute("SELECT 1 FROM tenants WHERE slug='gone'").fetchone()


# ─────────────── الحذف الصحيح ───────────────

def test_delete_removes_only_its_own_network(app_two):
    app = app_two
    gone, keep = app.tids["gone"], app.tids["keep"]
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import tenant_delete
        out = tenant_delete.delete_tenant(gone, confirm_slug="gone", actor="المالك")
        assert out["rows"] > 0
        c = db()
        assert not c.execute("SELECT 1 FROM tenants WHERE id=?", (gone,)).fetchone()
        for table in ("access_plans", "card_batches", "cards", "subscribers", "nas_devices"):
            assert c.execute(f"SELECT COUNT(*) n FROM {table} WHERE tenant_id=?",
                             (gone,)).fetchone()["n"] == 0, f"{table}: بقيت صفوف"
        # الشبكة الأخرى سليمة تمامًا
        assert c.execute("SELECT 1 FROM tenants WHERE id=?", (keep,)).fetchone()
        for table, n in (("access_plans", 1), ("card_batches", 1), ("cards", 2),
                         ("subscribers", 1), ("nas_devices", 1)):
            assert c.execute(f"SELECT COUNT(*) n FROM {table} WHERE tenant_id=?",
                             (keep,)).fetchone()["n"] == n, f"{table}: تأثّرت الشبكة الباقية!"
        assert c.execute("SELECT 1 FROM admins WHERE username='keep-own'").fetchone()
        assert not c.execute("SELECT 1 FROM admins WHERE username='gone-own'").fetchone()


def test_safety_backup_survives_the_delete(app_two):
    app = app_two
    gone = app.tids["gone"]
    with app.app_context():
        import gzip
        from app.radius.services import tenant_backup, tenant_delete
        out = tenant_delete.delete_tenant(gone, confirm_slug="gone", actor="t")
        assert out["safety_backup"]
        raw = tenant_backup.read_backup_bytes(gone, out["safety_backup"])
        assert raw, "نسخة الأمان اختفت مع الشبكة!"
        blob = gzip.decompress(raw).decode("utf-8")
        assert "sub-G" in blob and "card-G1" in blob   # بيانات الشبكة محفوظة
        assert "sub-K" not in blob                      # ولا شيء من الأخرى


def test_slug_route_dies_immediately(app_two):
    app = app_two
    with app.app_context():
        from app.radius.services import tenant_delete
        tenant_delete.delete_tenant(app.tids["gone"], confirm_slug="gone", actor="t")
    c = app.test_client()
    # لم يعد slug صالحًا → لا يُقشَّر من المسار → 404 بدل لوحة الشبكة
    assert c.get("/gone/admin/radius/login").status_code == 404
    assert c.get("/keep/admin/radius/login").status_code == 200


def test_shared_admin_is_kept_and_unlinked(app_two):
    """مديرٌ عضوٌ في الشبكتين: يبقى حسابه وتُنزَع عضويّته من المحذوفة فقط."""
    app = app_two
    gone, keep = app.tids["gone"], app.tids["keep"]
    with app.app_context():
        from app.radius.core.tenant import TenantMembership
        from app.radius.db.connection import db
        from app.radius.db.repos import admins_repo, tenants_repo
        from app.radius.services import tenant_delete
        shared = admins_repo.create_admin(username="shared-mgr", password="x12345678",
                                          full_name="مشترك", role_id=None)
        for tid in (gone, keep):
            tenants_repo.add_membership(TenantMembership(
                id=None, tenant_id=tid, admin_id=shared.id))
        tenant_delete.delete_tenant(gone, confirm_slug="gone", actor="t")
        c = db()
        assert c.execute("SELECT 1 FROM admins WHERE username='shared-mgr'").fetchone(), \
            "حُذف مديرٌ له عضوية في شبكة أخرى!"
        rows = c.execute("SELECT tenant_id FROM tenant_memberships WHERE admin_id=?",
                         (shared.id,)).fetchall()
        assert [r["tenant_id"] for r in rows] == [keep]


def test_provider_chat_of_deleted_network_is_purged(app_two):
    app = app_two
    gone, keep = app.tids["gone"], app.tids["keep"]
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import provider_chat, tenant_delete
        provider_chat.post_message(tenant_id=gone, sender="provider", body="مراسلة المحذوفة")
        provider_chat.post_message(tenant_id=keep, sender="provider", body="مراسلة الباقية")
        tenant_delete.delete_tenant(gone, confirm_slug="gone", actor="t")
        assert db().execute("SELECT COUNT(*) n FROM provider_chat_messages WHERE tenant_id=?",
                            (gone,)).fetchone()["n"] == 0
        assert [m["body"] for m in provider_chat.list_messages(tenant_id=keep)] == \
            ["مراسلة الباقية"]


def test_http_flow_owner_confirm_then_delete(app_two):
    """المسار الكامل كما في المتصفّح: صفحة التأكيد ثم التنفيذ."""
    app = app_two
    gone = app.tids["gone"]
    c = _login_owner(app)
    page = c.get(f"/admin/radius/tenants/{gone}/delete")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "gone" in body and "لا رجعة فيه" in body
    with c.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    # تأكيد خاطئ → لا حذف
    c.post(f"/admin/radius/tenants/{gone}/delete",
           data={"confirm_slug": "خطأ", "_csrf_token": tok}, follow_redirects=True)
    with app.app_context():
        from app.radius.db.connection import db
        assert db().execute("SELECT 1 FROM tenants WHERE id=?", (gone,)).fetchone()
    # تأكيد صحيح → حُذفت
    r = c.post(f"/admin/radius/tenants/{gone}/delete",
               data={"confirm_slug": "gone", "_csrf_token": tok}, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        from app.radius.db.connection import db
        assert not db().execute("SELECT 1 FROM tenants WHERE id=?", (gone,)).fetchone()
