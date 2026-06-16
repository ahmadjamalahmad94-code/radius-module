"""feat/mikrotik-user-import — increment 1: schema + import-logs repo.

يتحقّق أن migration 124 يطبّق api_type + جدول mikrotik_import_logs،
وأن nas_repo يقرأ/يضبط api_type، وأن repo السجلّات يعمل. شغّل الملف وحده.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "mt_import.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


def _seed_nas(rid=900):
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso
    with transaction() as conn:
        conn.execute("INSERT INTO nas_devices(id, tenant_id, name, address, secret, vendor, "
                     "nas_type, api_user, api_password, enabled, created_at) "
                     "VALUES (?,1,'MT-Imp','10.0.0.1','s','mikrotik','hotspot','admin','pw',1,?)",
                     (rid, now_iso()))
    return rid


def test_migration_applied_columns_and_table(app_ctx):
    from app.radius.db.connection import db
    cols = {r["name"] for r in db().execute("PRAGMA table_info(nas_devices)").fetchall()}
    assert "api_type" in cols
    t = db().execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mikrotik_import_logs'").fetchone()
    assert t is not None


def test_nas_api_type_default_and_set(app_ctx):
    from app.radius.db.repos import nas_repo
    rid = _seed_nas()
    nas = nas_repo.get_nas(1, rid)
    assert nas.api_type == "auto"            # افتراضي
    nas_repo.set_api_type(1, rid, "rest")
    assert nas_repo.get_nas(1, rid).api_type == "rest"
    # قيمة غير صالحة → auto
    nas_repo.set_api_type(1, rid, "bogus")
    assert nas_repo.get_nas(1, rid).api_type == "auto"


def test_import_log_create_list_get(app_ctx):
    from app.radius.db.repos import mikrotik_import_logs_repo as repo
    lid = repo.create(
        tenant_id=1, nas_id=900, nas_name="MT-Imp", import_type="hotspot",
        source="/ip hotspot user", transport="rest", duplicate_mode="skip_existing",
        total=10, imported=7, updated=1, skipped=2, failed=0,
        errors=[{"username": "u1", "error": "no password"}],
        status="completed", started_by=5, started_by_name="admin")
    assert lid > 0
    got = repo.get(1, lid)
    assert got["import_type"] == "hotspot" and got["imported_count"] == 7
    assert got["errors"] == [{"username": "u1", "error": "no password"}]
    lst = repo.list_for_tenant(1, nas_id=900)
    assert len(lst) == 1 and lst[0]["id"] == lid
    # tenant آخر لا يرى السجلّ
    assert repo.list_for_tenant(2) == []


def test_no_plaintext_password_in_log(app_ctx):
    from app.radius.db.repos import mikrotik_import_logs_repo as repo
    from app.radius.db.connection import db
    repo.create(tenant_id=1, nas_id=900, nas_name="x", import_type="broadband",
                message="done", errors=[{"username": "u", "error": "bad"}])
    raw = db().execute("SELECT * FROM mikrotik_import_logs").fetchone()
    # لا أعمدة لكلمات المرور أصلًا
    assert "password" not in raw.keys()
