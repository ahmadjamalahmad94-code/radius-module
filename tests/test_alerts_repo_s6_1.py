"""S6.1 — alerts_repo contract."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s6_1_")
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


def test_alerts_table_created(app):
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='alerts'"
        ).fetchone()
        assert row is not None


def test_open_creates_row_with_first_and_last_seen(app):
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        aid = ar.open(
            tenant_id=1, rule="router_offline",
            dedup_key="router_offline:42",
            router_id=42, severity="critical",
            title_ar="الراوتر مفصول",
            explanation_ar="لم نستلم استجابة منذ 5 دقائق",
            recommended_action_ar="افحص الـ uplink",
            evidence={"last_seen_minutes_ago": 5},
        )
        row = ar.get_by_id(1, aid)
        assert row["status"] == "open"
        assert row["router_id"] == 42
        assert row["severity"] == "critical"
        assert row["first_seen"] == row["last_seen"]
        assert row["evidence"]["last_seen_minutes_ago"] == 5


def test_repeat_open_bumps_last_seen_without_duplicate_row(app):
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        from app.radius.db.connection import db
        ar.open(tenant_id=1, rule="x", dedup_key="x:1",
                title_ar="t1")
        ar.open(tenant_id=1, rule="x", dedup_key="x:1",
                title_ar="t1-updated", severity="warning")
        cnt = db().execute(
            "SELECT COUNT(*) AS c FROM alerts "
            "WHERE tenant_id=1 AND dedup_key='x:1'"
        ).fetchone()["c"]
        assert cnt == 1


def test_resolve_marks_status_and_returns_true(app):
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        ar.open(tenant_id=1, rule="x", dedup_key="x:2",
                title_ar="t2")
        assert ar.resolve(1, "x:2") is True
        rows = ar.list_open(1)
        assert all(r["dedup_key"] != "x:2" for r in rows)
        resolved = ar.list_resolved(1)
        assert any(r["dedup_key"] == "x:2" for r in resolved)


def test_resolve_returns_false_for_already_resolved(app):
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        ar.open(tenant_id=1, rule="x", dedup_key="x:3", title_ar="t3")
        ar.resolve(1, "x:3")
        # Second call → already resolved.
        assert ar.resolve(1, "x:3") is False


def test_resolve_then_reopen_revives_row(app):
    """The condition came back — `open()` flips status from
    resolved → open and clears resolved_at."""
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        ar.open(tenant_id=1, rule="x", dedup_key="x:4", title_ar="t4")
        ar.resolve(1, "x:4")
        ar.open(tenant_id=1, rule="x", dedup_key="x:4", title_ar="t4")
        rows = ar.list_open(1)
        target = next((r for r in rows if r["dedup_key"] == "x:4"), None)
        assert target is not None
        assert target["status"] == "open"
        assert target["resolved_at"] == ""


def test_severity_clamped_to_allowlist(app):
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        aid = ar.open(tenant_id=1, rule="x", dedup_key="x:sev",
                      title_ar="t", severity="apocalyptic")
        row = ar.get_by_id(1, aid)
        assert row["severity"] == "info"


def test_evidence_redacts_secret_keys(app):
    """The shared redact path from jobs_repo runs over
    evidence too — no leaks via alerts."""
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        aid = ar.open(
            tenant_id=1, rule="x", dedup_key="x:secret",
            title_ar="t",
            evidence={"api_password": "leak",
                      "router_id": 42},
        )
        row = ar.get_by_id(1, aid)
        assert row["evidence"]["api_password"] == "***"
        assert row["evidence"]["router_id"] == 42


def test_list_open_filters_by_router_and_severity(app):
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        ar.open(tenant_id=1, rule="x", dedup_key="a:1", title_ar="t",
                router_id=10, severity="warning")
        ar.open(tenant_id=1, rule="x", dedup_key="a:2", title_ar="t",
                router_id=20, severity="critical")
        ar.open(tenant_id=1, rule="x", dedup_key="a:3", title_ar="t",
                router_id=10, severity="critical")
        assert len(ar.list_open(1, router_id=10)) == 2
        assert len(ar.list_open(1, severity="critical")) == 2
        assert len(ar.list_open(1, router_id=10,
                                severity="critical")) == 1


def test_get_by_id_is_tenant_scoped(app):
    with app.app_context():
        from app.radius.db.repos import alerts_repo as ar
        aid = ar.open(tenant_id=1, rule="x",
                      dedup_key="scope:1", title_ar="t")
        assert ar.get_by_id(1, aid) is not None
        assert ar.get_by_id(2, aid) is None
