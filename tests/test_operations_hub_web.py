"""E3 — Operations Center hub consolidation.

UI-only: the operations landing (/operations) and speed-control
(/operations/speed-control) now share a single in-section pill nav for a
consistent hub feel, matching the comms/events hubs. No operations logic
or dry-run safety is touched.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "ops_hub.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations

        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "ops_admin"
        sess["admin_name"] = "Ops Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "ops-csrf"


@pytest.mark.parametrize("url", [
    "/admin/radius/operations",
    "/admin/radius/operations/speed-control",
])
def test_operations_pages_render_with_shared_nav(app, url):
    with app.test_client() as client:
        _auth(client)
        res = client.get(url)
    assert res.status_code == 200, url
    html = res.get_data(as_text=True)
    assert 'data-testid="operations-nav"' in html
    assert "العمليات" in html
    assert "التحكم بالسرعة" in html


def test_speed_control_post_endpoint_stays(app):
    rules = {(r.rule, tuple(sorted(r.methods))) for r in app.url_map.iter_rules()}
    has_post = any(
        rule == "/admin/radius/operations/speed-control" and "POST" in methods
        for rule, methods in rules
    )
    assert has_post
