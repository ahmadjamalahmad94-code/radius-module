"""Network section consolidation (N1 wizard cleanup, N2 router-mgmt nav).

UI-only: the five duplicate «setup wizard» sidebar entries collapse to two
clear paths (quick add + advanced), and the router-management cluster
(operations / topology / problems / diagnostics) shares one in-section nav
behind a single sidebar entry. All legacy routes stay registered so old
bookmarks keep working.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "network_section.db")
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
        sess["admin_user"] = "net_admin"
        sess["admin_name"] = "Net Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "net-csrf"


# ── N1: wizard cleanup ──
def test_sidebar_shows_two_clear_router_add_paths(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)
    assert "إضافة راوتر (سريع)" in html
    assert "إعداد راوتر متقدم" in html
    # the confusing duplicates / superseded labels are gone from the sidebar
    assert "معالج إضافة راوتر" not in html
    assert "معالج الإعداد" not in html
    assert "أسطول الراوترات" not in html
    assert "عرض الإعداد الهندسي" not in html


def test_legacy_wizard_routes_stay_registered(app):
    rules = {r.rule for r in app.url_map.iter_rules()}
    # superseded pages keep their routes alive for old bookmarks
    for rule in (
        "/admin/radius/setup-wizard-v2",
        "/admin/radius/setup-wizard/fleet",
        "/admin/radius/setup-wizard",
        "/admin/radius/setup-wizard-v3",
        "/admin/radius/mt/setup",
    ):
        assert rule in rules, rule
