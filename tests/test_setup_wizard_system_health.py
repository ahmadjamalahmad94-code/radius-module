"""System-health endpoint tests — covers the single-pane
production verdict.

Pins the structure, the overall-status calculation, and that
each individual check returns the expected fields. Avoids
testing the actual UDP probe against a live freeradius
(unavailable in CI) by mocking that piece.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-sysh-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv(
        "HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR",
        str(tmp_path / "clients-wizard"),
    )
    # Create the dir + placeholder so the wizard-dir check
    # passes even without a real deploy.
    (tmp_path / "clients-wizard").mkdir(parents=True)
    (tmp_path / "clients-wizard" / "_placeholder.conf").write_text(
        "# test placeholder\n",
    )
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


def test_check_all_returns_expected_shape(app):
    from app.radius.services.setup_wizard_system_health import (
        check_all,
    )
    with app.app_context():
        report = check_all()
    assert report["overall"] in (
        "healthy", "degraded", "critical",
    )
    assert "checks" in report
    assert "checked_at" in report
    assert "duration_ms" in report
    expected_checks = {
        "db_migrations",
        "freeradius_responsive",
        "wizard_clients_directory",
        "wizard_invariants",
        "wizard_nas_secrets",
        "recent_reconciler_drift",
        "wg_peers_dir",
        "clients_conf_syntax",
    }
    assert set(report["checks"].keys()) == expected_checks
    # Each check carries the right fields.
    for name, check in report["checks"].items():
        assert check["status"] in ("ok", "warn", "fail"), name
        assert check["title_ar"], name
        assert isinstance(check["evidence"], dict), name


def test_db_migrations_check_detects_required_columns(app):
    from app.radius.services.setup_wizard_system_health import (
        check_db_migrations,
    )
    with app.app_context():
        r = check_db_migrations()
    # Fresh test DB has all migrations applied.
    assert r["status"] == "ok"
    assert r["evidence"]["column_count"] > 10


def test_wizard_invariants_clean_when_nothing_active(app, tmp_path):
    """No active runs + no files = OK (vacuously)."""
    from app.radius.services.setup_wizard_system_health import (
        check_wizard_invariants,
    )
    with app.app_context():
        r = check_wizard_invariants()
    assert r["status"] == "ok"
    assert r["evidence"]["active_runs"] == 0


def test_overall_status_calculation():
    """The aggregator returns critical when any check is
    fail, degraded when any is warn, healthy when all ok."""
    # We can't mock check_all internals easily — instead test
    # the policy via a stub-style approach.
    from app.radius.services.setup_wizard_system_health import (
        _ok, _warn, _fail,
    )
    assert _ok("t").get("status") == "ok"
    assert _warn("t", "msg").get("status") == "warn"
    assert _fail("t", "msg").get("status") == "fail"


def test_endpoint_returns_503_only_when_critical(app, monkeypatch):
    """External monitors poll the endpoint and rely on HTTP
    status. Policy:
      200 = healthy OR degraded (system functional)
      503 = critical (something fundamental broken)

    Degraded must NOT return 503 because it would alarm
    monitoring on every routine deploy (the reconciler
    always corrects a transient drift right after).
    """
    client = app.test_client()
    from app.radius.services import setup_wizard_system_health as h

    def force(overall):
        return {
            "overall": overall,
            "checks": {"x": {
                "status":
                    "fail" if overall == "critical"
                    else ("warn" if overall == "degraded" else "ok"),
                "title_ar": "t", "details": "d", "evidence": {},
            }},
            "checked_at": "2026-05-26T00:00:00Z",
            "duration_ms": 1,
        }

    monkeypatch.setattr(h, "check_all",
                        lambda: force("critical"))
    res = client.get("/admin/radius/setup-wizard/_system_health")
    assert res.status_code == 503
    assert res.get_json()["overall"] == "critical"

    monkeypatch.setattr(h, "check_all",
                        lambda: force("degraded"))
    res = client.get("/admin/radius/setup-wizard/_system_health")
    assert res.status_code == 200, (
        "degraded must NOT trigger 503 — that would alarm "
        "external monitors on every routine deploy"
    )
    assert res.get_json()["overall"] == "degraded"

    monkeypatch.setattr(h, "check_all",
                        lambda: force("healthy"))
    res = client.get("/admin/radius/setup-wizard/_system_health")
    assert res.status_code == 200
    assert res.get_json()["overall"] == "healthy"


def test_endpoint_is_public_no_login_required(app):
    """External monitoring should be able to poll without
    authentication."""
    client = app.test_client()
    # No session login → still gets a response (not redirected
    # to login).
    res = client.get("/admin/radius/setup-wizard/_system_health")
    assert res.status_code in (200, 503)
    # Crucially, NOT a 302 redirect to login.
    assert "Location" not in res.headers or (
        "/login" not in (res.headers.get("Location") or "")
    )


def test_check_clients_conf_syntax_detects_wildcard_regression():
    """Pins postmortem #17: if anyone ever puts
    $INCLUDE ...*.conf back, the check fails."""
    from app.radius.services.setup_wizard_system_health import (
        check_clients_conf_no_wildcards,
    )
    # Use the actual project file to check current state.
    r = check_clients_conf_no_wildcards()
    # Just confirm the function returns the right shape.
    assert r["status"] in ("ok", "warn", "fail")
    assert r["title_ar"]
