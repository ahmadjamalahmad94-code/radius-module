"""S1.2 — Job runner contract.

The runner is the seam every later track plugs into. Tests pin:
  - enqueue creates a queued job
  - run_job calls the registered handler + marks success
  - handler exception → status=failed + error_message
  - unknown type → UnknownJobType raised, job untouched
  - cancelled before run → no handler call, row stays cancelled
  - terminal job → idempotent (no double execution)
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s1_2_")
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


@pytest.fixture
def runner(app):
    """Reset the handler registry between tests so one case can't
    leak handlers into another."""
    with app.app_context():
        from app.radius.services import jobs_runner
        jobs_runner._reset_handlers_for_tests()
        yield jobs_runner
        jobs_runner._reset_handlers_for_tests()


# ─── Registry ─────────────────────────────────────────────────


def test_register_handler_indexes_by_type(app, runner):
    @runner.register_handler("x.first")
    def _h(job, payload): return {}

    @runner.register_handler("x.second")
    def _h2(job, payload): return {}

    assert set(runner.known_job_types()) == {"x.first", "x.second"}


def test_register_handler_rejects_empty_type(app, runner):
    with pytest.raises(ValueError):
        @runner.register_handler("")
        def _h(job, payload): return {}


# ─── Enqueue ──────────────────────────────────────────────────


def test_enqueue_creates_queued_job(app, runner):
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = runner.enqueue(
            tenant_id=1, type="mt.x", router_id=42,
            payload={"target": "ether2"},
        )
        row = jr.get(jid)
        assert row["status"] == "queued"
        assert row["router_id"] == 42
        assert row["payload"]["target"] == "ether2"


# ─── Happy path ───────────────────────────────────────────────


def test_run_job_calls_handler_and_marks_success(app, runner):
    seen = {}

    @runner.register_handler("x.work")
    def _h(job, payload):
        seen["job_id"] = job["id"]
        seen["payload"] = payload
        return {"answer": 42}

    with app.app_context():
        jid = runner.enqueue(tenant_id=1, type="x.work",
                              payload={"input": "hello"})
        row = runner.run_job(jid)
        assert row["status"] == "success"
        assert row["progress"] == 100
        assert row["result"]["answer"] == 42
        assert seen["job_id"] == jid
        assert seen["payload"]["input"] == "hello"


def test_run_job_handler_progress_calls_update_repo(app, runner):
    @runner.register_handler("x.progress")
    def _h(job, payload):
        runner.progress(job["id"], 25, "ربع الطريق")
        runner.progress(job["id"], 75, "ثلاثة أرباع")
        return {}

    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = runner.enqueue(tenant_id=1, type="x.progress")
        runner.run_job(jid)
        row = jr.get(jid)
        # Final progress overridden by mark_success → 100.
        assert row["progress"] == 100


# ─── Handler exceptions ───────────────────────────────────────


def test_run_job_handler_exception_marks_failed(app, runner):
    @runner.register_handler("x.boom")
    def _h(job, payload):
        raise RuntimeError("router refused")

    with app.app_context():
        jid = runner.enqueue(tenant_id=1, type="x.boom")
        row = runner.run_job(jid)
        assert row["status"] == "failed"
        assert "router refused" in row["error_message"]
        # Traceback captured (for operator triage) but stored in
        # the result blob, not the error_message which is the
        # short user-facing line.
        assert "traceback" in row["result"]


def test_run_job_handler_value_error_keeps_short_error_message(app, runner):
    @runner.register_handler("x.valerr")
    def _h(job, payload):
        raise ValueError("CIDR غير صالح")

    with app.app_context():
        jid = runner.enqueue(tenant_id=1, type="x.valerr")
        row = runner.run_job(jid)
        assert row["status"] == "failed"
        assert "CIDR غير صالح" in row["error_message"]


# ─── Unknown type ─────────────────────────────────────────────


def test_run_job_unknown_type_raises_without_touching_row(app, runner):
    """If no handler is registered, the runner must NOT mark the
    job failed — the handler may be registered by a module that
    imports later. Surface as exception, leave the row queued so
    the caller can decide."""
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = runner.enqueue(tenant_id=1, type="x.no.such.handler")
        with pytest.raises(runner.UnknownJobType):
            runner.run_job(jid)
        # Row stays queued.
        assert jr.get(jid)["status"] == "queued"


# ─── Cancellation / idempotency ───────────────────────────────


def test_cancelled_job_is_not_executed(app, runner):
    calls = []

    @runner.register_handler("x.never")
    def _h(job, payload):
        calls.append(job["id"])

    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        jid = runner.enqueue(tenant_id=1, type="x.never")
        jr.mark_cancelled(jid)
        row = runner.run_job(jid)
        # Handler never invoked; row keeps cancelled state.
        assert calls == []
        assert row["status"] == "cancelled"


def test_terminal_job_run_again_is_noop(app, runner):
    """A worker that re-pulls a successful job (e.g. after a
    crash + replay) must NOT execute the handler twice. The
    runner short-circuits on terminal status."""
    calls = []

    @runner.register_handler("x.once")
    def _h(job, payload):
        calls.append(1)
        return {}

    with app.app_context():
        jid = runner.enqueue(tenant_id=1, type="x.once")
        runner.run_job(jid)
        runner.run_job(jid)   # second invocation
        assert calls == [1]


# ─── Missing job ──────────────────────────────────────────────


def test_run_job_missing_id_raises(app, runner):
    with app.app_context():
        with pytest.raises(ValueError):
            runner.run_job(99999)
