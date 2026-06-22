"""صفحة «المتصلون الآن» — اسم البرج بجوار الـIP (فلتر السيرفر + صفوف الجدول).

قبل الإصلاح كان عمود/فلتر الـNAS يعرض الـIP الخام فقط (10.10.0.2). الآن
يُحلّ إلى «الاسم (IP)» من nas_devices (مطابقة address/vpn_peer_address)، مع
الارتداد للـIP وحده حين لا جهاز مطابق — نفس معالجة صفحة الإحصائيات.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_naslbl_")
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


def _seed_session(conn, *, username, session_id, nas_ip):
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute("""
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, framedipaddress, callingstationid, acctstarttime)
        VALUES (?,?,?,?,?,?,?,?)
    """, (1, session_id, f"u-{session_id}", username,
          nas_ip, "10.20.30.254", "AA:BB:CC:DD:EE:FF", now))


def _logged_in(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        s["is_super_admin"] = True
    return client


def test_online_nas_shows_name_with_ip(app):
    """برج باسم مطابق → «الاسم (IP)» في الفلتر والصف؛ والـIP يظل ظاهرًا."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,address,secret,vendor,created_at) "
                      "VALUES(1,'برج المبنى الرئيسي','10.10.0.2','s','mikrotik',?)",
                      (datetime.utcnow().isoformat() + "Z",))
            _seed_session(c, username="ahmad", session_id="s1", nas_ip="10.10.0.2")
    resp = _logged_in(app).get("/admin/radius/online")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # الاسم الودّي + الـIP موجودان (الاسم أساسي، الـIP ثانوي بين قوسين/سطر).
    assert "برج المبنى الرئيسي" in body
    assert "10.10.0.2" in body


def test_online_nas_ip_only_when_no_device(app):
    """جلسة على IP بلا جهاز مطابق → يظهر الـIP وحده بلا اسم مخترَع."""
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_session(c, username="ahmad", session_id="s1", nas_ip="10.99.99.99")
    resp = _logged_in(app).get("/admin/radius/online")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "10.99.99.99" in body
