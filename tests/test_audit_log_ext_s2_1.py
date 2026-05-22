"""S2.1 — Audit log schema extension + service signature.

The audit_log table grew six promoted columns in migration 038
so the S2.2 UI can filter by severity / result / router without
parsing payload_json on every query. This file pins:

  - Migration applied + columns present.
  - record() accepts new kwargs with safe defaults.
  - secret-keyed values in payload/before/after are redacted
    via the shared helper (same as jobs_repo).
  - severity allowlist enforced.
  - error_message truncated at 2000 chars.
  - recent() filters work (router_id, action, severity, search).
  - get_by_id() is tenant-scoped.
  - Existing callers (no new kwargs) still work — backward-compat.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s2_1_")
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


def test_audit_log_has_new_columns(app):
    with app.app_context():
        from app.radius.db.connection import db
        cols = {r["name"]
                for r in db().execute(
                    "PRAGMA table_info(audit_log)").fetchall()}
    for required in (
        "severity", "result_status", "router_id",
        "error_message", "before_json", "after_json",
    ):
        assert required in cols, f"audit_log missing {required}"


# ─── Backward-compat ──────────────────────────────────────────


def test_record_works_without_new_kwargs(app):
    """Existing callers (in app/api/v1/mikrotik_control.py and
    elsewhere) pass only the original signature. Those calls
    must keep returning a valid row."""
    with app.app_context():
        from app.radius.db.repos import audit_repo
        new_id = audit_repo.record(
            tenant_id=1, actor="op", action="mt.x",
            target_type="mikrotik_nas", target_id="42",
            payload={"k": "v"},
        )
        row = audit_repo.get_by_id(1, new_id)
        assert row is not None
        assert row["action"] == "mt.x"
        assert row["severity"] == "info"
        assert row["result_status"] == ""
        assert row["router_id"] is None
        assert row["error_message"] == ""


# ─── New fields ───────────────────────────────────────────────


def test_record_stores_all_new_fields(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        new_id = audit_repo.record(
            tenant_id=1, actor="op", action="mt.program.apply",
            target_type="mikrotik_nas", target_id="42",
            severity="warning",
            result_status="partial",
            router_id=42,
            error_message="پارت من القائمة فشل",
            before={"enabled": True},
            after={"enabled": False, "address": "10.0.0.1/24"},
        )
        row = audit_repo.get_by_id(1, new_id)
        assert row["severity"] == "warning"
        assert row["result_status"] == "partial"
        assert row["router_id"] == 42
        assert "پارت" in row["error_message"]
        import json
        assert json.loads(row["before_json"])["enabled"] is True
        assert json.loads(row["after_json"])["address"] == "10.0.0.1/24"


def test_severity_is_clamped_to_allowlist(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        new_id = audit_repo.record(
            tenant_id=1, actor="op", action="x",
            target_type="t", target_id="1",
            severity="catastrophic-OOPS",
        )
        row = audit_repo.get_by_id(1, new_id)
        # Unknown severity falls back to info — better safe
        # than mid-row CHECK failure.
        assert row["severity"] == "info"


def test_error_message_truncated_at_2000_chars(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        new_id = audit_repo.record(
            tenant_id=1, actor="op", action="x",
            target_type="t", target_id="1",
            error_message="x" * 50000,
        )
        row = audit_repo.get_by_id(1, new_id)
        assert len(row["error_message"]) <= 2000


# ─── Redaction ────────────────────────────────────────────────


def test_payload_before_after_all_redact_secrets(app):
    """The shared `_redact` from jobs_repo runs over every dict
    field. Pin it for all three so a leak in any single path is
    caught."""
    with app.app_context():
        from app.radius.db.repos import audit_repo
        import json
        new_id = audit_repo.record(
            tenant_id=1, actor="op", action="x",
            target_type="t", target_id="1",
            payload={"api_password": "leak-payload"},
            before={"radius_secret": "leak-before"},
            after={"wg_private_key": "leak-after"},
        )
        row = audit_repo.get_by_id(1, new_id)
        assert json.loads(row["payload_json"])["api_password"] == "***"
        assert json.loads(row["before_json"])["radius_secret"] == "***"
        assert json.loads(row["after_json"])["wg_private_key"] == "***"


# ─── recent() filters ─────────────────────────────────────────


def test_recent_filters_by_router_id(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        audit_repo.record(tenant_id=1, actor="o", action="x",
                          target_type="t", target_id="1",
                          router_id=10)
        audit_repo.record(tenant_id=1, actor="o", action="y",
                          target_type="t", target_id="1",
                          router_id=20)
        audit_repo.record(tenant_id=1, actor="o", action="z",
                          target_type="t", target_id="1")
        rows = audit_repo.recent(1, router_id=10)
        assert len(rows) == 1
        assert rows[0]["action"] == "x"


def test_recent_filters_by_severity_and_action(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        audit_repo.record(tenant_id=1, actor="o", action="apply",
                          target_type="t", target_id="1",
                          severity="warning")
        audit_repo.record(tenant_id=1, actor="o", action="apply",
                          target_type="t", target_id="1",
                          severity="info")
        audit_repo.record(tenant_id=1, actor="o", action="rollback",
                          target_type="t", target_id="1",
                          severity="warning")
        rows = audit_repo.recent(1, action="apply", severity="warning")
        assert len(rows) == 1
        assert rows[0]["severity"] == "warning"
        assert rows[0]["action"] == "apply"


def test_recent_search_runs_like_over_id_actor_action(app):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        audit_repo.record(tenant_id=1, actor="alice", action="mt.x",
                          target_type="t", target_id="r-special-7")
        audit_repo.record(tenant_id=1, actor="bob", action="mt.y",
                          target_type="t", target_id="r-1")
        rows = audit_repo.recent(1, search="special")
        assert len(rows) == 1
        rows = audit_repo.recent(1, search="alice")
        assert len(rows) == 1
        rows = audit_repo.recent(1, search="mt.y")
        assert len(rows) == 1


# ─── get_by_id ────────────────────────────────────────────────


def test_get_by_id_is_tenant_scoped(app):
    """Even with a valid id, looking it up under the wrong
    tenant_id must return None — no cross-tenant existence leak."""
    with app.app_context():
        from app.radius.db.repos import audit_repo
        new_id = audit_repo.record(
            tenant_id=1, actor="o", action="x",
            target_type="t", target_id="1",
        )
        assert audit_repo.get_by_id(1, new_id) is not None
        assert audit_repo.get_by_id(2, new_id) is None


# ─── Service layer passthrough ────────────────────────────────


def test_service_record_passes_new_kwargs(app):
    """get_audit_service().record(...) must surface the new
    columns when callers provide them."""
    with app.app_context():
        from app.radius.services.audit import get_audit_service
        from app.radius.db.repos import audit_repo
        entry = get_audit_service().record(
            actor="op", action="mt.program.apply",
            target_type="mikrotik_nas", target_id="7",
            severity="critical",
            result_status="failed",
            router_id=7,
            error_message="router unreachable",
        )
        # Service returns its own entry struct (legacy); we look
        # the row up in the DB to verify the promotion happened.
        rows = audit_repo.recent(1, router_id=7)
        assert len(rows) == 1
        assert rows[0]["severity"] == "critical"
        assert rows[0]["result_status"] == "failed"
        assert "unreachable" in rows[0]["error_message"]
