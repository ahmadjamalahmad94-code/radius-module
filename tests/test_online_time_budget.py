# -*- coding: utf-8 -*-
"""«وقت اليوم» used/total badge on /online — thirds coloring, per-type total.

Owner spec: show «used / total» (bidi-safe Latin units, e.g. «1h / 3h») as a
colored pill — green under ⅓ consumed, amber under ⅔, red in the last third
(«ثلث المدة أخضر، ثلثين أصفر، آخر ثلث أحمر»); neutral gray with «/ ∞» when
there is no total (never divide by zero or fake one).

TOTAL source per type:
  * card       → the batch time budget via card_accounting.budget_seconds —
                 the exact source the card checker uses.
  * subscriber → the effective daily cap (subscriber override else plan
                 max_daily_minutes); else the total connection-time limit;
                 else unlimited.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_daytime_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _now() -> _dt.datetime:
    return _dt.datetime.utcnow()


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat() + "Z"


def _seed_card(c, username, *, time_value=0, time_unit="hours",
               by_seconds=True, plan_duration_min=0):
    now = _iso(_now())
    pid = c.execute(
        "INSERT INTO access_plans(tenant_id, name, service_type, "
        "duration_minutes, created_at) VALUES (1,?,?,?,?)",
        (f"plan-{username}", "Hotspot", plan_duration_min, now)).lastrowid
    bid = c.execute(
        "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
        "count_by_seconds, count_from_first_connect, time_value, time_unit, "
        "created_at) VALUES (1,?,?,?,?,?,?,?,?)",
        (f"b-{username}", pid, 1, 1 if by_seconds else 0, 0,
         time_value, time_unit, now)).lastrowid
    c.execute(
        "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
        "created_at) VALUES (1,?,?,?,?,?)", (bid, username, "pw", pid, now))


def _seed_subscriber(c, username, *, daily_min=0, enabled_flag=False):
    c.execute(
        "INSERT INTO subscribers(tenant_id, username, password, "
        "daily_connection_time_min, connection_time_limit_enabled, created_at) "
        "VALUES (1,?,?,?,?,?)",
        (username, "pw", daily_min, 1 if enabled_flag else 0, _iso(_now())))


def _seed_usage(c, username, *, seconds, sid=None, open_row=True):
    """One radacct row carrying `seconds` of accounted usage, live now."""
    now = _iso(_now())
    c.execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, acctstarttime, acctupdatetime, acctstoptime, "
        "acctsessiontime) VALUES (1,?,?,?,?,?,?,?,?)",
        (sid or f"s-{username}", f"u-{sid or username}", username, "10.10.0.2",
         now, now, None if open_row else now, int(seconds)))


class _S:
    def __init__(self, username):
        self.username = username


# ───────────────────── the three owner cases ─────────────────────────────

def test_card_1h_of_3h_budget_is_green(app):
    """Card with 1h used of a 3h budget → «1h / 3h», GREEN (< ⅓)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.online_time_budget import day_time_cells
        with transaction() as c:
            _seed_card(c, "c-green", time_value=3, time_unit="hours")
            _seed_usage(c, "c-green", seconds=3600)
        cells = day_time_cells(1, [_S("c-green")], card_view=True)
    cell = cells["c-green"]
    assert cell["used_txt"] == "1h" and cell["total_txt"] == "3h"
    assert cell["total_sec"] == 3 * 3600
    assert cell["bucket"] == "green"


def test_card_2h_of_3h_budget_is_red(app):
    """2h of 3h = ⅔ consumed → RED (the last third starts AT ⅔)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.online_time_budget import day_time_cells
        with transaction() as c:
            _seed_card(c, "c-red", time_value=3, time_unit="hours")
            _seed_usage(c, "c-red", seconds=2 * 3600)
        cells = day_time_cells(1, [_S("c-red")], card_view=True)
    cell = cells["c-red"]
    assert cell["used_txt"] == "2h" and cell["total_txt"] == "3h"
    assert cell["bucket"] == "red"


def test_subscriber_without_daily_cap_is_neutral(app):
    """Subscriber with no cap → neutral bucket, no total (renders «/ ∞»)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.online_time_budget import day_time_cells
        with transaction() as c:
            _seed_subscriber(c, "s-free")
            _seed_usage(c, "s-free", seconds=1800)
        cells = day_time_cells(1, [_S("s-free")], card_view=False)
    cell = cells["s-free"]
    assert cell["total_sec"] is None and cell["total_txt"] == ""
    assert cell["bucket"] == "neutral"
    assert cell["used_sec"] == 1800


