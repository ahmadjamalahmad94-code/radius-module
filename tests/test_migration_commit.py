"""اختبارات تنفيذ الاستيراد (تكامل DB) — محرّك الترحيل.

تغطّي: dry_run لا يكتب، التنفيذ يُنشئ السجلّات، إعادة التشغيل idempotent
(دمج لا تكرار)، حلّ العلاقات (مشترك→باقة، مدير→دور)، علَم كلمة المرور
المُجزّأة، ووضع التخطّي.

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "migration.db")
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


TID = 1


def _sqlite_bytes(script: str) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.executescript(script)
        c.commit()
        c.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


_SUBS_AND_PLANS = """
    CREATE TABLE plans (id INTEGER, name TEXT, price REAL);
    INSERT INTO plans VALUES (1,'Gold',10),(2,'Silver',5);
    CREATE TABLE subscribers (id INTEGER, username TEXT, password TEXT,
                              plan TEXT, phone TEXT);
    INSERT INTO subscribers VALUES
      (1,'ali','pw1','Gold','0599'),
      (2,'sara','pw2','Silver','0598'),
      (3,'omar','pw3','Gold','0597');
"""


# ── dry_run ──────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_writes_nothing(self, app_ctx):
        from app.radius.services.migration import engine
        from app.radius.db.repos import subscribers_repo, plans_repo
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "src.db")
        report = engine.commit(TID, res.dataset, res.matches, dry_run=True)
        assert report.dry_run is True
        assert report.created > 0                       # التقرير يَعِد بإنشاء
        # لكن لا شيء في DB فعلًا.
        assert subscribers_repo.count_subscribers(TID) == 0
        assert len(plans_repo.list_plans(TID, limit=100)) == 0


# ── إنشاء + حلّ العلاقات ──────────────────────────────────────────────

class TestCommitCreate:
    def test_creates_plans_then_subscribers_linked(self, app_ctx):
        from app.radius.services.migration import engine
        from app.radius.db.repos import subscribers_repo, plans_repo
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "src.db")
        report = engine.commit(TID, res.dataset, res.matches, dry_run=False)
        assert report.status == "completed"

        plans = {p.name: p for p in plans_repo.list_plans(TID, limit=100)}
        assert "Gold" in plans and "Silver" in plans

        ali = subscribers_repo.get_subscriber(TID, "ali")
        assert ali is not None
        assert ali.password == "pw1"
        # العلاقة محلولة: plan_id يشير لباقة Gold المُنشأة.
        assert ali.plan_id == plans["Gold"].id

        sara = subscribers_repo.get_subscriber(TID, "sara")
        assert sara.plan_id == plans["Silver"].id

    def test_section_report_counts(self, app_ctx):
        from app.radius.services.migration import engine
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "src.db")
        report = engine.commit(TID, res.dataset, res.matches, dry_run=False)
        subs = report.section("subscribers")
        assert subs.created == 3
        plans = report.section("plans")
        assert plans.created == 2


# ── idempotency ──────────────────────────────────────────────────────

class TestIdempotency:
    def test_rerun_merges_not_duplicates(self, app_ctx):
        from app.radius.services.migration import engine
        from app.radius.db.repos import subscribers_repo, plans_repo
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "src.db")

        engine.commit(TID, res.dataset, res.matches, dry_run=False)
        n_subs = subscribers_repo.count_subscribers(TID)
        n_plans = len(plans_repo.list_plans(TID, limit=100))

        report2 = engine.commit(TID, res.dataset, res.matches, dry_run=False)
        # لا سجلّات جديدة — كلها دُمِجت.
        assert subscribers_repo.count_subscribers(TID) == n_subs
        assert len(plans_repo.list_plans(TID, limit=100)) == n_plans
        assert report2.section("subscribers").merged == 3
        assert report2.section("subscribers").created == 0


# ── وضع التخطّي ───────────────────────────────────────────────────────

class TestSkipMode:
    def test_skip_does_not_update_existing(self, app_ctx):
        from app.radius.services.migration import engine
        from app.radius.db.repos import subscribers_repo
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "src.db")
        engine.commit(TID, res.dataset, res.matches, dry_run=False)

        # عدّل كلمة ali محليًّا، ثمّ أعد الاستيراد بوضع التخطّي.
        from dataclasses import replace
        ali = subscribers_repo.get_subscriber(TID, "ali")
        subscribers_repo.upsert_subscriber(replace(ali, password="LOCAL"))

        sel = [{"section": "subscribers", "source_table": "subscribers",
                "mode": "skip", "enabled": True},
               {"section": "plans", "source_table": "plans",
                "mode": "skip", "enabled": True}]
        engine.commit(TID, res.dataset, res.matches, selections=sel, dry_run=False)
        assert subscribers_repo.get_subscriber(TID, "ali").password == "LOCAL"


# ── المدراء + الأدوار ─────────────────────────────────────────────────

class TestManagersRoles:
    def test_manager_linked_to_role(self, app_ctx):
        from app.radius.services.migration import engine
        from app.radius.db.repos import admins_repo
        data = _sqlite_bytes("""
            CREATE TABLE roles (id INTEGER, name TEXT, permissions TEXT);
            INSERT INTO roles VALUES (1,'agent','cards.view,cards.create');
            CREATE TABLE admins (id INTEGER, username TEXT, password TEXT,
                                 email TEXT, role TEXT);
            INSERT INTO admins VALUES (1,'op1','x','o@x.com','agent');
        """)
        res = engine.analyze(data, "ar.db")
        engine.commit(TID, res.dataset, res.matches, dry_run=False)

        role = admins_repo.get_role_by_name("agent")
        assert role is not None
        op1 = admins_repo.get_by_username("op1")
        assert op1 is not None
        assert op1.role_id == role.id


class TestManagerNumericGuard:
    def test_numeric_manager_not_minted(self, app_ctx):
        # created_by رقميّ (معرّف لم يُحَلّ) → لا يُفبرَك مدير اسمه رقم؛ النصّ
        # الحقيقيّ يُنشئ مديرًا ويُربَط.
        from app.radius.services.migration import engine
        from app.radius.db.repos import admins_repo, subscribers_repo
        data = _sqlite_bytes("""
            CREATE TABLE subscribers (id INTEGER, username TEXT, password TEXT, created_by TEXT);
            INSERT INTO subscribers VALUES (1,'u1','p1','7'),(2,'u2','p2','Shareef');
        """)
        res = engine.analyze(data, "s.db")
        engine.commit(TID, res.dataset, res.matches, dry_run=False)
        names = [a.username for a in admins_repo.list_admins()]
        assert "7" not in names                  # لا مدير اسمه رقم
        assert "Shareef" in names                 # الاسم الحقيقيّ أُنشئ
        assert subscribers_repo.get_subscriber(TID, "u1").manager_id is None


class TestBatchFkSafe:
    def test_batch_without_plan_skips_not_fails(self, app_ctx):
        # حزمة بباقة غير معروفة → تُتخطّى بسبب واضح (لا FOREIGN KEY crash).
        from app.radius.services.migration import engine
        data = _sqlite_bytes("""
            CREATE TABLE batches (id INTEGER, name TEXT, plan TEXT, count INTEGER);
            INSERT INTO batches VALUES (1,'B1','NoSuchPlan',10);
        """)
        res = engine.analyze(data, "b.db")
        report = engine.commit(TID, res.dataset, res.matches, dry_run=False)
        b = report.section("batches")
        assert b.failed == 0                       # لا فشل FK
        assert b.skipped >= 1
        assert any("الباقة غير معروفة" in e.get("reason", "") for e in b.errors)


class TestProgressCallback:
    def test_progress_cb_called(self, app_ctx):
        from app.radius.services.migration import engine
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "s.db")
        calls = []
        engine.commit(TID, res.dataset, res.matches, dry_run=False,
                      progress_cb=lambda d, t, s, p: calls.append((d, t, s, p)))
        assert calls, "progress_cb لم يُستدعَ"
        # آخر نداء يبلغ الإجمالي.
        assert calls[-1][0] == calls[-1][1] and calls[-1][1] > 0


class TestDistributors:
    def test_distributor_created(self, app_ctx):
        from app.radius.services.migration import engine
        from app.radius.db.connection import db
        data = _sqlite_bytes("""
            CREATE TABLE distributors (id INTEGER, name TEXT, email TEXT,
                                       phone TEXT, balance REAL);
            INSERT INTO distributors VALUES (1,'DistA','a@x.com','059',100.0);
        """)
        res = engine.analyze(data, "d.db")
        report = engine.commit(TID, res.dataset, res.matches, dry_run=False)
        assert report.section("distributors").created == 1
        row = db().execute(
            "SELECT * FROM distributors WHERE tenant_id=? AND name=?",
            (TID, "DistA")).fetchone()
        assert row is not None
        assert abs(row["balance"] - 100.0) < 0.001


# ── كلمة المرور المُجزّأة (FreeRADIUS) ─────────────────────────────────

class TestBuildPlan:
    def test_counts_new_and_invalid_and_dup(self, app_ctx):
        from app.radius.services.migration import engine
        data = _sqlite_bytes("""
            CREATE TABLE subscribers (id INTEGER, username TEXT, password TEXT, plan TEXT);
            INSERT INTO subscribers VALUES
              (1,'ali','p1','Gold'),
              (2,'ali','p1','Gold'),     -- مكرّر داخل الملف
              (3,'','p3','Gold');        -- بلا اسم → غير صالح
        """)
        res = engine.analyze(data, "s.db")
        plan = engine.build_plan(TID, res.dataset, res.matches)
        sp = plan.section("subscribers")
        counts = sp.counts()
        assert counts["new"] == 1
        assert counts["skip"] == 1          # المكرّر
        assert counts["invalid"] == 1       # بلا اسم

    def test_merge_when_exists(self, app_ctx):
        from app.radius.services.migration import engine
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "s.db")
        engine.commit(TID, res.dataset, res.matches, dry_run=False)
        # خطّة ثانية على نفس المصدر → كلها «دمج».
        plan = engine.build_plan(TID, res.dataset, res.matches)
        sp = plan.section("subscribers")
        assert sp.counts()["merge"] == 3
        assert sp.counts()["new"] == 0

    def test_preview_hides_password(self, app_ctx):
        from app.radius.services.migration import engine
        data = _sqlite_bytes(_SUBS_AND_PLANS)
        res = engine.analyze(data, "s.db")
        plan = engine.build_plan(TID, res.dataset, res.matches)
        sp = plan.section("subscribers")
        # المعاينة لا تُسرّب كلمة المرور الخام.
        for row in sp.rows:
            assert row.preview.get("password") in (None, "•••")


class TestHashedPassword:
    def test_hashed_password_not_stored_as_cleartext(self, app_ctx):
        from app.radius.services.migration import engine
        from app.radius.db.repos import subscribers_repo
        data = _sqlite_bytes("""
            CREATE TABLE radcheck (id INTEGER PRIMARY KEY, username TEXT,
                                   attribute TEXT, op TEXT, value TEXT);
            INSERT INTO radcheck (username,attribute,op,value) VALUES
              ('ali','Cleartext-Password',':=','plain1'),
              ('bob','Crypt-Password',':=','$1$xx$hash');
        """)
        res = engine.analyze(data, "fr.db")
        report = engine.commit(TID, res.dataset, res.matches, dry_run=False)

        ali = subscribers_repo.get_subscriber(TID, "ali")
        assert ali.password == "plain1"             # نصّيّة → تعمل PAP

        bob = subscribers_repo.get_subscriber(TID, "bob")
        assert bob.password == ""                   # لم تُخزَّن كنصّ صريح
        meta = json.loads(bob.metadata or "{}")
        assert meta.get("migration", {}).get("password_scheme") == "crypt"
        assert report.warnings                       # تحذير واضح للمالك
