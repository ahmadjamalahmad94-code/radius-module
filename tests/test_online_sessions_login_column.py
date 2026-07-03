"""FIX 4 regression: the /online sessions table splits «المستخدم» into two
columns — «الاسم» (display name) and «اسم الدخول» (login username).

Owner request: the login «25» must be its OWN column, distinct from the name
«ابو العبد» — not stacked under it. The username must still link where it
already linked (to the card/subscriber page).

We render the real template (radius/sessions_list.html) with one OnlineSession
so we assert the header split AND the row-cell split, keeping design-system
styling (.hr-entity-link).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_fix4_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _render(app):
    from flask import render_template
    from app.radius.core.types import OnlineSession
    item = OnlineSession(
        username="25",
        session_id="sess-1",
        nas_id="nas-1",
        nas_address="10.0.0.1",
        framed_ip="10.10.0.9",
        mac_address="AA:BB:CC:DD:EE:FF",
        started_at=datetime(2026, 7, 3, 15, 25, 0),
        last_update_at=datetime(2026, 7, 3, 16, 0, 0),
        full_name="ابو العبد",
        plan_name="امواج البحر",
        user_type="subscriber",
    )
    with app.test_request_context("/admin/radius/online"):
        return render_template(
            "radius/sessions_list.html",
            items=[item],
            settings={},
            error=None,
            filter_type="",
            nas_options=[], nas_name_by_ip={}, plan_options=[],
            selected_nas="", selected_plan="", selected_speed="",
            selected_group_id=None, group_options=[],
            device_by_mac={}, called_station_by_session={},
            temp_speed_state_by_username={},
            router_unreachable=False, unreachable_routers=[], reach_by_ip={},
            now=datetime(2026, 7, 3, 16, 0, 0),
        )


def test_two_distinct_column_headers(app):
    with app.app_context():
        html = _render(app)
    # Both new headers present…
    assert 'data-col="name"' in html
    assert 'data-col="login"' in html
    assert "اسم الدخول" in html
    # …and the login column has its own <th>.
    assert '<th data-col="login">' in html


def test_name_and_login_are_separate_cells(app):
    with app.app_context():
        html = _render(app)
    # The display name renders in the name cell.
    assert "ابو العبد" in html
    # The login renders in its own login cell (data-col="login") as a link.
    import re
    login_cell = re.search(
        r'<td data-col="login">(.*?)</td>', html, re.DOTALL)
    assert login_cell is not None, "login cell missing"
    body = login_cell.group(1)
    assert "25" in body
    assert "hr-entity-link" in body      # still a link, design-system styled
    assert "href=" in body               # username links where it used to


def test_login_not_stacked_under_name(app):
    with app.app_context():
        html = _render(app)
    import re
    # The name cell must NOT contain the old stacked username sub-line.
    name_cell = re.search(r'<td data-col="name">(.*?)</td>', html, re.DOTALL)
    assert name_cell is not None
    assert "hub-text-mute mono" not in name_cell.group(1), \
        "username must not be stacked under the name anymore"
