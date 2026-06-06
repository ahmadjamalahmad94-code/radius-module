"""انحدار: ضبط «السرعة المؤقتة» من نموذج تعديل المشترك يثبّت وقت الانتهاء.

الجذر الذي كُسِر: عند تفعيل المفتاح بمدة 0/فارغة، كان apply_temp_speed يرمي
ValueError (المدة < 1) ويُبتلَع في _delegate_temp_speed كتحذير، فلا تُكتب
النافذة (temporary_speed=0، بلا temporary_speed_to) ⇒ «لا يوجد وقت انتهاء».

الإصلاح: عند تفعيل المفتاح نضمن مدة صالحة دائمًا (المخزّنة سابقًا أو 30) فتُثبَّت
النافذة دومًا. هذا الملف يثبّت أن كل حالات المدة (فارغة/صفر/مفقودة/صالحة) تُنتج
الآن نافذة محفوظة بوقت انتهاء.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ts_sym_")
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


def _logged_in(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        s["is_super_admin"] = True
    return client


def _seed(app, username):
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO access_plans(id, tenant_id, name, code, "
                "speed_up_kbps, speed_down_kbps, created_at) "
                "VALUES (730, 1, 'P', 'p', 5000, 10000, ?)", (now,))
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                "status, user_type, service_type, created_at) "
                "VALUES (1, ?, 'pw', 730, 'enabled', 'subscriber', 'hotspot', ?)",
                (username, now))


def _save_temp(app, username, *, duration, down="2000", up="2000"):
    client = _logged_in(app)
    client.get(f"/admin/radius/users/{username}/edit")
    with client.session_transaction() as s:
        token = s.get("_csrf_token")
    data = {
        "_csrf_token": token, "username": username, "password": "pw",
        "status": "enabled", "service_type": "hotspot", "plan_id": "730",
        "temporary_speed": "1",
        "temporary_download_speed_kbps": down,
        "temporary_upload_speed_kbps": up,
    }
    if duration is not None:
        data["temporary_speed_duration_minutes"] = duration
    resp = client.post(f"/admin/radius/users/{username}", data=data)
    return client, resp


def _window(app, username):
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT temporary_speed, metadata FROM subscribers WHERE username=?",
            (username,)).fetchone()
    meta = json.loads(row["metadata"] or "{}")
    to = meta.get("temporary_speed_to") or (meta.get("advanced") or {}).get("temporary_speed_to")
    return row["temporary_speed"], to, meta


@pytest.mark.parametrize("label,duration", [
    ("valid", "30"),
    ("empty", ""),
    ("zero", "0"),
    ("missing", None),
])
def test_enabling_temp_always_persists_window(app, label, duration):
    """تفعيل المفتاح يثبّت دائمًا temporary_speed=1 + وقت انتهاء — مهما كانت
    قيمة المدة المُرسَلة (فارغة/صفر/مفقودة تتراجع للافتراضي 30)."""
    uname = f"sym_{label}"
    _seed(app, uname)
    _client, resp = _save_temp(app, uname, duration=duration)
    assert resp.status_code in (302, 303)
    temp_col, to, meta = _window(app, uname)
    assert temp_col == 1, f"[{label}] temporary_speed لم يُفعَّل: {meta}"
    assert to, f"[{label}] temporary_speed_to لم يُحفظ (العَرَض القديم): {meta}"


def test_reload_renders_saved_end_time(app):
    """بعد الحفظ، نموذج التعديل يعيد رندر temporary_speed_to فيعمل العدّاد."""
    uname = "sym_reload"
    _seed(app, uname)
    client, _ = _save_temp(app, uname, duration="")  # الحالة المكسورة سابقًا
    page = client.get(f"/admin/radius/users/{uname}/edit").get_data(as_text=True)
    m = re.search(r'name="temporary_speed_to"\s+value="([^"]*)"', page)
    assert m and m.group(1).strip(), \
        "نموذج التعديل أعاد رندر temporary_speed_to فارغًا — العدّاد سيظل 00:00"
