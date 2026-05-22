"""S1.1 — Background-job repository contract.

Pure repo tests: no Flask, no router, no worker. The job table
is the foundation every later S-track (audit / backups / alerts
/ snapshots) plugs into, so locking down the lifecycle + the
redaction contract here is the cheapest place to catch a leak.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s1_1_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
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


# ─── Migration ────────────────────────────────────────────────


def test_jobs_table_exists(app):
    """Migration 037 must run and create the table."""
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='jobs'"
        ).fetchone()
        assert row is not None


def test_jobs_table_has_expected_columns(app):
    """Pin the column shape so a future migration that drops a
    field is caught here, not in some downstream consumer."""
    with app.app_context():
        from app.radius.db.connection import db
        cols = {r["name"]
                for r in db().execute("PRAGMA table_info(jobs)").fetchall()}
    for required in (
        "id", "tenant_id", "type", "status", "progress",
        "current_step", "owner_admin_id", "router_id",
        "payload_json", "result_json", "error_message",
        "created_at", "started_at", "finished_at", "updated_at",
    ):
        assert required in cols, f"jobs table missing column: {required}"


# ─── Create + read ────────────────────────────────────────────


def test_create_returns_id_and_default_status(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="mt.diag.scan",
                        payload={"router_id": 42})
        assert jid > 0
        row = jr.get(jid)
        assert row is not None
        assert row["status"] == jr.JOB_STATUS_QUEUED
        assert row["type"] == "mt.diag.scan"
        assert row["progress"] == 0
        assert row["error_message"] == ""
        assert row["payload"] == {"router_id": 42}


def test_create_rejects_empty_type(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        with pytest.raises(ValueError):
            jr.create(tenant_id=1, type="")
        with pytest.raises(ValueError):
            jr.create(tenant_id=1, type="   ")


def test_list_recent_returns_newest_first(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        ids = [jr.create(tenant_id=1, type=f"x.{i}") for i in range(3)]
        rows = jr.list_recent(tenant_id=1, limit=10)
        # newest-first: created in order [0,1,2] → returned [2,1,0]
        assert [r["id"] for r in rows[:3]] == list(reversed(ids))


def test_list_recent_type_prefix_filters(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jr.create(tenant_id=1, type="mt.diag.scan")
        jr.create(tenant_id=1, type="mt.program.apply")
        jr.create(tenant_id=1, type="print.export")
        mt_only = jr.list_recent(tenant_id=1, type_prefix="mt.")
        types = {r["type"] for r in mt_only}
        assert types == {"mt.diag.scan", "mt.program.apply"}


def test_list_by_router_scoped(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jr.create(tenant_id=1, type="mt.x", router_id=5)
        jr.create(tenant_id=1, type="mt.y", router_id=5)
        jr.create(tenant_id=1, type="mt.z", router_id=99)
        rows = jr.list_by_router(tenant_id=1, router_id=5)
        assert len(rows) == 2
        assert all(r["router_id"] == 5 for r in rows)


# ─── State transitions ────────────────────────────────────────


def test_mark_running_sets_started_at(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x")
        jr.mark_running(jid)
        row = jr.get(jid)
        assert row["status"] == jr.JOB_STATUS_RUNNING
        assert row["started_at"], "started_at must be set on mark_running"


def test_update_progress_clamps_range(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x")
        jr.update_progress(jid, percent=-50, step="bad lower")
        assert jr.get(jid)["progress"] == 0
        jr.update_progress(jid, percent=200, step="bad upper")
        assert jr.get(jid)["progress"] == 100
        jr.update_progress(jid, percent=42,
                           step="نصف الطريق")
        row = jr.get(jid)
        assert row["progress"] == 42
        assert row["current_step"] == "نصف الطريق"


def test_mark_success_sets_terminal_state_and_progress_100(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x")
        jr.mark_running(jid)
        jr.mark_success(jid, result={"rows_exported": 124})
        row = jr.get(jid)
        assert row["status"] == jr.JOB_STATUS_SUCCESS
        assert row["progress"] == 100
        assert row["finished_at"]
        assert row["result"]["rows_exported"] == 124


def test_mark_failed_records_error(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x")
        jr.mark_running(jid)
        jr.mark_failed(jid, error="router unreachable: timeout",
                       result={"phase": "tcp-probe"})
        row = jr.get(jid)
        assert row["status"] == jr.JOB_STATUS_FAILED
        assert "router unreachable" in row["error_message"]
        assert row["finished_at"]
        assert row["result"]["phase"] == "tcp-probe"


def test_mark_failed_truncates_huge_error_strings(app):
    """A 100 KB error blob from a RouterOS trap shouldn't bloat the
    row indefinitely. The repo caps at 2000 chars."""
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x")
        huge = "x" * 50000
        jr.mark_failed(jid, error=huge)
        assert len(jr.get(jid)["error_message"]) <= 2000


def test_mark_cancelled_only_from_queued_or_waiting(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        # From queued → ok
        jid = jr.create(tenant_id=1, type="x")
        assert jr.mark_cancelled(jid) is True
        assert jr.get(jid)["status"] == jr.JOB_STATUS_CANCELLED

        # From running → refused (no-op).
        jid2 = jr.create(tenant_id=1, type="x")
        jr.mark_running(jid2)
        assert jr.mark_cancelled(jid2) is False
        assert jr.get(jid2)["status"] == jr.JOB_STATUS_RUNNING


def test_mark_cancelled_returns_false_for_unknown_job(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        assert jr.mark_cancelled(99999) is False


# ─── Redaction contract ───────────────────────────────────────


def test_payload_redacts_password_keys(app):
    """create() must NEVER store a value under a key whose name
    contains 'password' / 'secret' / 'private_key' / 'token' /
    etc. — even if the caller mistakenly passes one through.
    """
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x", payload={
            "router_id": 42,
            "api_password": "super-secret-pwd",
            "radius_secret": "shared-secret-string",
            "wg_private_key": "AAAA...",
            "bearer_token": "tok-xyz",
        })
        row = jr.get(jid)
        for k in ("api_password", "radius_secret",
                  "wg_private_key", "bearer_token"):
            assert row["payload"][k] == "***", (
                f"secret leaked through payload: {k}"
            )
        # Non-secret keys pass through.
        assert row["payload"]["router_id"] == 42


def test_payload_redacts_nested_dicts_and_lists(app):
    """Two redaction modes:
      - A secret-named key with a *scalar* value → "***".
      - A secret-named key with a *dict/list* value → the whole
        sub-tree is masked (which is what you want for a blob
        called e.g. `credentials`).
      - Non-secret keys are walked into so deeper secrets get
        masked individually.
    """
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x", payload={
            "nas": {                         # not secret → walk in
                "name": "r1",
                "credentials": {             # secret name → mask whole
                    "username": "hr",
                    "password": "p",
                },
            },
            "routers": [                     # not secret → walk in
                {"id": 1, "api_password": "pwd1"},
                {"id": 2, "api_password": "pwd2"},
            ],
        })
        p = jr.get(jid)["payload"]
        # nas itself is walked
        assert p["nas"]["name"] == "r1"
        # credentials sub-tree is masked whole (it's a secret-named key).
        assert p["nas"]["credentials"] == "***"
        # routers list is walked + each item's secret key masked.
        assert all(r["api_password"] == "***" for r in p["routers"])
        assert [r["id"] for r in p["routers"]] == [1, 2]


def test_result_blob_also_redacts_secrets(app):
    """mark_success / mark_failed go through the same redact path
    as create() — a failed Q2 apply that returns the router's
    response blob must not leak the password back into the job
    row."""
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x")
        jr.mark_success(jid, result={
            "applied_commands": ["/ip/pool/add"],
            "router_api_password": "leak!",
        })
        row = jr.get(jid)
        assert row["result"]["router_api_password"] == "***"
        assert row["result"]["applied_commands"] == ["/ip/pool/add"]


def test_secret_detection_is_case_insensitive(app):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = jr.create(tenant_id=1, type="x", payload={
            "PASSWORD": "uppercase",
            "Api_Password": "mixed",
            "RADIUS_SECRET": "shouted",
        })
        p = jr.get(jid)["payload"]
        for k in ("PASSWORD", "Api_Password", "RADIUS_SECRET"):
            assert p[k] == "***"


# ─── Catalogue / constants ────────────────────────────────────


def test_all_statuses_set_matches_lifecycle_doc():
    from app.radius.db.repos import jobs_repo as jr
    assert jr.ALL_STATUSES == {
        "queued", "running", "waiting",
        "success", "failed", "cancelled",
    }
    assert jr.TERMINAL_STATUSES == {"success", "failed", "cancelled"}
