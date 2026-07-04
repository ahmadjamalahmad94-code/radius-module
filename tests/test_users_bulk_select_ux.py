"""Bulk-selection UX on /subscribers (owner report, 2026-07):

1. Bulk status-change confirm showed 100+ usernames as one unbroken wall
   («سلطة كأنهم جريدة») pushing إلغاء/متابعة off-screen → the confirm message
   now leads with the COUNT and caps the sample at 15 names + «… و N آخرين»,
   and the shared confirm modal (#cfmModal) scrolls its message internally
   (max-height box + overflow:auto) so the buttons are always reachable.
2. «تحديد الكل» selected ALL table rows including pagination-hidden ones →
   now selects only the VISIBLE rows (current page + live-search filter).
3. Page-size options extended to 200/500/1000.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_bulkux_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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


def _page(app) -> str:
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"own_{uuid4().hex[:8]}"
        admins_repo.create_admin(username=u, password="p",
                                 full_name="Owner", is_super_admin=True)
        # Seed one subscriber so the data table (not the empty state) renders.
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, "
                "user_type, status, created_at) "
                "VALUES (1, 'bulk-ux-1', 'pw', 'subscriber', 'enabled', "
                "datetime('now'))")
    client.post("/admin/radius/login", data={"username": u, "password": "p"})
    resp = client.get("/admin/radius/subscribers")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_page_sizes_include_200_500_1000(app):
    html = _page(app)
    assert 'data-page-sizes="10,20,50,100,200,500,1000"' in html


def test_bulk_confirm_message_is_capped_not_a_wall(app):
    html = _page(app)
    # The cap constant + the «… و N آخرين» truncation are present in the
    # page's bulk-confirm builder — no more full-list newspaper.
    assert "MAX_SHOWN" in html
    assert "آخرين" in html


def test_select_all_targets_visible_rows_only(app):
    html = _page(app)
    # The check-all handler routes through visibleChecks() (pagination/filter
    # aware) instead of blanket-checking every row checkbox.
    assert "function visibleChecks()" in html
    assert "visibleChecks().forEach" in html


def test_confirm_modal_scrolls_internally(app):
    html = _page(app)
    # Modal box is viewport-capped and the message area scrolls + preserves
    # the count line break; footer buttons always visible.
    assert "max-height:calc(100vh - 48px)" in html
    assert "white-space:pre-line" in html
