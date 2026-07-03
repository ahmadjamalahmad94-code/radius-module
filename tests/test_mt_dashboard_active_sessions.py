# -*- coding: utf-8 -*-
"""Router-dashboard «المتصلون الآن» card — single source of truth.

The card at /admin/radius/mt/<id>/dashboard used to draw its three counters
(برودباند / بوابة الدخول / إجمالي) from a *different* source than its session
list: the server-rendered radacct baseline AND a client RouterOS-API poll
(hotspot/active + ppp/active) that overwrote counters and rows through separate
paths that could disagree. The reported symptom on router 38 (the SSTP «test»
router): counters read hotspot=1/total=1 while the list said «لا جلسات نشطة».

The rebuild routes the card through ONE endpoint —
/admin/radius/mt/<id>/active-sessions — that returns
live_sessions.active_sessions_for_router(...), the exact set the list renders,
with _is_live() excluding phantom/stale open rows. This mirrors
test_live_panel_phantom_reconcile but for the dashboard-card HTTP path:
the endpoint's counters must always equal its own session list.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mt_active_")
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


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"mtact_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="act-pass", full_name="Active Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "act-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


ROUTER_IP = "203.0.113.38"


def _seed_router(app, *, nas_id: int = 38) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = _dt.datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, 'test-sstp', ?, 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'sstp')""",
                (nas_id, ROUTER_IP, now),
            )


def _now() -> str:
    return _dt.datetime.utcnow().isoformat() + "Z"


def _insert_session(app, username, *, start, updated, ip=ROUTER_IP,
                    ptype="ethernet"):
    with app.app_context():
        from app.radius.db.connection import db
        db().execute(
            "INSERT INTO radacct(tenant_id, acctsessionid, username, "
            "nasipaddress, nasporttype, framedprotocol, framedipaddress, "
            "acctstarttime, acctupdatetime, acctstoptime, acctsessiontime) "
            "VALUES(1,?,?,?,?,?,?,?,?,?,?)",
            (username + "-s", username, ip, ptype, "", "10.5.5.5",
             start, updated, None, 0),
        )


def _fetch_active(app, client, nas_id: int = 38):
    _login(client)
    res = client.get(f"/admin/radius/mt/{nas_id}/active-sessions")
    assert res.status_code == 200, res.status_code
    return res.get_json()


def test_endpoint_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/admin/radius/mt/<int:nas_id>/active-sessions" in rules


def test_phantom_only_counters_and_list_both_zero(app, client):
    """Router 38's exact reported state: a phantom open row (NULL timestamps)
    must yield counters 0/0/0 AND an empty list — never a lone counter=1."""
    _seed_router(app)
    _insert_session(app, "ghost", start=None, updated=None)   # phantom
    data = _fetch_active(app, client)
    assert data["ok"] is True
    assert data["count"] == 0
    assert data["hotspot"] == 0
    assert data["ppp"] == 0
    assert data["other"] == 0
    assert data["sessions"] == []
    # The invariant the owner cares about: total == list length.
    assert data["count"] == len(data["sessions"])


def test_counter_total_always_equals_list_length(app, client):
    """The core invariant, with a mix of live + phantom + stale rows:
    count == len(sessions) AND hotspot + ppp + other == count."""
    _seed_router(app)
    _insert_session(app, "live1", start=_now(), updated=_now())
    _insert_session(app, "live2", start=_now(), updated=_now())
    _insert_session(app, "phantom", start=None, updated=None)          # excluded
    stale = (_dt.datetime.utcnow() - _dt.timedelta(minutes=600)).isoformat() + "Z"
    _insert_session(app, "stale", start=stale, updated=stale)          # excluded
    data = _fetch_active(app, client)
    assert data["count"] == len(data["sessions"]) == 2
    assert data["hotspot"] + data["ppp"] + data["other"] == data["count"]
    assert {s["username"] for s in data["sessions"]} == {"live1", "live2"}


def test_unknown_router_returns_404(app, client):
    _seed_router(app)
    _login(client)
    res = client.get("/admin/radius/mt/9999/active-sessions")
    assert res.status_code == 404


def test_dashboard_card_wires_single_source_url(app, client):
    """The card must carry the endpoint URL (single-source wiring) and must NOT
    still be reading the removed radacct baseline attribute."""
    _seed_router(app)
    _login(client)
    html = client.get("/admin/radius/mt/38/dashboard").get_data(as_text=True)
    assert "data-mt-active-sessions-url" in html
    assert "/admin/radius/mt/38/active-sessions" in html
    assert "data-mt-live-baseline" not in html
