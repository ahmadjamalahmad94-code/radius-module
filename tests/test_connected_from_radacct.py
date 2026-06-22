# -*- coding: utf-8 -*-
"""«المتصلون الآن» + حالة الراوترات المتصلة من radacct (المصدر الموثوق).

يغطّي:
  • live_sessions: جلسات نشطة لكل راوتر بمطابقة nasipaddress IN
    (address, vpn_peer_address) — تعمل على IP عام أو نفق واير جارد؛ حالة
    خالية صريحة؛ استبعاد الزومبي خارج النافذة.
  • حالة «متصل» للراوتر من radacct (router_live / live_map).
  • Option C: radius_coa.find_all_nas_for_sessions يطابق IP النفق.
  • عرض الويب: لوحة الراوتر تُظهر الجلسات؛ مركز العمليات يُظهر عدّ المتصلين.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_conn_radacct_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "t.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        a = admins_repo.create_admin(username="op", password="op123456",
                                     full_name="op")
        created.config["_admin_id"] = int(getattr(a, "id", 1) or 1)
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _now():
    return _dt.datetime.utcnow().isoformat() + "Z"


def _ago(minutes):
    return (_dt.datetime.utcnow() - _dt.timedelta(minutes=minutes)).isoformat() + "Z"


def _nas(address, *, vpn_peer="", secret="s3cr3t", name="RB", coa_port=3799):
    from app.radius.db.connection import db
    cur = db().execute(
        "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
        "enabled, connection_mode, vpn_peer_address, coa_port, created_at, updated_at) "
        "VALUES(1,?,?,?,'mikrotik',1,?,?,?,?,?)",
        (name, address, secret, "vpn" if vpn_peer else "direct", vpn_peer,
         coa_port, _now(), _now()))
    return int(cur.lastrowid)


def _sess(username, ip, *, ptype="ethernet", proto="", stop=None, updated=None):
    from app.radius.db.connection import db
    db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
        "nasporttype, framedprotocol, framedipaddress, acctstarttime, "
        "acctupdatetime, acctstoptime, acctsessiontime) "
        "VALUES(1,?,?,?,?,?,?,?,?,?,?)",
        (username + "-s", username, ip, ptype, proto, "10.5.5.5",
         _now(), updated or _now(), stop, 600))


# ───────────────────────── live_sessions ─────────────────────────

def test_active_sessions_direct_ip(app):
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _sess("ahmad", "203.0.113.9", ptype="ethernet")
        _sess("sara", "203.0.113.9", ptype="wireless")
        r = ls.active_sessions_for_router(1, {"address": "203.0.113.9",
                                              "vpn_peer_address": ""})
        assert r["count"] == 2 and r["hotspot"] == 2 and r["ppp"] == 0
        assert {s["username"] for s in r["sessions"]} == {"ahmad", "sara"}


def test_active_sessions_match_via_tunnel_ip(app):
    """جوهر النفق: جلسة تَصل على IP النفق (vpn_peer_address) تُنسب للراوتر
    وإن كان عنوانه العام مختلفاً."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _sess("omar", "10.10.0.5", ptype="virtual", proto="PPP")  # PPPoE على النفق
        r = ls.active_sessions_for_router(
            1, {"address": "198.51.100.7", "vpn_peer_address": "10.10.0.5"})
        assert r["count"] == 1 and r["ppp"] == 1
        assert r["sessions"][0]["username"] == "omar"


def test_empty_state_when_no_sessions(app):
    with app.app_context():
        from app.radius.services import live_sessions as ls
        r = ls.active_sessions_for_router(1, {"address": "192.0.2.1",
                                              "vpn_peer_address": ""})
        assert r["count"] == 0 and r["sessions"] == []


def test_zombie_session_excluded_by_window(app):
    """جلسة مفتوحة لكن آخر تحديثها أقدم من النافذة ⇒ لا تُحتسب نشطة."""
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _sess("live", "203.0.113.9")
        _sess("zombie", "203.0.113.9", updated=_ago(120))   # قبل ساعتين
        r = ls.active_sessions_for_router(1, {"address": "203.0.113.9",
                                              "vpn_peer_address": ""})
        assert r["count"] == 1
        assert r["sessions"][0]["username"] == "live"


def test_tenant_count_and_router_live(app):
    with app.app_context():
        from app.radius.services import live_sessions as ls
        _sess("a", "203.0.113.9")
        _sess("b", "10.10.0.5", proto="PPP")
        assert ls.tenant_active_count(1) == 2
        lmap = ls.live_map(1)
        assert ls.router_live({"address": "198.51.100.7",
                               "vpn_peer_address": "10.10.0.5"}, lmap)["online"]
        assert ls.router_live({"address": "203.0.113.9",
                               "vpn_peer_address": ""}, lmap)["active"] == 1
        assert ls.router_live({"address": "192.0.2.1",
                               "vpn_peer_address": ""}, lmap)["online"] is False


# ───────────────────────── CoA Option C ─────────────────────────

def test_coa_matches_session_on_tunnel_ip(app):
    """راوتر عنوانه العام 198.51.100.7 لكن جلساته تَصل على IP النفق 10.10.0.5:
    find_all_nas_for_sessions يجده عبر vpn_peer_address فيُمكّن CoA."""
    with app.app_context():
        from app.radius.integration import radius_coa
        _nas("198.51.100.7", vpn_peer="10.10.0.5", secret="topsecret")
        _sess("cardX", "10.10.0.5", proto="PPP")
        found = radius_coa.find_all_nas_for_sessions(1, "cardX")
        assert len(found) == 1
        assert found[0]["nas_secret"] == "topsecret"
        # nas_ip المُحلّ = عنوان النفق (resolve_connection_address لوضع vpn).
        assert found[0]["nas_ip"] == "10.10.0.5"


def test_coa_prefers_exact_public_match(app):
    with app.app_context():
        from app.radius.integration import radius_coa
        _nas("203.0.113.9", vpn_peer="", secret="direct-secret")
        _sess("u", "203.0.113.9")
        found = radius_coa.find_all_nas_for_sessions(1, "u")
        assert len(found) == 1 and found[0]["nas_secret"] == "direct-secret"


# ───────────────────────── الويب ─────────────────────────

@pytest.fixture()
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=app.config["_admin_id"], admin_user="op",
                 is_super_admin=True, tenant_id=1, _csrf_token="t")
    return c


def test_router_dashboard_renders_radacct_sessions(app, client):
    with app.app_context():
        nid = _nas("198.51.100.7", vpn_peer="10.10.0.5", name="راوتر النفق")
        _sess("omar", "10.10.0.5", proto="PPP")     # على IP النفق
    res = client.get(f"/admin/radius/mt/{nid}/dashboard")
    assert res.status_code == 200
    h = res.get_data(as_text=True)
    assert "omar" in h                       # الجلسة معروضة من radacct
    assert "من جلسات RADIUS" in h            # المصدر الموثوق مُعلَن


def test_operations_shows_radacct_connected_count(app, client):
    with app.app_context():
        _nas("198.51.100.7", vpn_peer="10.10.0.5", name="نفق")
        _nas("203.0.113.9", name="مباشر")
        _sess("omar", "10.10.0.5", proto="PPP")
        _sess("ahmad", "203.0.113.9")
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    h = res.get_data(as_text=True)
    # عدّ «متصل» المزروع من radacct = 2 راوتران لهما جلسات نشطة.
    assert 'data-mt-radacct-connected="2"' in h
    assert "جلسات RADIUS نشطة" in h
