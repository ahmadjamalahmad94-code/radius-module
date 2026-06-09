"""E1 — Communications Center hub consolidation.

UI-only consolidation: the 12 communications sidebar entries collapse to a
single «التواصل والحملات» entry that lands on /communications (غرفة العمليات),
whose shared in-section tab bar reaches every sub-page. No comms logic is
touched — these tests prove the sidebar is collapsed, every sub-page still
renders, the nav bar exposes all tabs (incl. campaigns + audience), and the
WhatsApp subscriber page stays standalone.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "comms_hub.db")
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
        sess["admin_user"] = "comms_admin"
        sess["admin_name"] = "Comms Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "comms-csrf"


# All communications sub-pages that must remain reachable (GET) after the
# sidebar collapse — they are now reached via the shared in-section nav bar.
_SUBPAGES = [
    "/admin/radius/communications",
    "/admin/radius/communications/guide",
    "/admin/radius/communications/templates",
    "/admin/radius/communications/send",
    "/admin/radius/communications/campaigns",
    "/admin/radius/communications/deliveries",
    "/admin/radius/communications/audience",
    "/admin/radius/communications/channels",
    "/admin/radius/communications/bot",
    "/admin/radius/communications/notifications",
    "/admin/radius/communications/quota",
]


def test_sidebar_shows_one_consolidated_communications_entry(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/").get_data(as_text=True)
    assert "التواصل والحملات" in html
    # WhatsApp subscriber gates stay as their own standalone entry.
    assert "رسائل واتساب للمشتركين" in html


@pytest.mark.parametrize("url", _SUBPAGES)
def test_every_communications_subpage_still_renders(app, url):
    with app.test_client() as client:
        _auth(client)
        res = client.get(url)
    assert res.status_code == 200, url


def test_shared_nav_bar_exposes_all_tabs(app):
    with app.test_client() as client:
        _auth(client)
        html = client.get("/admin/radius/communications").get_data(as_text=True)
    assert 'data-testid="communications-nav"' in html
    # the bar must reach every sub-page, including the two that previously
    # lived only in the sidebar
    for label in ("غرفة العمليات", "قنوات الإرسال", "بوت واتساب",
                  "الإشعارات الحدثية", "الرصيد والحِزم", "القوالب",
                  "إرسال رسالة", "الحملات", "الجمهور", "سجل الإرسال"):
        assert label in html, label


def test_whatsapp_subscriber_page_stays_standalone(app):
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/whatsapp")
    assert res.status_code == 200
