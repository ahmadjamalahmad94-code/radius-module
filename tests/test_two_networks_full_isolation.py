"""MT31 — اختبار عزل شامل: شبكتان كاملتان بكل عمليات الريديوس.

يبني شبكتين مستقلّتين (netA / netB) وينفّذ على كلٍّ منهما **كل** العمليات
التشغيلية (باقة، حزمة كروت + كروت، مشتركون، مدير، موزّع، راوتر، عرض متجر،
جلسة محاسبة، نسخة احتياطية) ثم يتحقّق من:

  1. عزل البيانات على مستوى كل كيان (كل شبكة ترى بياناتها فقط).
  2. عزل الواجهة (صفحات كل شبكة لا تعرض بيانات الأخرى).
  3. منع الوصول عبر الشبكات (403 على مسار شبكة أخرى).
  4. منع IDOR: محاولة فتح كيان شبكة أخرى بمعرّفه المباشر.
  5. عزل النسخ الاحتياطية (النسخة تحوي بيانات شبكتها؛ الاستعادة لا تمسّ غيرها).
  6. عزل مصادقة RADIUS (نفس اسم المستخدم في الشبكتين → كلٌّ لشبكته).

شغّل وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import pytest

NOW = "2026-01-01T00:00:00Z"
PW = "netpass12345"


# ─────────────── تهيئة: شبكتان كاملتان ───────────────

@pytest.fixture
def two_networks(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "two_nets.db")
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
    ctx = {}
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
        for slug in ("neta", "netb"):
            svc.create_trial(
                actor="provider",
                tenant=Tenant(id=None, slug=slug, name=slug.upper(),
                              display_name=slug.upper(), status="trial"),
                trial_days=30, operator_username=f"{slug}-own", operator_password=PW)
        invalidate_slug_cache()
        ctx["tid"] = {t.slug: t.id for t in tenants_repo.list_tenants()}
        _seed_full_network(ctx["tid"]["neta"], "A")
        _seed_full_network(ctx["tid"]["netb"], "B")
    app.ctx = ctx  # type: ignore[attr-defined]
    yield app


def _seed_full_network(tid: int, tag: str) -> None:
    """ينفّذ كل عمليات الريديوس على شبكة واحدة."""
    from app.radius.db.connection import db
    from app.radius.db.repos import freeradius_repo
    c = db()
    # باقة (عرض)
    c.execute("INSERT INTO access_plans(tenant_id,name,plan_type,service_type,created_at) "
              "VALUES(?,?,'time','Hotspot',?)", (tid, f"plan-{tag}", NOW))
    plan_id = c.execute("SELECT id FROM access_plans WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
                        (tid,)).fetchone()["id"]
    # حزمة كروت + كروت
    c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,created_at) "
              "VALUES(?,?,?,?,?,?)", (tid, f"BATCH-{tag}", f"package-{tag}", plan_id, 3, NOW))
    batch_id = c.execute("SELECT id FROM card_batches WHERE tenant_id=? ORDER BY id DESC LIMIT 1",
                         (tid,)).fetchone()["id"]
    for i in range(1, 4):
        c.execute("INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,created_at) "
                  "VALUES(?,?,?,?,?,?)", (tid, batch_id, f"card-{tag}{i}", "cpw", plan_id, NOW))
    # مشتركون (منهم اسم مشترك بين الشبكتين لاختبار عزل المصادقة)
    for u, p in ((f"sub-{tag}1", "p1"), ("shared-user", f"pw-{tag}")):
        c.execute("INSERT INTO subscribers(tenant_id,username,password,user_type,status,"
                  "full_name,plan_id,created_at) VALUES(?,?,?,'subscriber','enabled',?,?,?)",
                  (tid, u, p, f"user {tag}", plan_id, NOW))
    # موزّع
    c.execute("INSERT INTO distributors(tenant_id,name,display_name,status,created_at) "
              "VALUES(?,?,?,'active',?)", (tid, f"dist-{tag}", f"موزع {tag}", NOW))
    # راوتر (NAS) + عميل FreeRADIUS
    addr = f"10.{90 + (0 if tag == 'A' else 1)}.0.1"
    c.execute("INSERT INTO nas_devices(tenant_id,name,address,secret,created_at) "
              "VALUES(?,?,?,?,?)", (tid, f"rtr-{tag}", addr, f"sec{tag}", NOW))
    freeradius_repo.upsert_nas_client(tid, nasname=addr, shortname=f"rtr{tag}",
                                       secret=f"sec{tag}")
    # جلسة محاسبة (radacct)
    c.execute("INSERT INTO radacct(tenant_id,acctsessionid,username,nasipaddress,acctstarttime) "
              "VALUES(?,?,?,?,?)", (tid, f"sess-{tag}", f"sub-{tag}1", addr, NOW))
    c.commit()


def _login(app, slug, user, pw):
    c = app.test_client()
    html = c.get(f"/{slug}/admin/radius/login").get_data(as_text=True)
    tok = re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)
    c.post(f"/{slug}/admin/radius/login",
           data={"username": user, "password": pw, "_csrf_token": tok})
    return c


# ─────────────── 1. عزل البيانات لكل كيان ───────────────

def test_every_entity_is_tenant_scoped(two_networks):
    """كل جدول تشغيليّ: صفوف الشبكة (أ) لا تتسرّب للشبكة (ب) والعكس."""
    app = two_networks
    a, b = app.ctx["tid"]["neta"], app.ctx["tid"]["netb"]
    with app.app_context():
        from app.radius.db.connection import db
        checks = {
            "access_plans": "name", "card_batches": "batch_code", "cards": "username",
            "subscribers": "username", "distributors": "name",
            "nas_devices": "name", "radacct": "acctsessionid", "nas": "nasname",
        }
        for table, col in checks.items():
            rows_a = [r[col] for r in db().execute(
                f"SELECT {col} FROM {table} WHERE tenant_id=?", (a,)).fetchall()]
            rows_b = [r[col] for r in db().execute(
                f"SELECT {col} FROM {table} WHERE tenant_id=?", (b,)).fetchall()]
            assert rows_a, f"{table}: الشبكة أ فارغة (البذر فشل)"
            assert rows_b, f"{table}: الشبكة ب فارغة (البذر فشل)"
            # لا تقاطع إلا في الاسم المشترك المتعمَّد (shared-user)
            overlap = (set(rows_a) & set(rows_b)) - {"shared-user"}
            assert not overlap, f"{table}: تسريب بين الشبكتين → {overlap}"


# ─────────────── 2. عزل الواجهة ───────────────

@pytest.mark.parametrize("page", [
    "/admin/radius/subscribers", "/admin/radius/plans", "/admin/radius/cards/batches",
    "/admin/radius/devices", "/admin/radius/business-operators",
    "/admin/radius/distributors",
])
def test_pages_show_only_own_network(two_networks, page):
    """صفحات الشبكة (أ) لا تعرض أي كيان من الشبكة (ب)."""
    app = two_networks
    ca = _login(app, "neta", "neta-own", PW)
    body = ca.get(f"/neta{page}").get_data(as_text=True)
    # علامات الشبكة ب يجب ألّا تظهر
    for marker in ("sub-B1", "card-B1", "BATCH-B", "plan-B", "rtr-B", "dist-B", "netb-own"):
        assert marker not in body, f"{page}: تسريب «{marker}» من الشبكة ب"


# ─────────────── 3. منع الوصول عبر الشبكات ───────────────

def test_cross_network_access_denied(two_networks):
    app = two_networks
    ca = _login(app, "neta", "neta-own", PW)
    for path in ("/netb/admin/radius/", "/netb/admin/radius/subscribers",
                 "/netb/admin/radius/cards/batches", "/netb/admin/radius/my-backups"):
        assert ca.get(path).status_code == 403, f"{path}: لم يُمنع!"


# ─────────────── 4. منع IDOR (فتح كيان شبكة أخرى بمعرّفه) ───────────────

def _ids_of(app, tid):
    from app.radius.db.connection import db
    q = lambda s: db().execute(s, (tid,)).fetchone()["id"]  # noqa: E731
    return {
        "plan": q("SELECT id FROM access_plans WHERE tenant_id=?"),
        "batch": q("SELECT id FROM card_batches WHERE tenant_id=?"),
        "nas": q("SELECT id FROM nas_devices WHERE tenant_id=?"),
        "dist": q("SELECT id FROM distributors WHERE tenant_id=?"),
        "admin": q("SELECT a.id FROM admins a JOIN tenant_memberships m "
                   "ON m.admin_id=a.id WHERE m.tenant_id=?"),
    }


def _edit_paths(slug, ids):
    return [
        f"/{slug}/admin/radius/plans/{ids['plan']}/edit",
        f"/{slug}/admin/radius/cards/batches/{ids['batch']}/edit",
        f"/{slug}/admin/radius/devices/{ids['nas']}/edit",
        f"/{slug}/admin/radius/distributors/{ids['dist']}/edit",
        f"/{slug}/admin/radius/admins/{ids['admin']}/edit",
    ]


def test_own_entities_open_fine_positive_control(two_networks):
    """ضبطٌ موجب: نفس المسارات تفتح بمعرّفات الشبكة نفسها — كي لا يمرّ
    اختبار IDOR بسبب مسارٍ خاطئ (404) بدل حارسٍ حقيقيّ."""
    app = two_networks
    with app.app_context():
        ids = _ids_of(app, app.ctx["tid"]["neta"])
    ca = _login(app, "neta", "neta-own", PW)
    for p in _edit_paths("neta", ids):
        assert ca.get(p).status_code == 200, f"{p}: لا يفتح لصاحبه (المسار خاطئ؟)"


def test_idor_read_of_other_network_entities_blocked(two_networks):
    """مالك الشبكة (أ) يحاول فتح كيانات الشبكة (ب) بمعرّفاتها المباشرة."""
    app = two_networks
    with app.app_context():
        b_ids = _ids_of(app, app.ctx["tid"]["netb"])
    ca = _login(app, "neta", "neta-own", PW)
    for p in _edit_paths("neta", b_ids):   # مسار شبكتي + معرّف الشبكة الأخرى
        r = ca.get(p)
        assert r.status_code != 200, f"{p}: فُتح كيان شبكة أخرى!"
        if r.status_code == 200:
            pytest.fail(p)
        # وإن كان تحويلًا، فليس إلى صفحة تعرض بيانات ب
        body = ca.get(p, follow_redirects=True).get_data(as_text=True)
        for marker in ("BATCH-B", "plan-B", "rtr-B", "dist-B", "netb-own"):
            assert marker not in body, f"{p}: سرّب «{marker}»"


def test_idor_write_to_other_network_entities_blocked(two_networks):
    """محاولات كتابة/حذف عبر الشبكات — يجب ألّا تُغيّر شيئًا في الشبكة (ب)."""
    app = two_networks
    b = app.ctx["tid"]["netb"]
    with app.app_context():
        ids = _ids_of(app, b)
    ca = _login(app, "neta", "neta-own", PW)
    writes = [
        (f"/neta/admin/radius/plans/{ids['plan']}", {"name": "HACKED"}),
        (f"/neta/admin/radius/plans/{ids['plan']}/delete", {}),
        (f"/neta/admin/radius/devices/{ids['nas']}", {"name": "HACKED"}),
        (f"/neta/admin/radius/devices/{ids['nas']}/delete", {}),
        (f"/neta/admin/radius/admins/{ids['admin']}/delete", {}),
        (f"/neta/admin/radius/distributors/{ids['dist']}/edit", {"name": "HACKED"}),
    ]
    for path, data in writes:
        ca.post(path, data=data)          # النتيجة غير مهمّة — الأثر هو المهمّ
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT name FROM access_plans WHERE id=?", (ids["plan"],)).fetchone()
        assert row and row["name"] == "plan-B", "باقة الشبكة ب تغيّرت/حُذفت!"
        row = db().execute("SELECT name FROM nas_devices WHERE id=?", (ids["nas"],)).fetchone()
        assert row and row["name"] == "rtr-B", "راوتر الشبكة ب تغيّر/حُذف!"
        row = db().execute("SELECT name FROM distributors WHERE id=?", (ids["dist"],)).fetchone()
        assert row and row["name"] == "dist-B", "موزّع الشبكة ب تغيّر!"
        assert db().execute("SELECT 1 FROM admins WHERE id=?",
                            (ids["admin"],)).fetchone(), "مدير الشبكة ب حُذف!"


# ─────────────── 4b. الإنشاء الحيّ عبر HTTP لكلا الشبكتين ───────────────

def _post(c, slug, path, data):
    """POST مع رمز CSRF الحيّ للجلسة (الحماية خادميّة لا Flask-WTF)."""
    with c.session_transaction() as s:
        tok = s.get("_csrf_token", "")
    payload = dict(data, _csrf_token=tok)
    return c.post(f"/{slug}/admin/radius{path}", data=payload, follow_redirects=True)


def test_live_creation_lands_in_the_right_network(two_networks):
    """كل مالك يُنشئ (باقة/مشترك/راوتر/موزّع/مدير) عبر الواجهة فعليًّا،
    ثم نتحقّق أن كل صفٍّ حطّ في شبكته وحدها."""
    app = two_networks
    tid = app.ctx["tid"]
    with app.app_context():
        from app.radius.db.connection import db
        op_role = db().execute("SELECT id FROM roles WHERE name='operator'").fetchone()["id"]
    for slug, tag in (("neta", "A"), ("netb", "B")):
        c = _login(app, slug, f"{slug}-own", PW)
        _post(c, slug, "/plans", {
            "name": f"live-plan-{tag}", "plan_type": "time", "service_type": "Hotspot",
            "speed_down_kbps": "10000", "speed_up_kbps": "5000",
            "concurrent_sessions": "1"})
        _post(c, slug, "/users", {
            "username": f"live-sub-{tag}", "password": "livepass123",
            "full_name": f"live {tag}", "user_type": "subscriber", "status": "enabled"})
        _post(c, slug, "/devices", {
            "name": f"live-rtr-{tag}", "address": f"10.{80 + (0 if tag == 'A' else 1)}.0.9",
            "secret": "livesecret", "vendor": "mikrotik"})
        _post(c, slug, "/distributors", {
            "name": f"live-dist-{tag}", "display_name": f"موزع حيّ {tag}",
            "phone": "0000", "status": "active"})
        _post(c, slug, "/admins", {
            "username": f"live-mgr-{tag}", "password": "livepass123",
            "full_name": f"مدير حيّ {tag}", "role_id": str(op_role), "enabled": "1"})
        # توليد كروت من باقة الشبكة نفسها (حزمة + كروت في عمليّة واحدة)
        with app.app_context():
            from app.radius.db.connection import db
            pid = db().execute("SELECT id FROM access_plans WHERE tenant_id=? AND name=?",
                               (tid[slug], f"live-plan-{tag}")).fetchone()
        assert pid, f"باقة {tag} الحيّة لم تُنشأ — تعذّر توليد الكروت"
        _post(c, slug, "/cards/generate", {
            "package_name": f"live-batch-{tag}", "plan_id": str(pid["id"]),
            "count": "3", "username_length": "6", "password_length": "6",
            "price": "1000", "currency": "YER"})

    with app.app_context():
        from app.radius.db.connection import db
        a, b = tid["neta"], tid["netb"]
        checks = [("access_plans", "name", "live-plan-"), ("subscribers", "username", "live-sub-"),
                  ("nas_devices", "name", "live-rtr-"), ("distributors", "name", "live-dist-"),
                  ("card_batches", "package_name", "live-batch-")]
        for table, col, prefix in checks:
            for owner_tid, tag, other in ((a, "A", b), (b, "B", a)):
                row = db().execute(
                    f"SELECT tenant_id FROM {table} WHERE {col}=?",
                    (f"{prefix}{tag}",)).fetchone()
                assert row, f"{table}: لم يُنشأ {prefix}{tag} عبر الواجهة"
                assert row["tenant_id"] == owner_tid, \
                    f"{table}/{prefix}{tag}: حطّ في الجهة الخطأ ({row['tenant_id']})"
        # المدير الحيّ: عضويته في شبكة مُنشِئه فقط
        for owner_tid, tag in ((a, "A"), (b, "B")):
            rows = db().execute(
                "SELECT m.tenant_id FROM admins a JOIN tenant_memberships m ON m.admin_id=a.id "
                "WHERE a.username=?", (f"live-mgr-{tag}",)).fetchall()
            assert rows, f"لم يُنشأ المدير live-mgr-{tag}"
            assert {r["tenant_id"] for r in rows} == {owner_tid}, \
                f"live-mgr-{tag}: عضوية في شبكة أخرى!"


def test_created_manager_sees_only_its_network(two_networks):
    """المدير الذي أنشأه مالك الشبكة (أ) يدخل ولا يرى شيئًا من (ب)،
    ولا يستطيع فتح مسار الشبكة (ب)."""
    app = two_networks
    with app.app_context():
        from app.radius.db.connection import db
        op_role = db().execute("SELECT id FROM roles WHERE name='operator'").fetchone()["id"]
    c = _login(app, "neta", "neta-own", PW)
    _post(c, "neta", "/admins", {
        "username": "mgr-A2", "password": "livepass123", "full_name": "مدير أ٢",
        "role_id": str(op_role), "enabled": "1"})
    mc = _login(app, "neta", "mgr-A2", "livepass123")
    r = mc.get("/neta/admin/radius/")
    assert r.status_code == 200, f"المدير لم يدخل لوحته (http={r.status_code})"
    home = r.get_data(as_text=True)
    for marker in ("sub-B1", "BATCH-B", "netb-own", "plan-B"):
        assert marker not in home, f"تسريب «{marker}» لمدير الشبكة أ"
    assert mc.get("/netb/admin/radius/").status_code == 403, "المدير فتح شبكة أخرى!"
    assert mc.get("/netb/admin/radius/subscribers").status_code == 403


# ─────────────── 5. عزل النسخ الاحتياطية ───────────────

def test_backup_contains_only_own_network(two_networks):
    app = two_networks
    a, b = app.ctx["tid"]["neta"], app.ctx["tid"]["netb"]
    with app.app_context():
        import gzip
        import json
        from app.radius.services import tenant_backup
        info = tenant_backup.export_tenant(a, actor="t")
        assert info["rows"] > 0
        name = tenant_backup.list_tenant_backups(a)[0]["name"]
        payload = json.loads(gzip.decompress(tenant_backup.read_backup_bytes(a, name)))
        # كل صفّ في النسخة يخصّ الشبكة أ
        assert all(r["tenant_id"] == a
                   for tbl in payload["tables"].values() for r in tbl)
        # ولا يحوي علامات الشبكة ب
        blob = json.dumps(payload, ensure_ascii=False)
        for marker in ("sub-B1", "card-B1", "BATCH-B", "plan-B", "rtr-B"):
            assert marker not in blob, f"النسخة تحوي «{marker}» من الشبكة ب"


def test_restore_does_not_touch_other_network(two_networks):
    app = two_networks
    a, b = app.ctx["tid"]["neta"], app.ctx["tid"]["netb"]
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.services import tenant_backup
        tenant_backup.export_tenant(a, actor="t")
        name = tenant_backup.list_tenant_backups(a)[0]["name"]
        # اعبث بالشبكتين ثم استعِد (أ) فقط
        db().execute("DELETE FROM subscribers WHERE tenant_id=? AND username='sub-A1'", (a,))
        db().execute("INSERT INTO subscribers(tenant_id,username,password,user_type,status,created_at) "
                     "VALUES(?,'sub-B-new','x','subscriber','enabled',?)", (b, NOW))
        db().commit()
        tenant_backup.restore_tenant(a, name, actor="t")
        a_subs = {r["username"] for r in db().execute(
            "SELECT username FROM subscribers WHERE tenant_id=?", (a,)).fetchall()}
        b_subs = {r["username"] for r in db().execute(
            "SELECT username FROM subscribers WHERE tenant_id=?", (b,)).fetchall()}
        assert "sub-A1" in a_subs                 # الشبكة أ عادت
        assert "sub-B-new" in b_subs              # الشبكة ب لم تتأثّر
        assert not any(u.endswith("-B1") for u in a_subs)  # ولا تسرّبت لها


# ─────────────── 6. عزل مصادقة RADIUS ───────────────

def test_radius_auth_same_username_resolves_per_network(two_networks):
    """اسم مستخدم واحد في الشبكتين بكلمتين مختلفتين → كلٌّ يصادق على شبكته."""
    app = two_networks
    with app.app_context():
        from app.radius.services.policy_engine import AuthRequest, authorize
        a, b = app.ctx["tid"]["neta"], app.ctx["tid"]["netb"]
        # كلمة الشبكة أ تنجح على أ وتفشل على ب
        assert authorize(AuthRequest(username="shared-user", password="pw-A", tenant_id=a)).ok
        assert not authorize(AuthRequest(username="shared-user", password="pw-A", tenant_id=b)).ok
        # والعكس
        assert authorize(AuthRequest(username="shared-user", password="pw-B", tenant_id=b)).ok
        assert not authorize(AuthRequest(username="shared-user", password="pw-B", tenant_id=a)).ok


def test_nas_ip_resolves_to_its_own_network(two_networks):
    """عنوان راوتر كل شبكة يحلّ لجهتها (أساس عزل المصادقة على السلك)."""
    app = two_networks
    with app.app_context():
        from app.api.v1.internal_auth import _resolve_tenant_id
        a, b = app.ctx["tid"]["neta"], app.ctx["tid"]["netb"]
        assert _resolve_tenant_id({"Packet-Src-IP": "10.90.0.1"}) == a
        assert _resolve_tenant_id({"Packet-Src-IP": "10.91.0.1"}) == b
        # راوتر مجهول مع تعدّد الشبكات → رفض (fail-closed)
        assert _resolve_tenant_id({"Packet-Src-IP": "203.0.113.7"}) is None
