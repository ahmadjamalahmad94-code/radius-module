"""اختبارات أداة «تصفير / تنظيف البيانات» (للمالك فقط) — e2e عبر عميل Flask.

تغطّي:
  • حظر غير المالك (403) على الصفحة + الملخّص + التنفيذ.
  • النسخة الاحتياطيّة تُنشأ **قبل** الحذف، وفشلها يُلغي التصفير.
  • كل فئة تحذف صفوفها + صفوف الراديوس التابعة دون ترك حسابات نفق الإدارة.
  • بقاء حساب المالك بعد تصفير المدراء.
  • وجوب كلمة التأكيد.
  • الذرّيّة: خطأ أثناء التنفيذ يُعيد كل شيء (ROLLBACK).
  • اللوحة تعمل بعد التصفير الكامل.

شغّل هذا الملف وحده.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "reset.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        yield app
    reset_for_tests(None)


@pytest.fixture
def client(app):
    return app.test_client()


# ── أدوات ─────────────────────────────────────────────────────────────

def _make_admin(is_super=True):
    from app.radius.db.repos import admins_repo
    u = f"u_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw123456",
                             full_name="T", is_super_admin=is_super)
    return u


def _login(client, username):
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": "pw123456"},
                      follow_redirects=False)
    assert res.status_code in (302, 303), res.status_code


def _csrf(client):
    client.get("/admin/radius/data-reset")
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def _csrf_open(client):
    """توكن CSRF من صفحة يصلها أيّ مسؤول مُسجَّل (لاختبار حارس 403 على POST
    دون أن يعترضه حارس CSRF أوّلًا)."""
    client.get("/admin/radius/account")
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def _run(client, tok, keys, confirm="تصفير"):
    return client.post("/admin/radius/data-reset/run",
                       json={"keys": keys, "confirm": confirm},
                       headers={"X-CSRFToken": tok})


def _summary(client, tok, keys):
    return client.post("/admin/radius/data-reset/summary",
                       json={"keys": keys},
                       headers={"X-CSRFToken": tok})


# ── بذر بيانات مباشرة (SQL) ───────────────────────────────────────────

def _seed_plan(name="Gold"):
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id, name, created_at) VALUES(1,?,?)",
        (name, now_iso()))
    return cur.lastrowid


def _seed_subscriber(username, *, radcheck=True):
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    db().execute(
        "INSERT INTO subscribers(tenant_id, username, created_at) VALUES(1,?,?)",
        (username, now_iso()))
    if radcheck:
        db().execute(
            "INSERT INTO radcheck(tenant_id, username, attribute, value) "
            "VALUES(1,?, 'Cleartext-Password', 'x')", (username,))
        db().execute(
            "INSERT INTO radreply(tenant_id, username, attribute, value) "
            "VALUES(1,?, 'Framed-IP', '10.0.0.1')", (username,))


def _seed_card(username, plan_id, *, batch_id=None):
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    if batch_id is None:
        cur = db().execute(
            "INSERT INTO card_batches(tenant_id, batch_code, plan_id, created_at) "
            "VALUES(1,?,?,?)", (f"B_{uuid4().hex[:6]}", plan_id, now_iso()))
        batch_id = cur.lastrowid
    db().execute(
        "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, created_at) "
        "VALUES(1,?,?, 'p', ?, ?)", (batch_id, username, plan_id, now_iso()))
    db().execute(
        "INSERT INTO radcheck(tenant_id, username, attribute, value) "
        "VALUES(1,?, 'Cleartext-Password', 'x')", (username,))
    return batch_id


def _seed_mgmt_radcheck(username="rtr-9"):
    """صف radcheck لنفق إدارة راوتر — يجب أن يبقى بعد تصفير المشتركين/الكروت."""
    from app.radius.db.connection import db
    db().execute(
        "INSERT INTO radcheck(tenant_id, username, attribute, value) "
        "VALUES(1,?, 'Cleartext-Password', 'secret')", (username,))


def _count(table, where="", params=()):
    from app.radius.db.connection import db
    sql = f"SELECT COUNT(*) AS c FROM {table}"
    if where:
        sql += " WHERE " + where
    return int(db().execute(sql, params).fetchone()["c"])


# ═══════════════════════ حماية المالك (403) ═══════════════════════

class TestOwnerOnly:
    def test_page_owner_ok(self, client):
        u = _make_admin()
        _login(client, u)
        res = client.get("/admin/radius/data-reset")
        assert res.status_code == 200
        assert "تصفير".encode() in res.data

    def test_page_non_owner_forbidden(self, client):
        owner = _make_admin()      # أصغر معرّف = المالك
        other = _make_admin()      # مدير لاحق
        _login(client, other)
        assert client.get("/admin/radius/data-reset").status_code == 403

    def test_summary_non_owner_forbidden(self, client):
        _make_admin()
        other = _make_admin()
        _login(client, other)
        tok = _csrf_open(client)
        res = client.post("/admin/radius/data-reset/summary",
                          json={"keys": ["subscribers"]},
                          headers={"X-CSRFToken": tok})
        assert res.status_code == 403

    def test_run_non_owner_forbidden(self, client, app):
        _make_admin()
        other = _make_admin()
        with app.app_context():
            _seed_subscriber("ali")
        _login(client, other)
        tok = _csrf_open(client)
        res = client.post("/admin/radius/data-reset/run",
                          json={"keys": ["subscribers"], "confirm": "تصفير"},
                          headers={"X-CSRFToken": tok})
        assert res.status_code == 403
        with app.app_context():
            assert _count("subscribers") == 1  # لم يُحذف شيء


# ═══════════════════════ التأكيد ═══════════════════════

class TestConfirmation:
    def test_wrong_confirm_word_no_wipe(self, client, app):
        u = _make_admin()
        with app.app_context():
            _seed_subscriber("ali")
        _login(client, u)
        tok = _csrf(client)
        res = _run(client, tok, ["subscribers"], confirm="نعم")
        j = res.get_json()
        assert j["ok"] is False and j["code"] == "confirm"
        with app.app_context():
            assert _count("subscribers") == 1

    def test_no_keys_rejected(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        j = _run(client, tok, [], confirm="تصفير").get_json()
        assert j["ok"] is False and j["code"] == "no_keys"


# ═══════════════════════ النسخة الاحتياطيّة أوّلًا ═══════════════════════

class TestBackupFirst:
    def test_backup_created_before_delete(self, client, app):
        u = _make_admin()
        with app.app_context():
            _seed_subscriber("ali")
        _login(client, u)
        tok = _csrf(client)
        j = _run(client, tok, ["subscribers"]).get_json()
        assert j["ok"] is True, j
        # اسم نسخة موجود ومُعاد.
        assert j["backup"] and (j["backup"].endswith(".sqlite3.gz")
                                or j["backup"].endswith(".sqlite3"))
        # الملف موجود فعليًّا على القرص.
        from app.radius.db.connection import db_path
        from pathlib import Path
        bdir = Path(db_path()).parent / "backups"
        assert (bdir / j["backup"]).exists()
        with app.app_context():
            assert _count("subscribers") == 0

    def test_backup_failure_aborts_wipe(self, client, app, monkeypatch):
        u = _make_admin()
        with app.app_context():
            _seed_subscriber("ali")
        _login(client, u)
        tok = _csrf(client)
        # اجعل النسخة تفشل → يجب أن يُلغى التصفير ولا يُحذف شيء.
        from app.radius.services import operations
        monkeypatch.setattr(
            operations.OperationsService, "run_local_backup",
            lambda self, **kw: {"verified": False, "run": {"message": "boom"}})
        j = _run(client, tok, ["subscribers"]).get_json()
        assert j["ok"] is False and j["code"] == "backup_failed"
        with app.app_context():
            assert _count("subscribers") == 1  # سليم


# ═══════════════════════ حذف الفئات + الراديوس ═══════════════════════

class TestWipeCategories:
    def test_subscribers_clears_radius_but_keeps_mgmt(self, client, app):
        u = _make_admin()
        with app.app_context():
            _seed_subscriber("ali")
            _seed_subscriber("sara")
            _seed_mgmt_radcheck("rtr-9")     # يجب أن يبقى
            assert _count("subscribers") == 2
            assert _count("radcheck") == 3   # ali + sara + rtr-9
        _login(client, u)
        tok = _csrf(client)
        j = _run(client, tok, ["subscribers"]).get_json()
        assert j["ok"] is True, j
        with app.app_context():
            assert _count("subscribers") == 0
            assert _count("radreply") == 0
            # صفوف المشتركين في radcheck ذهبت، لكن حساب نفق الإدارة بقي.
            assert _count("radcheck") == 1
            assert _count("radcheck", "username = ?", ("rtr-9",)) == 1
        # التقرير يذكر عدد المشتركين المحذوف.
        rep = {r["key"]: r for r in j["report"]}
        assert rep["subscribers"]["primary"] == 2

    def test_cards_wipe_keeps_plan(self, client, app):
        u = _make_admin()
        with app.app_context():
            pid = _seed_plan()
            _seed_card("c001", pid)
            _seed_card("c002", pid)
            assert _count("cards") == 2
            assert _count("card_batches") == 2
        _login(client, u)
        tok = _csrf(client)
        j = _run(client, tok, ["cards"]).get_json()
        assert j["ok"] is True, j
        with app.app_context():
            assert _count("cards") == 0
            assert _count("card_batches") == 0
            assert _count("radcheck") == 0     # صفوف الكروت ذهبت
            assert _count("access_plans") == 1  # الباقة سليمة (لم تُختَر)

    def test_plans_with_cards_integrity_block(self, client, app):
        """اختيار الباقات وحدها بينما توجد كروت تُشير إليها (RESTRICT) →
        إجهاض نظيف: لا يُحذف شيء (لا مرجع مكسور)."""
        u = _make_admin()
        with app.app_context():
            pid = _seed_plan()
            _seed_card("c001", pid)
        _login(client, u)
        tok = _csrf(client)
        j = _run(client, tok, ["plans"]).get_json()
        assert j["ok"] is False and j["code"] == "integrity"
        assert j["backup"]  # نسخة أُنشئت رغم فشل الحذف
        with app.app_context():
            assert _count("access_plans") == 1  # سليم
            assert _count("cards") == 1

    def test_select_all_clears_everything(self, client, app):
        u = _make_admin()
        with app.app_context():
            pid = _seed_plan()
            _seed_subscriber("ali")
            _seed_card("c001", pid)
            _seed_mgmt_radcheck("rtr-1")
        _login(client, u)
        tok = _csrf(client)
        keys = list(get_all_keys())
        j = _run(client, tok, keys).get_json()
        assert j["ok"] is True, j
        with app.app_context():
            assert _count("subscribers") == 0
            assert _count("cards") == 0
            assert _count("card_batches") == 0
            assert _count("access_plans") == 0
            # حساب نفق الإدارة يبقى (ليس مشتركًا ولا كرتًا).
            assert _count("radcheck", "username = ?", ("rtr-1",)) == 1


def get_all_keys():
    from app.radius.services.data_reset import get_data_reset_service
    return [c.key for c in get_data_reset_service().categories()]


# ═══════════════════════ بقاء المالك ═══════════════════════

class TestOwnerSurvives:
    def test_owner_and_current_survive_managers_wipe(self, client, app):
        owner = _make_admin()                 # أصغر معرّف = المالك
        m1 = _make_admin(is_super=False)
        m2 = _make_admin(is_super=False)
        _login(client, owner)
        tok = _csrf(client)
        with app.app_context():
            assert _count("admins") == 3
        j = _run(client, tok, ["managers"]).get_json()
        assert j["ok"] is True, j
        with app.app_context():
            from app.radius.db.repos import admins_repo
            names = {a.username for a in admins_repo.list_admins()}
            assert owner in names           # المالك باقٍ
            assert m1 not in names and m2 not in names  # المدراء حُذفوا
            assert _count("admins") == 1

    def test_current_owner_not_deleted_even_if_designated(self, client, app):
        # المالك المُسجَّل حاليًّا لا يُحذف أبدًا (preserve set يشمل الجلسة).
        owner = _make_admin()
        _login(client, owner)
        tok = _csrf(client)
        j = _run(client, tok, ["managers"]).get_json()
        assert j["ok"] is True
        with app.app_context():
            assert _count("admins") == 1


# ═══════════════════════ الذرّيّة (ROLLBACK) ═══════════════════════

class TestTransactional:
    def test_rollback_on_midway_error(self, client, app, monkeypatch):
        u = _make_admin()
        with app.app_context():
            pid = _seed_plan()
            _seed_subscriber("ali")
            _seed_card("c001", pid)
        _login(client, u)
        tok = _csrf(client)
        # اجعل تصفير المشتركين (يعمل بعد الكروت في الترتيب العالميّ) يرمي خطأ.
        from app.radius.services.data_reset import DataResetService

        def boom(self, conn, ctx):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(DataResetService, "_wipe_subscribers", boom)
        j = _run(client, tok, ["cards", "subscribers"]).get_json()
        assert j["ok"] is False and j["code"] == "error"
        with app.app_context():
            # الكروت حُذفت منطقيًّا قبل الخطأ، لكن ROLLBACK أعادها.
            assert _count("cards") == 1
            assert _count("card_batches") == 1
            assert _count("subscribers") == 1


# ═══════════════════════ اللوحة تعمل بعد التصفير ═══════════════════════

class TestAppAfterWipe:
    def test_dashboard_renders_after_full_wipe(self, client, app):
        u = _make_admin()
        with app.app_context():
            pid = _seed_plan()
            _seed_subscriber("ali")
            _seed_card("c001", pid)
        _login(client, u)
        tok = _csrf(client)
        j = _run(client, tok, list(get_all_keys())).get_json()
        assert j["ok"] is True, j
        # صفحات رئيسيّة تُصيّر بلا انهيار على حالة فارغة.
        assert client.get("/admin/radius/").status_code in (200, 302)
        assert client.get("/admin/radius/data-reset").status_code == 200


# ═══════════════════════ الملخّص ═══════════════════════

class TestSummary:
    def test_summary_counts(self, client, app):
        u = _make_admin()
        with app.app_context():
            _seed_subscriber("ali")
            _seed_subscriber("sara")
        _login(client, u)
        tok = _csrf(client)
        j = _summary(client, tok, ["subscribers"]).get_json()
        assert j["ok"] is True
        sub = next(c for c in j["categories"] if c["key"] == "subscribers")
        assert sub["count"] == 2
        assert j["total"] == 2
