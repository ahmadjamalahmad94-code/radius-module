"""R6 (r6-ops, client20): استرجاعُ مشتركٍ من سلّة المحذوفات كان يُعيده **معطّلًا
دائمًا** — ``restore_subscriber`` يكتب ``status='disabled'`` والأرشفةُ كانت قد
محت حالتَه السابقة. فمشتركٌ مفعَّلٌ حُذف بالخطأ ثمّ استُرجع يبقى مرفوضًا في
RADIUS بلا أيّ رسالة (الـAPI يقول «استُعيد»): 60/60 على client20.

الأرشفةُ تحفظ الحالةَ السابقة في ``metadata.status_before_archive``
والاسترجاعُ يُعيدها (ويمحو المفتاح)؛ صفٌّ أُرشف قبل هذا الإصلاح (بلا مفتاح)
يُستعاد معطّلًا كما كان — لا تفعيلَ لما لا نعرف حالتَه. شغّل هذا الملف وحده.
"""
from __future__ import annotations

import json
import os

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "r6restore.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield flask_app


def db():
    from app.radius.db.connection import db as live
    return live()


def _seed(username, status, metadata=""):
    db().execute(
        "INSERT INTO subscribers(tenant_id,username,password,status,user_type,metadata,created_at)"
        " VALUES(1,?,'pw1234',?,'subscriber',?,datetime('now'))", (username, status, metadata))
    db().commit()


def _row(username):
    return db().execute("SELECT status, deleted_at, metadata FROM subscribers WHERE username=?",
                        (username,)).fetchone()


@pytest.mark.parametrize("status", ["enabled", "disabled", "suspended"])
def test_restore_brings_back_the_status_it_had(app_ctx, status):
    from app.radius.db.repos import subscribers_repo
    _seed("s_" + status, status)
    assert subscribers_repo.archive_subscriber(1, "s_" + status, actor="t") is True
    r = _row("s_" + status)
    assert r["deleted_at"] and r["status"] == "disabled"      # archived rows never authenticate
    assert subscribers_repo.restore_subscriber(1, "s_" + status, actor="t") is True
    r = _row("s_" + status)
    assert r["deleted_at"] is None
    assert r["status"] == status
    assert "status_before_archive" not in (r["metadata"] or "")


def test_existing_metadata_is_preserved(app_ctx):
    from app.radius.db.repos import subscribers_repo
    _seed("meta", "enabled", json.dumps({"mikrotik": {"profile": "p1"}}))
    subscribers_repo.archive_subscriber(1, "meta", actor="t")
    subscribers_repo.restore_subscriber(1, "meta", actor="t")
    r = _row("meta")
    assert r["status"] == "enabled"
    assert json.loads(r["metadata"]) == {"mikrotik": {"profile": "p1"}}


def test_row_archived_before_the_fix_restores_disabled(app_ctx):
    """لا مفتاح محفوظ ⇒ معطّل (السلوك السابق) — لا نُفعّل ما لا نعرف حالتَه."""
    from app.radius.db.repos import subscribers_repo
    _seed("legacy", "enabled")
    db().execute("UPDATE subscribers SET deleted_at=datetime('now'), status='disabled'"
                 " WHERE username='legacy'")
    db().commit()
    assert subscribers_repo.restore_subscriber(1, "legacy", actor="t") is True
    assert _row("legacy")["status"] == "disabled"


def test_invalid_metadata_text_is_left_alone(app_ctx):
    from app.radius.db.repos import subscribers_repo
    _seed("badmeta", "enabled", "not-json")
    subscribers_repo.archive_subscriber(1, "badmeta", actor="t")
    assert _row("badmeta")["metadata"] == "not-json"
    subscribers_repo.restore_subscriber(1, "badmeta", actor="t")
    r = _row("badmeta")
    assert r["metadata"] == "not-json" and r["status"] == "disabled"


def test_lifecycle_auto_archive_also_remembers_status(app_ctx):
    from app.radius.db.repos import lifecycle_repo, subscribers_repo
    _seed("auto1", "enabled")
    sid = db().execute("SELECT id FROM subscribers WHERE username='auto1'").fetchone()["id"]
    from app.radius.db.connection import transaction
    with transaction() as conn:
        assert lifecycle_repo.archive_subscriber(conn, tenant_id=1, subscriber_id=sid, policy_id=1,
                                                 actor="t", reason="r", retention_expires_at="")
    assert subscribers_repo.restore_subscriber(1, "auto1", actor="t") is True
    assert _row("auto1")["status"] == "enabled"
