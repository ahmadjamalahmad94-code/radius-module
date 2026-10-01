"""قرارات المالك 2026-10-01 — «المتصلون» و«المشتركون»:

* **السرعة المؤقتة للكروت** (كانت للمشتركين فقط): الكرت المولَّد له «مرآة»
  سرعةٍ في ``subscribers`` ⇒ تُطبَّق عليه فعلًا وتُرجَع؛ كرتٌ بلا مرآة يُرفض
  برسالةٍ مفهومة.
* **«مُستخدَم / متبقّي»** للكرت في قائمة المتصلين — بحسبة الفاحص نفسها.
* **هوت سبوت / برود باند**: تصنيفٌ واحد (‏PPP يحسم حتى مع NAS-Port-Type=Ethernet)
  وفلتر ``access`` في المتصلين والمشتركين — API والويب.
* **بيانات الاتصال على بطاقة المشترك** (‏``live``): الجلسة الحاليّة، وإلّا آخر جلسة.

شغّلْ هذا الملفَّ وحدَه."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_online_cards_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds") + "Z"


def _seed(app):
    now = datetime.utcnow()
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                         "VALUES (1, 't1', 'T1', ?)", (_iso(now),))
            conn.execute("INSERT INTO access_plans(id, tenant_id, name, code, service_type, "
                         " speed_down_kbps, speed_up_kbps, created_at) "
                         "VALUES (701, 1, 'HS', 'hs', 'hotspot', 4096, 1024, ?)", (_iso(now),))
            conn.execute("INSERT INTO access_plans(id, tenant_id, name, code, service_type, created_at) "
                         "VALUES (702, 1, 'BB', 'bb', 'pppoe', ?)", (_iso(now),))
            # مشتركان: هوت سبوت (من باقته) وبرود باند (من حقله)
            conn.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, status, "
                         " user_type, created_at) VALUES (1, 'sub-hs', 'x', 701, 'enabled', "
                         " 'subscriber', ?)", (_iso(now),))
            conn.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, status, "
                         " user_type, service_type, created_at) VALUES (1, 'sub-bb', 'x', 702, "
                         " 'enabled', 'subscriber', 'pppoe', ?)", (_iso(now),))
            conn.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, status, "
                         " user_type, created_at) VALUES (1, 'sub-never', 'x', 701, 'enabled', "
                         " 'subscriber', ?)", (_iso(now),))
            # حزمةُ كروت «ساعتان من أوّل اتصال»
            conn.execute("INSERT INTO card_batches(id, tenant_id, batch_code, package_name, plan_id, "
                         " count, generated, count_from_first_connect, time_value, time_unit, "
                         " created_at) VALUES (701, 1, 'B-701', 'ساعتان', 701, 2, 2, 1, 2, "
                         " 'hours', ?)", (_iso(now),))
            first = now - timedelta(minutes=30)
            conn.execute("INSERT INTO cards(id, tenant_id, batch_id, username, password, plan_id, "
                         " used, first_used_at, created_at) VALUES (701, 1, 701, 'card-mirror', "
                         " 'p', 701, 1, ?, ?)", (_iso(first), _iso(now)))
            # مرآة السرعة للكرت المولَّد
            conn.execute("INSERT INTO subscribers(tenant_id, username, password, plan_id, status, "
                         " user_type, card_batch_id, created_at) VALUES (1, 'card-mirror', 'p', "
                         " 701, 'enabled', 'card', 701, ?)", (_iso(now),))
            # كرتٌ بلا مرآة (مستورد)
            conn.execute("INSERT INTO cards(id, tenant_id, batch_id, username, password, plan_id, "
                         " used, created_at) VALUES (702, 1, 701, 'card-bare', 'p', 701, 0, ?)",
                         (_iso(now),))
            rows = (
                # username, sid, porttype, framedprotocol, ip, start, stop
                ("sub-hs", "s-hs", "Wireless-802.11", None, "10.0.0.5", now - timedelta(minutes=10), None),
                # راوترٌ يُرسل Ethernet للـPPPoE — البروتوكول يحسم
                ("sub-bb", "s-bb", "Ethernet", "PPP", "100.64.0.9", now - timedelta(minutes=20), None),
                ("card-mirror", "s-cm", "Wireless-802.11", None, "10.0.0.6", first, None),
                ("card-bare", "s-cb", "Wireless-802.11", None, "10.0.0.7", now - timedelta(minutes=5), None),
            )
            for u, sid, npt, fp, ip, start, stop in rows:
                conn.execute(
                    "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                    " nasipaddress, nasporttype, framedprotocol, framedipaddress, callingstationid, "
                    " acctstarttime, acctupdatetime, acctinputoctets, acctoutputoctets, acctstoptime) "
                    "VALUES (1, ?, ?, ?, '10.20.30.1', ?, ?, ?, 'AA:BB:CC:00:00:01', ?, ?, 1000, "
                    " 5000, ?)",
                    (sid, sid + "-u", u, npt, fp, ip, _iso(start), _iso(now),
                     _iso(stop) if stop else None))
            # جلسةٌ قديمةٌ مغلقة لمشتركٍ غير متّصل الآن
            conn.execute(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                " nasipaddress, framedipaddress, acctstarttime, acctstoptime, acctsessiontime, "
                " acctinputoctets, acctoutputoctets) VALUES (1, 's-old', 's-old-u', 'sub-never', "
                " '10.20.30.1', '10.0.0.99', ?, ?, 600, 11, 22)",
                (_iso(now - timedelta(days=1)), _iso(now - timedelta(days=1) + timedelta(minutes=10))))


# ── التصنيف ──────────────────────────────────────────────────────────────
def test_access_classifier_ppp_wins_over_ethernet():
    from app.radius.services.access_type import classify_session
    assert classify_session(nas_port_type="Ethernet", framed_protocol="PPP") == "broadband"
    assert classify_session(nas_port_type="Virtual") == "broadband"
    assert classify_session(nas_port_type="Wireless-802.11") == "hotspot"
    assert classify_session(nas_port_type="Ethernet") == "hotspot"
    assert classify_session(service_type="pppoe") == "broadband"
    assert classify_session(user_type="card") == "hotspot"
    assert classify_session() == ""


# ── المتصلون (API) ───────────────────────────────────────────────────────
def test_online_api_access_type_filter_and_card_time(app, client):
    _seed(app)
    res = client.get("/api/v1/sessions/online", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    by = {it["username"]: it for it in data["items"]}
    assert by["sub-hs"]["access_type"] == "hotspot"
    assert by["sub-bb"]["access_type"] == "broadband"
    assert by["card-mirror"]["access_type"] == "hotspot"
    assert data["accesses"] == {"hotspot": 3, "broadband": 1}

    cm = by["card-mirror"]
    # ساعتان من أوّل اتصال، مضى نصف ساعة ⇒ مستخدَم ~30د ومتبقٍّ ~90د
    assert cm["card_budget_seconds"] == 7200
    assert 1700 <= cm["card_used_seconds"] <= 1900
    assert 5300 <= cm["card_remaining_seconds"] <= 5500
    assert "card_used_seconds" not in by["sub-hs"]

    res = client.get("/api/v1/sessions/online?access=broadband", headers=AUTH)
    names = [it["username"] for it in res.get_json()["data"]["items"]]
    assert names == ["sub-bb"]
    res = client.get("/api/v1/sessions/online?access=hotspot&type=card", headers=AUTH)
    names = sorted(it["username"] for it in res.get_json()["data"]["items"])
    assert names == ["card-bare", "card-mirror"]


def test_card_temp_speed_applies_on_mirror_and_reverts(app, client, monkeypatch):
    _seed(app)
    import app.radius.services.temp_speed as ts
    pushed = []
    monkeypatch.setattr(ts, "_push_rate", lambda tid, u, rate: pushed.append((u, rate)) or {"ok": True})
    res = client.post("/api/v1/sessions/temp-speed", headers=AUTH, json={
        "username": "card-mirror", "session_id": "s-cm",
        "down_kbps": 1024, "up_kbps": 512, "duration_minutes": 30})
    assert res.status_code == 200, res.get_json()
    assert pushed and pushed[-1][0] == "card-mirror"
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT download_speed_kbps, upload_speed_kbps FROM subscribers "
                           "WHERE username='card-mirror'").fetchone()
        assert (row["download_speed_kbps"], row["upload_speed_kbps"]) == (1024, 512)
    res = client.post("/api/v1/sessions/temp-speed/cancel", headers=AUTH,
                      json={"username": "card-mirror", "session_id": "s-cm"})
    assert res.status_code == 200, res.get_json()
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute("SELECT download_speed_kbps, upload_speed_kbps FROM subscribers "
                           "WHERE username='card-mirror'").fetchone()
        assert (row["download_speed_kbps"], row["upload_speed_kbps"]) == (0, 0), \
            "الكرت لم يرجع لسرعته الأصليّة بعد الإلغاء"
    # الإرجاع يعود لسرعة الباقة (لا «بلا حدّ»)
    assert pushed[-1] == ("card-mirror", "1024k/4096k")


def test_card_without_speed_account_gets_clear_message(app, client):
    _seed(app)
    res = client.post("/api/v1/sessions/temp-speed", headers=AUTH, json={
        "username": "card-bare", "session_id": "s-cb",
        "down_kbps": 1024, "up_kbps": 512, "duration_minutes": 30})
    assert res.status_code == 422
    assert "بلا حساب سرعة" in res.get_json()["error"]["message"]


# ── المشتركون (API) ──────────────────────────────────────────────────────
def test_accounts_api_live_usage_and_access_filter(app, client):
    _seed(app)
    res = client.get("/api/v1/accounts?limit=50", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    by = {it["username"]: it for it in res.get_json()["data"]["items"]}
    assert "card-mirror" not in by  # مرايا الكروت لا تظهر في المشتركين
    hs = by["sub-hs"]
    assert hs["online"] is True and hs["access_type"] == "hotspot"
    assert hs["live"]["framed_ip"] == "10.0.0.5"
    assert hs["live"]["bytes_in"] == 1000 and hs["live"]["bytes_out"] == 5000
    assert 500 <= hs["live"]["session_time"] <= 700
    assert by["sub-bb"]["access_type"] == "broadband"
    never = by["sub-never"]
    assert never["online"] is False
    assert never["live"]["framed_ip"] == "10.0.0.99" and never["live"]["session_time"] == 600

    res = client.get("/api/v1/accounts?access=broadband", headers=AUTH)
    data = res.get_json()["data"]
    assert [it["username"] for it in data["items"]] == ["sub-bb"] and data["total"] == 1
    res = client.get("/api/v1/accounts?access=hotspot", headers=AUTH)
    data = res.get_json()["data"]
    assert sorted(it["username"] for it in data["items"]) == ["sub-hs", "sub-never"]
    assert data["total"] == 2


# ── الويب ────────────────────────────────────────────────────────────────
def _web_login(app, client):
    with app.app_context():
        from app.radius.db.repos import admins_repo
        from app.radius.db.connection import db
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="acc_owner", password="pw", full_name="مالك",
                                 is_super_admin=True,
                                 role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
        db().execute("UPDATE admins SET is_co_owner=1 WHERE username='acc_owner'")
        db().commit()
    r = client.post("/admin/radius/login", data={"username": "acc_owner", "password": "pw"})
    assert r.status_code in (302, 303)


def test_web_online_and_users_access_filter(app, client):
    _seed(app)
    _web_login(app, client)
    r = client.get("/admin/radius/online?access=broadband")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'name="access"' in html and "sub-bb" in html and "sub-hs" not in html

    r = client.get("/admin/radius/users?access=broadband")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'name="access"' in html and "sub-bb" in html and "sub-never" not in html
