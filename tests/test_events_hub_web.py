"""E2 — Events & Risk Center hub consolidation.

UI-only: the four events sidebar entries (events / risk / security /
investigations) collapse into one «الأحداث والمخاطر» entry that lands on
/events, whose shared in-section tab bar reaches every sub-page. Event
detail pages stay standalone. No events/risk logic is touched.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "events_hub.db")
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
        sess["admin_user"] = "events_admin"
        sess["admin_name"] = "Events Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "events-csrf"


_SUBPAGES = [
    "/admin/radius/events",
    "/admin/radius/events/risk",
    "/admin/radius/events/security",
    "/admin/radius/events/investigations",
]


def test_sidebar_collapses_events_into_one_entry(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)
    assert "الأحداث والمخاطر" in html
    # the standalone sub-labels no longer sit in the sidebar (they live in
    # the in-section nav bar now)
    assert "تقييم المخاطر" not in html
    assert "التحقيقات" not in html


@pytest.mark.parametrize("url", _SUBPAGES)
def test_every_events_subpage_still_renders(app, url):
    with app.test_client() as client:
        _auth(client)
        res = client.get(url)
    assert res.status_code == 200, url


def test_shared_nav_bar_exposes_all_event_tabs(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/events").get_data(as_text=True)
    assert 'data-testid="events-nav"' in html
    for label in ("الأحداث", "تقييم المخاطر", "الأمان", "التحقيقات"):
        assert label in html, label


def test_event_detail_route_stays_standalone(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/events/<int:event_id>" in rules
