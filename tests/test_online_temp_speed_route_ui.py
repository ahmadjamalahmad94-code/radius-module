"""تكامل: مسار POST «السرعة المؤقتة» من صفحة المتصلين + رندر الصفحة.

يثبّت العقد الجديد بين الواجهة والراوت بعد إعادة بناء النافذة لتطابق المرجع:
  1. الصفحة /admin/radius/online تُرندَر بنجاح (يلتقط أي خطأ وقت تشغيل في
     markup النافذة الجديد) ويظهر فيها النموذج بأسماء حقوله الجديدة.
  2. الراوت يقرأ الوقت كـ (duration + duration_unit) ويحوّل «ساعات» → دقائق.
  3. «0 = غير محدود» مقبول على اتجاه السرعة ولا يُرفض (لا يرمي ValueError).

ملاحظة: لا يوجد صفّ nas_devices مطابق، فـ CoA يعود no_active_session بهدوء
(لا اتصال شبكي) لكن النافذة تُحفظ في DB — وهو بالضبط سيناريو المستخدم.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_temp_ui_")
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


def _seed(app):
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, created_at) "
                "VALUES (?,?,?,?)",
                (1, "tsuser", "pw", now),
            )
            c.execute("""
                INSERT INTO radacct
                    (tenant_id, acctsessionid, acctuniqueid, username,
                     nasipaddress, framedipaddress, callingstationid, acctstarttime)
                VALUES (?,?,?,?,?,?,?,?)
            """, (1, "ts-sess", "u-ts-sess", "tsuser",
                  "10.10.0.2", "10.20.30.254", "AA:BB:CC:DD:EE:FF", now))


def _logged_in(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        # تجاوز حارس الصلاحيات (RBAC) في بيئة الاختبار — لا يوجد صفّ admin
        # مزروع، فبدون هذا يُمنع GET/POST بـ403 (حاجز معروف، انظر الذاكرة).
        s["is_super_admin"] = True
    return client


def _row(app):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute(
            "SELECT temporary_speed, download_speed_kbps, upload_speed_kbps, metadata "
            "FROM subscribers WHERE username='tsuser'").fetchone()


def test_online_page_renders_temp_modal(app):
    """الصفحة تُرندَر والنموذج الجديد حاضر بأسماء حقوله الجديدة."""
    _seed(app)
    resp = _logged_in(app).get("/admin/radius/online")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # حقول النافذة الجديدة موجودة
    assert 'name="down_kbps"' in body
    assert 'name="up_kbps"' in body
    assert 'name="duration"' in body
    assert 'name="duration_unit"' in body
    assert "data-temp-conv" in body          # التحويل الحي
    assert "data-temp-remain" in body        # شريط «باقٍ»


def test_temp_speed_hours_unit_converts_to_minutes(app):
    """duration=2 + unit=hours ⇒ نافذة مدتها 120 دقيقة محفوظة في DB."""
    _seed(app)
    client = _logged_in(app)
    # GET أولاً لتوليد توكن CSRF في الجلسة
    client.get("/admin/radius/online")
    with client.session_transaction() as s:
        token = s.get("_csrf_token")
    resp = client.post("/admin/radius/online/temp-speed", data={
        "_csrf_token": token,
        "next": "/admin/radius/online",
        "username": "tsuser",
        "session_id": "ts-sess",
        "down_kbps": "2500",
        "up_kbps": "2500",
        "duration": "2",
        "duration_unit": "hours",
    })
    assert resp.status_code == 302
    r = _row(app)
    assert r["temporary_speed"] == 1
    meta = json.loads(r["metadata"])
    assert int(meta["temporary_speed_duration_minutes"]) == 120


def test_temp_speed_zero_is_unlimited_not_rejected(app):
    """down_kbps=0 (غير محدود) مقبول — يُحفظ ولا يُرفض كـ «أقل من 64»."""
    _seed(app)
    client = _logged_in(app)
    client.get("/admin/radius/online")
    with client.session_transaction() as s:
        token = s.get("_csrf_token")
    resp = client.post("/admin/radius/online/temp-speed", data={
        "_csrf_token": token,
        "next": "/admin/radius/online",
        "username": "tsuser",
        "session_id": "ts-sess",
        "down_kbps": "0",
        "up_kbps": "5000",
        "duration": "30",
        "duration_unit": "minutes",
    })
    assert resp.status_code == 302
    r = _row(app)
    assert r["temporary_speed"] == 1
    assert r["download_speed_kbps"] == 0      # غير محدود
    assert r["upload_speed_kbps"] == 5000
