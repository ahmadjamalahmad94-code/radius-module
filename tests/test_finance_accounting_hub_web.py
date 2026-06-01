"""Tests for the unified accounting ledger and reports hub."""
from __future__ import annotations

import os

import pytest


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


def _run_pending_migrations() -> None:
    from app.radius.db.migrations_runner import run_pending_migrations

    run_pending_migrations()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "accounting_hub.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        _run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "accounting_admin"
        sess["admin_name"] = "Accounting Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "accounting-csrf"


_HUB = "/admin/radius/finance/accounting"


def _counts(app):
    with app.app_context():
        from app.radius.db.connection import db

        conn = db()
        return {
            "ledger": conn.execute(
                "SELECT COUNT(*) AS c FROM accounting_ledger_entries"
            ).fetchone()["c"],
            "snapshots": conn.execute(
                "SELECT COUNT(*) AS c FROM financial_report_snapshots"
            ).fetchone()["c"],
        }


def test_accounting_hub_route_renders_ledger_and_reports(app):
    with app.test_client() as client:
        _auth(client)
        ledger = client.get(_HUB)
        reports = client.get(f"{_HUB}?tab=reports&type=subscriber_payments")

    assert ledger.status_code == 200
    assert reports.status_code == 200
    ledger_html = ledger.get_data(as_text=True)
    reports_html = reports.get_data(as_text=True)
    assert "السجل والتقارير المحاسبية" in ledger_html
    assert "السجل المالي" in ledger_html
    assert "التقارير المالية" in reports_html
    assert "CSV" in reports_html
    assert "PDF" in reports_html


def test_legacy_ledger_and_report_urls_redirect_to_hub(app):
    with app.test_client() as client:
        _auth(client)
        ledger = client.get(
            "/admin/radius/finance/ledger?entry_type=payment&subscriber_id=7",
            follow_redirects=False,
        )
        reports = client.get(
            "/admin/radius/finance/reports?type=loans",
            follow_redirects=False,
        )

    assert ledger.status_code in {301, 302, 303}
    assert "/finance/accounting" in ledger.headers.get("Location", "")
    assert "entry_type=payment" in ledger.headers.get("Location", "")
    assert "subscriber_id=7" in ledger.headers.get("Location", "")
    assert reports.status_code in {301, 302, 303}
    assert "/finance/accounting" in reports.headers.get("Location", "")
    assert "tab=reports" in reports.headers.get("Location", "")
    assert "type=loans" in reports.headers.get("Location", "")


def test_accounting_hub_link_appears_in_sidebar(app):
    with app.test_client() as client:
        _auth(client)
        response = client.get("/admin/radius/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/finance/accounting" in html
    assert "السجل والتقارير المحاسبية" in html


def test_accounting_hub_get_writes_nothing(app):
    before = _counts(app)
    with app.test_client() as client:
        _auth(client)
        client.get(_HUB)
        client.get(f"{_HUB}?tab=reports&type=daily")
    assert _counts(app) == before
