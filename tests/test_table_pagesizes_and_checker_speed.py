# -*- coding: utf-8 -*-
"""Guards for two owner requests:

  #4 page-size dropdown = exactly 10/25/50/100/200/500 + «الكل» (show all),
     on the subscribers list and the online-sessions list, with the "all"
     option supported by dashboard_table.js.
  #2 the Card Checker shows the card's speed (سرعة البطاقة).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from uuid import uuid4

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ── #4 page sizes ─────────────────────────────────────────────────────

def test_dashboard_table_supports_all_and_new_size_list():
    js = _read("app", "static", "js", "dashboard_table.js")
    # default list is the trimmed set + all
    assert "10,25,50,100,200,500,all" in js
    # "all" sentinel handled (label + full-page render)
    assert 'var ALL = "all"' in js
    assert "الكل" in js
    assert "pageSizeNum" in js


@pytest.mark.parametrize("tmpl,key", [
    ("users_list.html", "users-list"),
    ("sessions_list.html", "online-sessions"),
])
def test_tables_declare_the_exact_size_list(tmpl, key):
    src = _read("app", "templates", "radius", tmpl)
    m = re.search(r'data-page-sizes="([^"]+)"[^>]*data-persist-key="' + key + '"', src) \
        or re.search(r'data-persist-key="' + key + '"[^>]*data-page-sizes="([^"]+)"', src)
    assert m, f"{tmpl}: no data-page-sizes next to persist-key {key}"
    assert m.group(1) == "10,25,50,100,200,500,all", m.group(1)
    # the trimmed-out values must be gone
    assert '"10,20,50,100,200,500,1000"' not in src


# ── #2 checker speed ──────────────────────────────────────────────────

def test_checker_template_has_speed_macro_and_metric():
    src = _read("app", "templates", "radius", "cards_checker_v2.html")
    assert "{% macro spd(kbps)" in src
    assert "سرعة البطاقة" in src
    assert "spd(p.speed_down_kbps)" in src


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_spd_")
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


def test_checker_page_renders_card_speed(app):
    import datetime as _dt
    from app.radius.db.repos import admins_repo
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _dt.datetime.utcnow().isoformat()
        with transaction() as c:
            pid = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, "
                "speed_down_kbps, speed_up_kbps, created_at) VALUES (1,?,?,?,?,?)",
                ("4 ميجا فري لانسر", "Hotspot", 7500, 7500, now)).lastrowid
            bid = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, "
                "created_at) VALUES (1,?,?,?,?)", ("spd-b", pid, 1, now)).lastrowid
            c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, "
                "plan_id, created_at) VALUES (1,?,?,?,?,?)",
                (bid, "3172911", "pw", pid, now))
    client = app.test_client()
    with app.app_context():
        u = f"spd_{uuid4().hex[:10]}"
        admins_repo.create_admin(username=u, password="spd-pass",
                                 full_name="Speed Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "spd-pass"})
    assert res.status_code in {302, 303}
    res = client.get("/admin/radius/cards/checker?query=3172911")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "سرعة البطاقة" in body
    assert "7.5 Mbps" in body            # 7500 kbps → 7.5 Mbps
    assert 'dir="ltr"' in body           # LTR-isolated pair
