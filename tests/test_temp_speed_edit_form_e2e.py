"""E2E repro: ضبط «السرعة المؤقتة» من نموذج تعديل المشترك (users_form).

يثبّت العَرَض الذي أبلغ عنه المستخدم: المفتاح مفعّل + مدة 30 دقيقة، لكن بعد
الحفظ لا يُخزَّن temporary_speed_to (العدّاد 00:00 / «لا يوجد وقت انتهاء»).

نحاكي POST الذي ترسله الواجهة فعلاً (الحقول من unit_input_picker تصل
كقيمة base في حقل hidden بنفس الاسم) ثم نفحص قاعدة البيانات + إعادة الرندر.
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
    tmp = tempfile.mkdtemp(prefix="hr_ts_edit_")
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
                "INSERT INTO access_plans(id, tenant_id, name, code, "
                "speed_up_kbps, speed_down_kbps, created_at) "
                "VALUES (701, 1, 'P', 'p', 5000, 10000, ?)", (now,))
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                "status, user_type, service_type, created_at) "
                "VALUES (1, 'editsub', 'pw', 701, 'enabled', 'subscriber', 'hotspot', ?)",
                (now,))


def _logged_in(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        s["is_super_admin"] = True
    return client


def _row(app):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute(
            "SELECT temporary_speed, download_speed_kbps, upload_speed_kbps, metadata "
            "FROM subscribers WHERE username='editsub'").fetchone()


def test_edit_form_save_persists_temp_speed_window(app):
    _seed(app)
    client = _logged_in(app)
    client.get("/admin/radius/users/editsub/edit")
    with client.session_transaction() as s:
        token = s.get("_csrf_token")
    resp = client.post("/admin/radius/users/editsub", data={
        "_csrf_token": token,
        "username": "editsub",
        "password": "pw",
        "status": "enabled",
        "service_type": "hotspot",
        "plan_id": "701",
        # ── temp speed: المفتاح مفعّل + الحقول كما ترسلها unit_input_picker ──
        "temporary_speed": "1",
        "temporary_download_speed_kbps": "2000",
        "temporary_upload_speed_kbps": "1000",
        "temporary_speed_duration_minutes": "30",
    })
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:600]

    r = _row(app)
    assert r["temporary_speed"] == 1, "العمود temporary_speed لم يُضبط"
    meta = json.loads(r["metadata"] or "{}")
    # المفتاح المخزَّن في المستوى الأعلى بفعل apply_temp_speed
    to_top = meta.get("temporary_speed_to")
    to_adv = (meta.get("advanced") or {}).get("temporary_speed_to")
    assert to_top or to_adv, f"temporary_speed_to لم يُحفظ إطلاقًا: {meta}"
    assert r["download_speed_kbps"] == 2000, "السرعة المؤقتة لم تُكتب في عمود السرعة"


def test_edit_form_reload_shows_saved_end_time(app):
    """بعد الحفظ، صفحة التعديل تعرض وقت الانتهاء (لا «لا يوجد وقت انتهاء»)."""
    _seed(app)
    client = _logged_in(app)
    client.get("/admin/radius/users/editsub/edit")
    with client.session_transaction() as s:
        token = s.get("_csrf_token")
    client.post("/admin/radius/users/editsub", data={
        "_csrf_token": token,
        "username": "editsub", "password": "pw", "status": "enabled",
        "service_type": "hotspot", "plan_id": "701",
        "temporary_speed": "1",
        "temporary_download_speed_kbps": "2000",
        "temporary_upload_speed_kbps": "1000",
        "temporary_speed_duration_minutes": "30",
    })
    # إعادة فتح نموذج التعديل — يجب أن يحمل قيمة temporary_speed_to في الحقل المخفي
    page = client.get("/admin/radius/users/editsub/edit").get_data(as_text=True)
    import re
    m = re.search(r'name="temporary_speed_to"\s+value="([^"]*)"', page)
    assert m and m.group(1).strip(), \
        "نموذج التعديل أعاد الرندر بحقل temporary_speed_to فارغ (العدّاد سيظل 00:00)"
