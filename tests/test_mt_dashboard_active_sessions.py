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


def test_empty_message_hidden_attribute_wins_over_display_flex(app, client):
    """Visual root cause of «counter=1 while the list says لا جلسات»: the
    card's .mt-empty carries an author display:flex rule, which overrides the
    UA's [hidden]{display:none} — so the «لا جلسات» message stayed visible
    ABOVE a table that actually had session rows, making the honest counter
    look contradicted. The template must carry a [hidden] kill-rule so the
    hidden attribute (set by both the server render and the JS) always wins."""
    _seed_router(app)
    _login(client)
    html = client.get("/admin/radius/mt/38/dashboard").get_data(as_text=True)
    assert ".mt-empty[hidden]{display:none !important}" in html


def test_server_render_with_live_session_hides_empty_message(app, client):
    """With a genuinely live session, the server must render the empty message
    WITH the hidden attribute and the table WITHOUT it (the CSS kill-rule then
    guarantees the message is actually invisible)."""
    import re
    _seed_router(app)
    _insert_session(app, "realuser", start=_now(), updated=_now())
    _login(client)
    html = client.get("/admin/radius/mt/38/dashboard").get_data(as_text=True)
    # empty <p> carries hidden; the table does not.
    empty_tag = re.search(r"<p[^>]*data-mt-active-users-empty[^>]*>", html)
    table_tag = re.search(r"<table[^>]*data-mt-active-users-table[^>]*>", html)
    assert empty_tag and "hidden" in empty_tag.group(0)
    assert table_tag and "hidden" not in table_tag.group(0)
    assert "realuser" in html


# ───────────── «أخرى» counter: the visible breakdown must sum to total ───────
# Live regression (screenshot-confirmed): 2 «other»-type sessions showed
# برودباند=0, بوابة الدخول=0 while إجمالي=2 — the total==list invariant held,
# but «other» sessions counted in the total with no visible category, so the
# two shown counters looked inconsistent. The card now carries a third «أخرى»
# pill: hotspot + ppp + other must VISIBLY equal the total.

def test_other_sessions_endpoint_breakdown_sums_to_total(app, client):
    """2 sessions that classify as «other» (unrecognized porttype):
    other=2, hotspot=0, ppp=0, and the three categories sum to count==len."""
    _seed_router(app)
    # nasporttype «Async» لا يطابق أيّ توكن في _normalize_session_type
    # (ethernet→hotspot هنا!) فيُصنَّف «other» — كالجلسات في بلاغ المالك.
    _insert_session(app, "3172911", start=_now(), updated=_now(),
                    ptype="Async")
    _insert_session(app, "3172912", start=_now(), updated=_now(),
                    ptype="Async")
    data = _fetch_active(app, client)
    assert data["count"] == len(data["sessions"]) == 2
    assert data["hotspot"] == 0
    assert data["ppp"] == 0
    assert data["other"] == 2
    assert data["hotspot"] + data["ppp"] + data["other"] == data["count"]
    assert all(s["type"] == "other" for s in data["sessions"])


def test_card_renders_other_pill_with_server_value(app, client):
    """The dashboard card must carry the third «أخرى» counter, server-rendered
    from the same live payload (2 for two other-type sessions), alongside
    hotspot=0 and ppp=0 — so the visible breakdown accounts for the total."""
    import re
    _seed_router(app)
    # nasporttype «Async» لا يطابق أيّ توكن في _normalize_session_type
    # (ethernet→hotspot هنا!) فيُصنَّف «other» — كالجلسات في بلاغ المالك.
    _insert_session(app, "3172911", start=_now(), updated=_now(),
                    ptype="Async")
    _insert_session(app, "3172912", start=_now(), updated=_now(),
                    ptype="Async")
    _login(client)
    html = client.get("/admin/radius/mt/38/dashboard").get_data(as_text=True)

    def counter(marker: str) -> int:
        m = re.search(r"<strong[^>]*" + marker + r"[^>]*>\s*(\d+)\s*<", html)
        assert m, f"counter {marker} missing from the card"
        return int(m.group(1))

    total = counter("data-mt-active-users-total")
    hot = counter("data-mt-hotspot-count")
    ppp = counter("data-mt-ppp-count")
    other = counter("data-mt-other-count")
    assert (hot, ppp, other, total) == (0, 0, 2, 2)
    assert hot + ppp + other == total
    assert "أخرى" in html
