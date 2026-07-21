"""MT34 — اسم المدير يتفرّد **لكل شبكة**، لا عالميًّا.

عقد المنتج: كل شبكة «ريديوس» مستقلّ لا يعلم بوجود غيره. فـ:

    الشبكة (أ): ahmad / 791994
    الشبكة (ب): ahmad / 123456

شخصان مختلفان تمامًا. تشابه الاسم لا يعني مديرًا مشتركًا، ولا يجوز أن
يَمنع أحدهما الآخر، ولا أن يُفشي لأحدهما وجود الآخر، ولا أن يَدخل أحدهما
بكلمة الآخر.
"""
from __future__ import annotations

import os
import re

import pytest

PW_A = "pass-A-791994"
PW_B = "pass-B-123456"


@pytest.fixture
def app_two(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "names.db")
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
        # نفس اسم المالك في الشبكتين بكلمتَي مرور مختلفتين — جوهر الاختبار
        svc.create_trial(actor="p", tenant=Tenant(id=None, slug="neta", name="A",
                                                  display_name="A", status="trial"),
                         trial_days=30, operator_username="ahmad", operator_password=PW_A)
        svc.create_trial(actor="p", tenant=Tenant(id=None, slug="netb", name="B",
                                                  display_name="B", status="trial"),
                         trial_days=30, operator_username="ahmad", operator_password=PW_B)
        invalidate_slug_cache()
        app.tids = {t.slug: t.id for t in tenants_repo.list_tenants()}  # type: ignore[attr-defined]
    return app


def _login(app, prefix, user, pw):
    c = app.test_client()
    html = c.get(f"{prefix}/admin/radius/login").get_data(as_text=True)
    m = re.search(r'name="_csrf_token" value="([^"]+)"', html)
    r = c.post(f"{prefix}/admin/radius/login",
               data={"username": user, "password": pw,
                     "_csrf_token": m.group(1) if m else ""})
    return c, r


# ─────────────── الإنشاء ───────────────

def test_same_admin_name_allowed_in_both_networks(app_two):
    """الاسم نفسه في الشبكتين = حسابان مختلفان، لا تصادم."""
    app = app_two
    with app.app_context():
        from app.radius.db.connection import db
        rows = db().execute(
            "SELECT id, username, tenant_id FROM admins WHERE username='ahmad' "
            "ORDER BY tenant_id").fetchall()
        assert len(rows) == 2, f"لم يُنشأ حسابان مستقلّان: {[dict(r) for r in rows]}"
        assert {r["tenant_id"] for r in rows} == {app.tids["neta"], app.tids["netb"]}
        assert rows[0]["id"] != rows[1]["id"]


def test_second_network_creation_not_blocked_by_first(app_two):
    """إنشاء مدير باسمٍ تستعمله شبكةٌ أخرى لا يُرفض ولا يُفشي وجودها."""
    app = app_two
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import admins_repo
        role = db().execute("SELECT id FROM roles WHERE name='operator'").fetchone()["id"]
        a = admins_repo.create_admin(username="khaled", password="x12345678",
                                     role_id=role, tenant_id=app.tids["neta"])
        b = admins_repo.create_admin(username="khaled", password="y87654321",
                                     role_id=role, tenant_id=app.tids["netb"])
        assert a.id != b.id
        # ...لكن التكرار **داخل نفس الشبكة** يبقى ممنوعًا
        with pytest.raises(ValueError):
            admins_repo.create_admin(username="khaled", password="z11223344",
                                     role_id=role, tenant_id=app.tids["neta"])


def test_creation_via_http_in_second_network_succeeds(app_two):
    """السيناريو الحيّ الذي كان يفشل بـ«already exists»."""
    app = app_two
    with app.app_context():
        from app.radius.db.connection import db
        role = db().execute("SELECT id FROM roles WHERE name='operator'").fetchone()["id"]
    made = []
    for slug, pw in (("neta", PW_A), ("netb", PW_B)):
        c, _ = _login(app, f"/{slug}", "ahmad", pw)
        with c.session_transaction() as s:
            tok = s.get("_csrf_token", "")
        c.post(f"/{slug}/admin/radius/admins",
               data={"username": "saeed", "password": "saeedpass123",
                     "full_name": f"سعيد {slug}", "role_id": str(role),
                     "enabled": "1", "_csrf_token": tok}, follow_redirects=True)
        made.append(slug)
    with app.app_context():
        from app.radius.db.connection import db
        rows = db().execute("SELECT tenant_id FROM admins WHERE username='saeed'").fetchall()
        assert len(rows) == 2, "الشبكة الثانية مُنعت من اسمٍ تستعمله الأولى"
        assert {r["tenant_id"] for r in rows} == {app.tids["neta"], app.tids["netb"]}


# ─────────────── الدخول ───────────────

