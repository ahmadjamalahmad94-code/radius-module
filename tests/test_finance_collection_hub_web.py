"""Tests for the unified collection and payments hub."""
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
    db_file = os.path.join(tmp_path, "collection_hub.db")
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
        sess["admin_user"] = "collection_admin"
        sess["admin_name"] = "Collection Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "collection-csrf"


_HUB = "/admin/radius/finance/collection"


def test_collection_hub_route_renders_tabs_and_modal(app):
    with app.test_client() as client:
        _auth(client)
        response = client.get(_HUB)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "التحصيل والمدفوعات" in html
    assert "طلبات الدفع" in html
    assert "قائمة المراجعة" in html
    assert "المطابقة" in html
    assert 'data-modal="settings"' in html
    assert 'action="/admin/radius/payments/settings"' in html


def test_collection_hub_review_and_reconciliation_tabs_render(app):
    with app.test_client() as client:
        _auth(client)
        review = client.get(f"{_HUB}?tab=review")
        reconciliation = client.get(f"{_HUB}?tab=reconciliation")

    assert review.status_code == 200
    assert reconciliation.status_code == 200
    assert "قائمة المراجعة" in review.get_data(as_text=True)
    assert "المطابقة والتدقيق" in reconciliation.get_data(as_text=True)


def test_collection_hub_link_appears_in_sidebar(app):
    with app.test_client() as client:
        _auth(client)
        response = client.get("/admin/radius/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "/finance/collection" in html
    assert "التحصيل والمدفوعات" in html
    # the four old separate labels are gone (collapsed into one)
    assert "مراجعة الدفعات" not in html
    assert "مطابقة التحصيل" not in html


def test_settings_tab_auto_opens_modal(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get(f"{_HUB}?tab=settings").get_data(as_text=True)
    assert "sd.showModal" in html  # auto-open branch present


@pytest.mark.parametrize("old,expect", [
    ("/admin/radius/payments/requests", "tab=requests"),
    ("/admin/radius/payments/review-queue", "tab=review"),
    ("/admin/radius/payments/reconciliation", "tab=reconciliation"),
    ("/admin/radius/payments/settings", "tab=settings"),
])
def test_legacy_urls_redirect_to_hub(app, old, expect):
    with app.test_client() as client:
        _auth(client)
        res = client.get(old, follow_redirects=False)
    assert res.status_code in {301, 302}
    loc = res.headers.get("Location", "")
    assert "/finance/collection" in loc
    assert expect in loc


def test_requests_filter_preserved_in_redirect(app):
    with app.test_client() as client:
        _auth(client)
        res = client.get(
            "/admin/radius/payments/requests?status=paid", follow_redirects=False
        )
    assert "status=paid" in res.headers.get("Location", "")


def test_request_detail_and_actions_stay_standalone(app):
    """The detail page + approve/reject/apply-service POST endpoints
    must remain live (reached from the requests table), NOT collapsed."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/payments/requests/<int:request_id>" in rules
    assert "/admin/radius/payments/requests/<int:request_id>/approve" in rules
    assert "/admin/radius/payments/requests/<int:request_id>/reject" in rules
    assert (
        "/admin/radius/payments/requests/<int:request_id>/apply-service" in rules
    )


def test_settings_post_persists_via_original_endpoint(app):
    with app.test_client() as client:
        _auth(client)
        res = client.post("/admin/radius/payments/settings", data={
            "_csrf_token": "collection-csrf",
            "enabled": "1",
            "provider": "manual_wallet",
            "currency": "ILS",
            "wallet_number": "0599-123456",
            "wallet_owner_name": "Company",
            "confirmation_mode": "manual",
            "payment_request_ttl_minutes": "1440",
        }, follow_redirects=False)
    assert res.status_code in {301, 302}
    assert "tab=settings" in res.headers.get("Location", "")
    with app.app_context():
        from app.radius.db.repos.payments_repo import PaymentSettingsRepository
        s = PaymentSettingsRepository().get(1)
    assert s is not None
    assert s.wallet_number == "0599-123456"
    assert s.enabled is True


def test_hub_get_writes_nothing(app):
    with app.app_context():
        from app.radius.db.connection import db
        before = db().execute(
            "SELECT COUNT(*) c FROM payment_requests"
        ).fetchone()["c"]
    with app.test_client() as client:
        _auth(client)
        for tab in ("requests", "review", "reconciliation", "settings"):
            client.get(f"{_HUB}?tab={tab}")
    with app.app_context():
        from app.radius.db.connection import db
        after = db().execute(
            "SELECT COUNT(*) c FROM payment_requests"
        ).fetchone()["c"]
    assert after == before
