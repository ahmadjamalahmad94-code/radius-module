from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests
from app.radius.services.dashboard_reports import DashboardReportsService


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "dashboard_reports_archives.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "reports_admin"
        sess["admin_name"] = "Reports Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "reports-csrf"


def _revenue(amount_minor: int, profit_minor: int, created_at: str) -> None:
    db().execute(
        """
        INSERT INTO revenue_records(
            tenant_id, source_type, source_id, original_price_minor,
            retail_price_minor, wholesale_cost_minor, collected_amount_minor,
            net_profit_minor, company_share_minor, currency, status,
            metadata_json, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            1,
            "manual_sale",
            10,
            amount_minor,
            amount_minor,
            amount_minor - profit_minor,
            amount_minor,
            profit_minor,
            profit_minor,
            "JOD",
            "posted",
            "{}",
            created_at,
        ),
    )


def _card_sale(period: str = "2026-05-10T12:00:00Z") -> None:
    plan_id = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, created_at, updated_at)
        VALUES(?,?,?,?)
        """,
        (1, "Report Card Plan", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    ).lastrowid
    batch_id = db().execute(
        """
        INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, generated, created_at)
        VALUES(?,?,?,?,?,?)
        """,
        (1, "REP-BATCH", plan_id, 1, 1, "2026-01-01T00:00:00Z"),
    ).lastrowid
    db().execute(
        """
        INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, used, first_used_at, created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (1, batch_id, "report-card-1", "masked", plan_id, 1, period, "2026-01-01T00:00:00Z"),
    )


def test_dashboard_summary_endpoint_returns_executive_metrics(app):
    with app.app_context():
        _revenue(1500, 500, "2026-05-10T12:00:00Z")
        _card_sale()
    with app.test_client() as client:
        _auth_session(client)
        res = client.get("/admin/radius/reports/summary.json?date_from=2026-05-01&date_to=2026-05-31")

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["status"] == "ok"
    assert payload["summary"]["finance"]["revenue"] == 15.0
    assert payload["summary"]["finance"]["margin_year"] >= 5.0
    assert payload["summary"]["cards"]["sold_month"] >= 1


def test_date_range_filters_revenue_and_margin(app):
    with app.app_context():
        service = DashboardReportsService(tenant_id=1)
        _revenue(1000, 400, "2026-05-10T12:00:00Z")
        _revenue(7000, 2000, "2026-04-10T12:00:00Z")
        summary = service.executive_summary(date_from="2026-05-01", date_to="2026-05-31")

    assert summary["finance"]["revenue"] == 10.0
    assert summary["finance"]["margin_today"] == 0.0


def test_drilldown_url_generation(app):
    with app.app_context():
        links = DashboardReportsService(tenant_id=1).drilldown_links()

    assert links["subscribers_active"] == "/admin/radius/users?status=enabled"
    assert links["financial_reports"] == "/admin/radius/reports/financial"
    assert links["audit_reports"] == "/admin/radius/events"


def test_archive_snapshot_creation_is_immutable(app):
    with app.app_context():
        service = DashboardReportsService(tenant_id=1)
        _revenue(2500, 800, "2026-05-10T12:00:00Z")
        first = service.create_archive_snapshot(
            archive_type="yearly",
            period="2026",
            report_type="financial",
            actor="qa",
        )
        _revenue(9000, 1000, "2026-05-11T12:00:00Z")
        second = service.create_archive_snapshot(
            archive_type="yearly",
            period="2026",
            report_type="financial",
            actor="qa",
        )

    assert first["created"] is True
    assert second["created"] is False
    assert first["id"] == second["id"]
    assert second["summary"]["finance"]["revenue"] == first["summary"]["finance"]["revenue"]
    assert second["summary"]["finance"]["revenue"] == 25.0


def test_report_routes_render_and_archive_post_preserves_financial_history(app):
    with app.app_context():
        _revenue(3000, 1200, "2026-05-10T12:00:00Z")
    with app.test_client() as client:
        _auth_session(client)
        home = client.get("/admin/radius/reports")
        financial = client.get("/admin/radius/reports/financial?date_from=2026-05-01&date_to=2026-05-31")
        cards = client.get("/admin/radius/reports/cards")
        distributors = client.get("/admin/radius/reports/distributors")
        archive = client.post(
            "/admin/radius/reports/archive/create",
            data={
                "_csrf_token": "reports-csrf",
                "archive_type": "yearly",
                "period": "2026",
                "report_type": "financial",
            },
            follow_redirects=True,
        )
        dashboard = client.get("/admin/radius/dashboard")

    assert home.status_code == 200
    assert "executive-dashboard-summary" in home.get_data(as_text=True)
    assert financial.status_code == 200
    assert "report-detail-table" in financial.get_data(as_text=True)
    assert cards.status_code == 200
    assert distributors.status_code == 200
    assert archive.status_code == 200
    assert "archive-snapshots-table" in archive.get_data(as_text=True)
    assert dashboard.status_code == 200