# ───────────────────── boundaries & robustness ────────────────────────────

def test_thirds_boundaries_and_overuse_cap():
    """Owner's anchors fix the edges: exactly ⅓ is still GREEN (1h/3h) and
    exactly ⅔ is already RED (2h/3h); amber is strictly between."""
    from app.radius.services.online_time_budget import thirds_bucket
    assert thirds_bucket(0, 9000) == "green"
    assert thirds_bucket(3000, 9000) == "green"        # exactly ⅓ → green
    assert thirds_bucket(3001, 9000) == "amber"        # just over ⅓
    assert thirds_bucket(5999, 9000) == "amber"        # just under ⅔
    assert thirds_bucket(6000, 9000) == "red"          # exactly ⅔ → red
    assert thirds_bucket(20000, 9000) == "red"         # used > total → capped
    assert thirds_bucket(500, 0) == "neutral"          # zero total → neutral
    assert thirds_bucket(500, None) == "neutral"       # missing total


def test_subscriber_daily_cap_colors_by_todays_usage(app):
    """Subscriber with a 3h daily cap and 2h used today → red; the cap is the
    effective enforcement daily limit (subscriber override)."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.online_time_budget import day_time_cells
        with transaction() as c:
            _seed_subscriber(c, "s-cap", daily_min=180, enabled_flag=True)
            _seed_usage(c, "s-cap", seconds=2 * 3600)
        cells = day_time_cells(1, [_S("s-cap")], card_view=False)
    cell = cells["s-cap"]
    assert cell["total_sec"] == 180 * 60
    assert cell["bucket"] == "red"


def test_card_without_any_time_budget_is_neutral(app):
    """A card with no batch time fields and no plan duration → unlimited by
    time: neutral, total None, used = its accounted seconds."""
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.online_time_budget import day_time_cells
        with transaction() as c:
            _seed_card(c, "c-open", time_value=0, plan_duration_min=0)
            _seed_usage(c, "c-open", seconds=900)
        cells = day_time_cells(1, [_S("c-open")], card_view=True)
    cell = cells["c-open"]
    assert cell["total_sec"] is None
    assert cell["bucket"] == "neutral"
    assert cell["used_sec"] == 900


# ───────────────────── route-level render (both tabs) ─────────────────────

def _login(app):
    from app.radius.db.repos import admins_repo
    client = app.test_client()
    with app.app_context():
        u = f"dt_{uuid4().hex[:10]}"
        admins_repo.create_admin(username=u, password="dt-pass",
                                 full_name="DT Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "dt-pass"})
    assert res.status_code in {302, 303}
    return client


def _pill_for(body: str, username: str) -> str:
    row = re.search(
        r'data-username="%s".*?data-col="daily_used".*?(?:<span class="du-pill ([a-z\- ]*du-[a-z]+)"|—)'
        % re.escape(username), body, re.S)
    assert row, f"daily_used cell for {username} not found"
    return row.group(1) or ""


def test_online_cards_tab_renders_thirds_pill(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_card(c, "3172911", time_value=3, time_unit="hours")
            _seed_usage(c, "3172911", seconds=3600)
    body = _login(app).get(
        "/admin/radius/online?type=card").get_data(as_text=True)
    assert "3172911" in body
    assert "du-green" in _pill_for(body, "3172911")
    assert "1h / 3h" in body


def test_online_subscribers_tab_renders_neutral_for_uncapped(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_subscriber(c, "s-plain")
            _seed_usage(c, "s-plain", seconds=1200)
    body = _login(app).get("/admin/radius/online").get_data(as_text=True)
    assert "s-plain" in body
    assert "du-neutral" in _pill_for(body, "s-plain")
    assert "∞" in body
