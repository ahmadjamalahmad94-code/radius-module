"""Subscribers KPI cards are clickable → each opens its own filtered view.

Owner request: clicking a KPI card (expiring / online / expired / active /
disabled / total) navigates to the matching filtered subscribers list.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def app():
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "kpi.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    return a


@pytest.fixture()
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "preview"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    return c


def test_kpi_cards_are_links_to_their_filters(client):
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    # The cards render as links now.
    assert "hub-kpi--link" in html
    # Each card points at its own filtered view.
    assert "status=enabled" in html          # فعّال
    assert "status=disabled" in html         # معطّل
    assert "status=expired" in html          # منتهي الاشتراك
    assert "online=1" in html                # متصل الآن
    assert "attention=expiring_3d" in html   # ينتهي خلال 3 أيام


def test_online_filter_renders_and_shows_removable_chip(client):
    res = client.get("/admin/radius/subscribers?online=1")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # The active-filter chip (green online variant) appears and is removable.
    assert "users-attention-chip--online" in html
    assert "إزالة الفلتر" in html


def test_online_filter_is_ignored_when_absent(client):
    # A plain load must NOT show the online chip.
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert "users-attention-chip--online" not in html