def test_each_ahmad_logs_in_with_his_own_password(app_two):
    app = app_two
    for slug, pw, tid_key in (("neta", PW_A, "neta"), ("netb", PW_B, "netb")):
        c, r = _login(app, f"/{slug}", "ahmad", pw)
        assert r.status_code in (200, 302), f"{slug}: تعذّر دخول ahmad بكلمته"
        home = c.get(f"/{slug}/admin/radius/")
        assert home.status_code == 200, f"{slug}: ahmad لم يصل لوحته"
        with c.session_transaction() as s:
            assert s.get("tenant_id") == app.tids[tid_key], \
                f"{slug}: ahmad هبط في الشبكة الخطأ"


def test_password_of_one_network_never_opens_the_other(app_two):
    """كلمة ahmad الشبكة (أ) لا تفتح ahmad الشبكة (ب) — وهو جوهر الخطر."""
    app = app_two
    for slug, wrong_pw in (("neta", PW_B), ("netb", PW_A)):
        c, r = _login(app, f"/{slug}", "ahmad", wrong_pw)
        assert r.status_code == 401, f"{slug}: قُبلت كلمة مرور مدير الشبكة الأخرى!"
        assert c.get(f"/{slug}/admin/radius/").status_code != 200


def test_admin_of_one_network_cannot_enter_the_other(app_two):
    """ahmad الشبكة (أ) — حتى بكلمته الصحيحة — لا يبلغ لوحة (ب)."""
    app = app_two
    c, _ = _login(app, "/neta", "ahmad", PW_A)
    assert c.get("/netb/admin/radius/").status_code == 403
    assert c.get("/netb/admin/radius/subscribers").status_code == 403


def test_lookup_is_scoped_not_global(app_two):
    """البحث بالاسم داخل شبكةٍ لا يُرجع مدير شبكةٍ أخرى."""
    app = app_two
    with app.app_context():
        from app.radius.db.repos import admins_repo
        a = admins_repo.get_by_username("ahmad", tenant_id=app.tids["neta"])
        b = admins_repo.get_by_username("ahmad", tenant_id=app.tids["netb"])
        assert a and b and a.id != b.id
        assert a.password_hash != b.password_hash


# ─────────────── حساب المزوّد ───────────────

def test_provider_owner_is_global_and_may_enter_any_network(app_two):
    """قرار المالك: حساب المزوّد (tenant_id NULL) يدخل أي شبكة للدعم."""
    app = app_two
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT id, username, tenant_id FROM admins "
                           "ORDER BY id LIMIT 1").fetchone()
        assert row["tenant_id"] is None, "حساب المزوّد لم يَبقَ عامًّا"
        from app.radius.db.repos import admins_repo
        for slug in ("neta", "netb"):
            found = admins_repo.get_by_username(row["username"],
                                                tenant_id=app.tids[slug])
            assert found and found.id == row["id"], \
                f"حساب المزوّد لا يُرى داخل {slug}"


def test_network_cannot_shadow_the_provider_support_account(app_two):
    """اسم حساب المزوّد محجوز في كل الشبكات — عمدًا.

    لو سُمح لشبكةٍ بمديرٍ يحمل اسم حساب الدعم لَحَجَبَته في لوحتها وفقد
    المزوّد قدرته على دخولها للدعم (وهي القدرة التي اختارها المالك).
    التسريب هنا مقبول: الاسم يخصّ حساب المنصّة نفسها لا عميلًا آخر.
    """
    app = app_two
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import admins_repo
        owner = db().execute("SELECT id, username FROM admins ORDER BY id LIMIT 1").fetchone()
        role = db().execute("SELECT id FROM roles WHERE name='operator'").fetchone()["id"]
        with pytest.raises(ValueError):
            admins_repo.create_admin(username=owner["username"], password="local1234",
                                     role_id=role, tenant_id=app.tids["neta"])


def test_local_admin_wins_if_a_clash_already_exists(app_two):
    """شبكة قديمة فيها اسمٌ يطابق حساب المزوّد (بيانات ما قبل MT34):
    صاحب البيت يفوز في لوحته، ويبقى حساب المزوّد مطابقًا في غيرها."""
    app = app_two
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import admins_repo
        owner = db().execute("SELECT id, username FROM admins ORDER BY id LIMIT 1").fetchone()
        role = db().execute("SELECT id FROM roles WHERE name='operator'").fetchone()["id"]
        # نُدخل التصادم مباشرةً كما لو ورثناه من قاعدة قديمة
        db().execute(
            "INSERT INTO admins(username,password_hash,role_id,enabled,created_at,tenant_id) "
            "VALUES(?,?,?,1,'2026-01-01T00:00:00Z',?)",
            (owner["username"], "x", role, app.tids["neta"]))
        db().commit()
        local = db().execute("SELECT id FROM admins WHERE username=? AND tenant_id=?",
                             (owner["username"], app.tids["neta"])).fetchone()["id"]
        found = admins_repo.get_by_username(owner["username"], tenant_id=app.tids["neta"])
        assert found.id == local, "حساب المزوّد حَجَب مدير الشبكة في لوحته"
        other = admins_repo.get_by_username(owner["username"], tenant_id=app.tids["netb"])
        assert other.id == owner["id"]
