"""Audit regression suite (2026-06): recycle-bin + lifecycle/archive.

Covers the contracts a strong audit must keep green:

  1. Soft-delete → restore → round-trip leaves the DB consistent and the
     row recoverable (no orphaned / hard-deleted state).
  2. Retention expiry locks restore (restore_allowed flips to False); the
     recycle-bin page disables the restore button. No auto hard-delete.
  3. lifecycle.run() respects policy: disabled / unsupported-entity policies
     archive nothing; a supported policy archives only due items; the run is
     idempotent (a second run does not double-archive).
  4. RBAC gating: a limited (viewer) admin is 403'd from BOTH the recycle-bin
     GET page and the restore/lifecycle write actions, with the friendly
     in-panel 403 (not the raw werkzeug page). The owner/super bypasses.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_rclc_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _iso(delta_days: int = 0) -> str:
    return (datetime.utcnow() + timedelta(days=delta_days)).replace(
        microsecond=0).isoformat() + "Z"


def _mk(*, is_super=False, role=None):
    from app.radius.db.repos import admins_repo
    rid = None
    if role:
        r = admins_repo.get_role_by_name(role)
        rid = r.id if r else None
    return admins_repo.create_admin(
        username=f"rclc_{uuid4().hex[:8]}", password="rclc-pass",
        full_name="RCLC", role_id=rid, is_super_admin=is_super,
    )


def _login(client, username):
    r = client.post("/admin/radius/login",
                    data={"username": username, "password": "rclc-pass"},
                    follow_redirects=False)
    assert r.status_code in {302, 303}, r.status_code


def _csrf(client):
    client.get("/admin/radius/")
    with client.session_transaction() as s:
        return s.get("_csrf_token")


def _seed_subscriber(username: str, *, expire_days: int = -5) -> int:
    from app.radius.db.connection import transaction
    now = _iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO access_plans(tenant_id, name, enabled, created_at) "
            "VALUES(1,'RCLC plan',1,?)", (now,))
        plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            "INSERT INTO subscribers(tenant_id, username, password, plan_id, "
            "status, expire_at, created_at) VALUES(1,?,?,?, 'active', ?, ?)",
            (username, "pw", plan_id, _iso(expire_days), now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


# ─────────────────── 1) soft-delete → restore round-trip ───────────────────

def test_soft_delete_then_restore_round_trip_is_consistent(app):
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import subscribers_repo
        username = "rt_" + uuid4().hex[:8]
        sub_id = _seed_subscriber(username)

        # DELETE = soft-delete (recoverable), never a hard DELETE FROM.
        assert subscribers_repo.archive_subscriber(1, username, actor="qa") is True
        row = db().execute(
            "SELECT deleted_at, status FROM subscribers WHERE id=?",
            (sub_id,)).fetchone()
        assert row["deleted_at"], "soft-delete must set deleted_at (row stays)"
        assert row["status"] == "disabled"

        # RESTORE brings the row back, clears tombstone fields, no orphan.
        assert subscribers_repo.restore_subscriber(1, username, actor="qa") is True
        row = db().execute(
            "SELECT deleted_at, deleted_by, delete_reason FROM subscribers "
            "WHERE id=?", (sub_id,)).fetchone()
        assert row["deleted_at"] is None
        assert (row["deleted_by"] or "") == ""
        assert (row["delete_reason"] or "") == ""
        # the row was never physically removed
        assert db().execute(
            "SELECT COUNT(*) c FROM subscribers WHERE id=?",
            (sub_id,)).fetchone()["c"] == 1


def test_admin_soft_delete_lands_in_bin_and_restores_disabled(app):
    """A deleted admin must land in the recycle-bin (deleted_at set) and come
    back DISABLED after restore (review before reuse)."""
    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import admins_repo
        a = _mk(is_super=False, role="viewer")

        assert admins_repo.archive_admin(a.id, actor="qa", reason="x") is True
        row = db().execute(
            "SELECT deleted_at, enabled FROM admins WHERE id=?", (a.id,)).fetchone()
        assert row["deleted_at"] and row["enabled"] == 0

        assert admins_repo.restore_admin(a.id, actor="qa") is True
        row = db().execute(
            "SELECT deleted_at, enabled FROM admins WHERE id=?", (a.id,)).fetchone()
        assert row["deleted_at"] is None
        assert row["enabled"] == 0, "restored admin must stay disabled until re-enabled"


# ─────────────────── 2) retention expiry locks restore ───────────────────

def test_retention_status_locks_restore_after_expiry(app):
    with app.app_context():
        from app.radius.services.lifecycle import retention_status
        # no retention window → always restorable
        assert retention_status({})["restore_allowed"] is True
        # future window → restorable
        future = retention_status({"retention_expires_at": _iso(30)})
        assert future["restore_allowed"] is True
        assert future["retention_expired"] is False
        # past window → restore locked (NOT hard-deleted, just locked)
        past = retention_status({"retention_expires_at": _iso(-1)})
        assert past["restore_allowed"] is False
        assert past["retention_expired"] is True


def test_recycle_bin_page_disables_restore_for_expired_item(client):
    _mk(is_super=True)  # owner occupies id #1, bypasses guards
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "owner"
        s["tenant_id"] = 1
        s["is_super_admin"] = True
    # seed an archived subscriber whose retention window already expired
    username = "exp_" + uuid4().hex[:8]
    sub_id = _seed_subscriber(username)
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute(
            "UPDATE subscribers SET deleted_at=?, archive_source='auto', "
            "retention_expires_at=? WHERE id=?",
            (_iso(-10), _iso(-1), sub_id))
    res = client.get("/admin/radius/recycle-bin?entity_type=subscribers")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert username in html
    # expired → "غير متاحة" (disabled) button is shown, not an active restore
    assert "غير متاحة" in html


# ─────────────────── 3) lifecycle.run respects policy ───────────────────

def _seed_expired_card():
    from app.radius.db.connection import transaction
    now = _iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO access_plans(tenant_id, name, enabled, created_at) "
            "VALUES(1,'LCpol',1,?)", (now,))
        plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            "INSERT INTO card_batches(tenant_id, batch_code, package_name, "
            "plan_id, count, generated, used, created_by, status, created_at, "
            "metadata, original_count, settlement_count) "
            "VALUES(1,'LCP','LCP',?,1,1,0,'t','active',?, '{}',1,1)",
            (plan_id, now))
        batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
            "expire_at, created_at) VALUES(1,?,?,?,?,?,?)",
            (batch_id, "lcp-card", "s", plan_id, _iso(-5), now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def test_run_skips_disabled_policy(app):
    with app.app_context():
        from app.radius.services import lifecycle
        from app.radius.db.connection import db
        card_id = _seed_expired_card()
        lifecycle.create_policy(1, {
            "entity_type": "card", "trigger_type": "expired_at",
            "delay_value": 2, "delay_unit": "days",
            "retention_value": 90, "retention_unit": "days",
            "enabled": False,  # disabled → must archive nothing
        }, actor="qa")
        result = lifecycle.run(1, actor="qa")
        assert result["changed"] == 0
        assert db().execute(
            "SELECT deleted_at FROM cards WHERE id=?",
            (card_id,)).fetchone()["deleted_at"] is None


def test_run_skips_unsupported_entity_policy(app):
    with app.app_context():
        from app.radius.services import lifecycle
        _seed_expired_card()
        # card_batch is a valid policy entity but NOT executed by the worker
        lifecycle.create_policy(1, {
            "entity_type": "card_batch", "trigger_type": "expired_at",
            "delay_value": 0, "delay_unit": "days",
            "retention_value": 90, "retention_unit": "days",
            "enabled": True,
        }, actor="qa")
        result = lifecycle.run(1, actor="qa")
        assert result["changed"] == 0
        assert result["skipped"] >= 1
        assert any(i.get("status") == "skipped" for i in result["items"])


def test_run_archives_due_card_and_is_idempotent(app):
    with app.app_context():
        from app.radius.services import lifecycle
        from app.radius.db.connection import db
        card_id = _seed_expired_card()
        lifecycle.create_policy(1, {
            "entity_type": "card", "trigger_type": "expired_at",
            "delay_value": 2, "delay_unit": "days",
            "retention_value": 90, "retention_unit": "days",
            "enabled": True,
        }, actor="qa")
        first = lifecycle.run(1, actor="qa")
        assert first["changed"] == 1
        row = db().execute(
            "SELECT deleted_at, archive_source, retention_expires_at, "
            "archive_policy_id FROM cards WHERE id=?", (card_id,)).fetchone()
        assert row["deleted_at"] and row["archive_source"] == "auto"
        assert row["retention_expires_at"] and row["archive_policy_id"]
        # second run must NOT re-archive the same card (idempotent)
        second = lifecycle.run(1, actor="qa")
        assert second["changed"] == 0
        # an audit_log row was written for the archive action
        assert db().execute(
            "SELECT COUNT(*) c FROM audit_log WHERE action='lifecycle.archive'"
        ).fetchone()["c"] == 1


# ─────────────────── 4) RBAC gating + friendly 403 ───────────────────

def test_viewer_blocked_from_recycle_bin_page(app, client):
    _mk(is_super=True)                       # owner occupies id #1
    limited = _mk(is_super=False, role="viewer")
    _login(client, limited.username)
    res = client.get("/admin/radius/recycle-bin", follow_redirects=False)
    assert res.status_code == 403
    # friendly in-panel 403, not the raw werkzeug page
    html = res.get_data(as_text=True)
    assert "data-mt-forbidden-page" in html or "ليس لديك صلاحية الوصول" in html


def test_viewer_blocked_from_recycle_restore_and_lifecycle_writes(app, client):
    _mk(is_super=True)
    limited = _mk(is_super=False, role="viewer")
    _login(client, limited.username)
    for url in ("/admin/radius/recycle-bin/subscribers/1/restore",
                "/admin/radius/lifecycle/run",
                "/admin/radius/lifecycle/policies"):
        tok = _csrf(client)
        res = client.post(url, data={"_csrf_token": tok}, follow_redirects=False)
        assert res.status_code == 403, f"{url} expected 403, got {res.status_code}"


def test_owner_reaches_recycle_bin_and_lifecycle(app, client):
    owner = _mk(is_super=True)
    _login(client, owner.username)
    assert client.get("/admin/radius/recycle-bin").status_code == 200
    assert client.get("/admin/radius/lifecycle").status_code == 200
