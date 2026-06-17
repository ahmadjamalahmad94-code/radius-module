"""feat/mikrotik-user-import — الزيادة 4: تنفيذ الاستيراد + السجلّ.

يغطّي: المحاكاة (dry_run) بلا كتابة، الإنشاء الحقيقي + مزامنة RADIUS،
أوضاع التكرار (skip/only_new/update/conflict)، إنشاء الخطط الناقصة،
الاستيراد بلا خطّة، تخطّي غير الصالح، كتابة السجلّ، وعدم تسريب كلمة المرور.
شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mt_import_run.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
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


_NAS = {"id": 7, "name": "MT-Main"}


def _mk_plan(name):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    return plans_repo.upsert_plan(AccessPlan(
        id=None, tenant_id=1, name=name, enabled=True))


def _mk_sub(username, plan_id=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, username=username, password="old", tenant_id=1, plan_id=plan_id))


def _preview(recs, itype="hotspot", transport="rest"):
    from app.radius.services import mt_import_service as S
    return S.build_preview(1, itype, recs, transport=transport)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


# ════════════════════════════════════════════════════════════════════════
# تطبيع وضع التكرار
# ════════════════════════════════════════════════════════════════════════
class TestDupModeNorm:

    def test_aliases(self):
        from app.radius.services import mt_import_runner as R
        assert R._norm_dup_mode("skip_existing") == R.DUP_SKIP
        assert R._norm_dup_mode("overwrite") == R.DUP_UPDATE
        assert R._norm_dup_mode("fail") == R.DUP_CONFLICT
        assert R._norm_dup_mode("garbage") == R.DUP_SKIP


# ════════════════════════════════════════════════════════════════════════
# المحاكاة (dry_run)
# ════════════════════════════════════════════════════════════════════════
class TestDryRun:

    def test_dry_run_no_writes(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        _mk_plan("1hour")
        prev = _preview([{"name": "newguy", "password": "p", "profile": "1hour"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev, dry_run=True)
        assert res.dry_run and res.imported == 1 and res.log_id is None
        assert _get("newguy") is None  # لم يُكتب شيء


# ════════════════════════════════════════════════════════════════════════
# الإنشاء الحقيقي + السجلّ
# ════════════════════════════════════════════════════════════════════════
class TestRealImport:

    def test_creates_subscribers_and_log(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        from app.radius.db.repos import mikrotik_import_logs_repo as logs
        p = _mk_plan("1hour")
        prev = _preview([
            {"name": "u1", "password": "p1", "profile": "1hour"},
            {"name": "u2", "password": "p2", "profile": "1hour"},
        ])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           actor="admin", actor_id=3)
        assert res.imported == 2 and res.failed == 0
        u1 = _get("u1")
        assert u1 is not None and u1.plan_id == p.id
        assert u1.service_type == "Hotspot"
        # السجلّ مكتوب وقابل للقراءة.
        log = logs.get(1, res.log_id)
        assert log["imported_count"] == 2 and log["nas_name"] == "MT-Main"
        assert log["transport"] == "rest"

    def test_disabled_user_imported_disabled(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        _mk_plan("1hour")
        prev = _preview([{"name": "off1", "password": "p", "profile": "1hour",
                          "disabled": "true"}])
        R.run_import(tenant_id=1, nas=_NAS, preview=prev)
        from app.radius.core.constants import STATUS_DISABLED
        assert _get("off1").status == STATUS_DISABLED

    def test_broadband_mac_and_ip(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        _mk_plan("home")
        prev = _preview([{"name": "ppp1", "password": "p", "profile": "home",
                          "caller-id": "AA:BB:CC:DD:EE:FF",
                          "remote-address": "10.0.0.9"}],
                        itype="broadband")
        R.run_import(tenant_id=1, nas=_NAS, preview=prev)
        s = _get("ppp1")
        assert s.service_type == "PPPoE" and s.static_ip == "10.0.0.9"
        assert s.mac_lock == "AA:BB:CC:DD:EE:FF"


# ════════════════════════════════════════════════════════════════════════
# أوضاع التكرار
# ════════════════════════════════════════════════════════════════════════
class TestDuplicateModes:

    def test_skip_leaves_existing(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        old_plan = _mk_plan("old")
        new_plan = _mk_plan("new")
        _mk_sub("dup", plan_id=old_plan.id)
        prev = _preview([{"name": "dup", "password": "x", "profile": "new"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           duplicate_mode="skip")
        assert res.skipped == 1 and res.updated == 0
        assert _get("dup").plan_id == old_plan.id  # لم يتغيّر

    def test_update_changes_existing(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        old_plan = _mk_plan("old")
        new_plan = _mk_plan("new")
        _mk_sub("dup", plan_id=old_plan.id)
        prev = _preview([{"name": "dup", "password": "newpw", "profile": "new"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           duplicate_mode="update")
        assert res.updated == 1 and res.imported == 0
        s = _get("dup")
        assert s.plan_id == new_plan.id and s.password == "newpw"

    def test_update_blank_password_preserved(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        _mk_plan("new")
        _mk_sub("dup")
        prev = _preview([{"name": "dup", "password": "", "profile": "new"}])
        R.run_import(tenant_id=1, nas=_NAS, preview=prev, duplicate_mode="update")
        assert _get("dup").password == "old"  # لم تُمسح

    def test_conflict_records_failure(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        _mk_plan("new")
        _mk_sub("dup")
        prev = _preview([{"name": "dup", "password": "x", "profile": "new"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           duplicate_mode="conflict")
        assert res.failed == 1 and res.imported == 0
        assert res.errors[0]["action"] == "conflict"


# ════════════════════════════════════════════════════════════════════════
# الخطط الناقصة + الاستيراد بلا خطّة
# ════════════════════════════════════════════════════════════════════════
class TestPlanHandling:

    def test_create_missing_plans(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        from app.radius.db.repos import plans_repo
        prev = _preview([{"name": "u1", "password": "p", "profile": "ghost-plan"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           create_missing_plans=True)
        assert res.imported == 1 and "ghost-plan" in res.created_plans
        s = _get("u1")
        assert s.plan_id is not None
        plan = plans_repo.get_plan(1, s.plan_id)
        assert plan.name == "ghost-plan"

    def test_unmapped_without_flag_imports_no_plan(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        prev = _preview([{"name": "u1", "password": "p", "profile": "ghost"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           create_missing_plans=False)
        assert res.imported == 1 and res.created_plans == []
        assert _get("u1").plan_id is None

    def test_create_missing_plans_dedup(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        from app.radius.db.repos import plans_repo
        prev = _preview([
            {"name": "a", "password": "p", "profile": "shared"},
            {"name": "b", "password": "p", "profile": "shared"},
        ])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           create_missing_plans=True)
        assert res.imported == 2
        named = [p for p in plans_repo.list_plans(1) if p.name == "shared"]
        assert len(named) == 1  # خطّة واحدة لا اثنتان


# ════════════════════════════════════════════════════════════════════════
# غير الصالح + الأمان
# ════════════════════════════════════════════════════════════════════════
class TestInvalidAndSecurity:

    def test_invalid_rows_skipped(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        _mk_plan("1hour")
        prev = _preview([
            {"name": "", "password": "p", "profile": "1hour"},
            {"name": "good", "password": "p", "profile": "1hour"},
        ])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev)
        assert res.skipped == 1 and res.imported == 1

    def test_password_not_in_log(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        from app.radius.db.repos import mikrotik_import_logs_repo as logs
        _mk_plan("1hour")
        # صفّ يتعارض كي يولّد خطأً في السجلّ.
        _mk_sub("dup")
        prev = _preview([{"name": "dup", "password": "TOP-SECRET", "profile": "1hour"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev,
                           duplicate_mode="conflict")
        log = logs.get(1, res.log_id)
        assert "TOP-SECRET" not in str(log)

    def test_result_to_dict_no_password(self, app_ctx):
        from app.radius.services import mt_import_runner as R
        _mk_plan("1hour")
        prev = _preview([{"name": "u", "password": "SECRET", "profile": "1hour"}])
        res = R.run_import(tenant_id=1, nas=_NAS, preview=prev)
        assert "SECRET" not in str(res.to_dict())
