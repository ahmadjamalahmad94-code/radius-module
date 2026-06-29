# -*- coding: utf-8 -*-
"""اختبارات سلسلة الدفع للجوال (FCM) من طرف إلى طرف — الفجوات المسدودة:

  • الجسر (إشعارات لوحة التراخيص) صار يَدفع عبر FCM مثل الإشعارات المحلّية
    (كان يَكتب في الجرس مباشرةً بلا دفع).
  • الدفع يُطلَق **مرّة واحدة** للإشعار الجديد فقط؛ إعادة الاستدعاء بنفس
    dedup_key (إصابة تكرار) لا تُعيد الدفع → لا إشعارات مُكرّرة على الجوّال.
  • خدمة + مسار «أرسل إشعار تجريبي» يَختبران الدفع ويُميّزان الحالات
    (لا أجهزة / غير مُفعَّل / نجح).
  • بطاقة حالة الدفع تَظهر في مركز الإشعارات.

تُموّه طبقة الإرسال (fcm_push.send_to_tokens) — لا اتّصال بـ FCM الحقيقي.
عزل لكل ملفّ — راجع memory test-isolation-per-file.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_push_e2e_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "test.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    os.environ.pop("FIREBASE_CREDENTIALS_PATH", None)
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    os.environ.pop("HOBERADIUS_FCM_DISABLED", None)
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
        admin = admins_repo.create_admin(
            username="op", password="op123456", full_name="مشغّل")
        created.config["_admin_id"] = int(getattr(admin, "id", 1) or 1)
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture()
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=app.config["_admin_id"], admin_user="op",
                 admin_name="مشغّل", is_super_admin=True, tenant_id=1,
                 _csrf_token="t")
    return c


# ───────────────── الدفع للصفّ الجديد فقط (لا تكرار) ─────────────────

def test_notify_pushes_only_on_new_not_on_dedup(app, monkeypatch):
    """notify() يُطلق الدفع للإشعار الجديد، ولا يُعيده عند إصابة dedup_key."""
    from app.radius.services import notifications

    fired = []
    monkeypatch.setattr(notifications, "_fire_push",
                        lambda tid, **kw: fired.append(kw))
    with app.app_context():
        a = notifications.notify(1, title="مرّة", dedup_key="k-dup")
        b = notifications.notify(1, title="مرّة-مكرّرة", dedup_key="k-dup")
        assert a == b              # نفس الصفّ (إزالة تكرار)
        assert len(fired) == 1     # الدفع انطلق مرّة واحدة فقط
        assert fired[0]["nid"] == a


def test_create_returning_flags_new_then_dedup(app):
    from app.radius.db.repos import notifications_repo as R
    with app.app_context():
        nid1, new1 = R.create_returning(1, title="t", dedup_key="x1")
        nid2, new2 = R.create_returning(1, title="t2", dedup_key="x1")
        assert new1 is True and new2 is False
        assert nid1 == nid2
        # بلا مفتاح → دائمًا جديد
        nid3, new3 = R.create_returning(1, title="nokey")
        assert new3 is True and nid3 is not None


# ───────────────── الجسر يَدفع عبر FCM ─────────────────

def test_bridge_ingest_fires_push_per_new_item(app, monkeypatch):
    """إشعارات لوحة التراخيص (الجسر) تَمرّ عبر notify() فتُدفَع للجوال؛
    إعادة الاستيعاب (نفس المراجع) لا تُعيد الدفع."""
    from app.radius.services import notifications
    from app.radius.services.notifications_bridge import NotificationBridgeService

    fired = []
    monkeypatch.setattr(notifications, "_fire_push",
                        lambda tid, **kw: fired.append(kw))
    items = [
        {"id": "r1", "type": "license", "severity": "warning",
         "title": "تجديد الترخيص", "body": "قريبًا", "link": "/admin/radius/account"},
        {"id": "r2", "type": "billing", "title": "فاتورة", "message": "ادفع"},
    ]
    with app.app_context():
        svc = NotificationBridgeService()
        res = svc.ingest(1, items)
        assert res["ingested"] == 2
        # دُفع لكل عنصر جديد، وحُمِلت عناوينه الصحيحة + النوع.
        assert len(fired) == 2
        titles = {f["title"] for f in fired}
        assert titles == {"تجديد الترخيص", "فاتورة"}
        assert {f["type"] for f in fired} == {"license", "billing"}
        # إعادة الاستيعاب لا تُعيد الدفع (dedup على bridge:<ref>).
        svc.ingest(1, items)
        assert len(fired) == 2


# ───────────────── خدمة الإشعار التجريبي ─────────────────

def test_send_test_push_no_tokens(app):
    from app.radius.services import notifications
    with app.app_context():
        res = notifications.send_test_push(1)
        assert res["ok"] is False and res["reason"] == "no_tokens"


def test_send_test_push_dispatches_to_registered_tokens(app, monkeypatch):
    from app.radius.services import notifications
    from app.radius.db.repos import device_push_tokens_repo as repo
    from app.services import fcm_push

    captured = {}

    def fake_send(tokens, title, body, data):
        captured.update(tokens=list(tokens), title=title, body=body, data=dict(data))
        return {"ok": True, "sent": len(tokens), "failed": 0, "invalid_tokens": []}

    monkeypatch.setattr(fcm_push, "send_to_tokens", fake_send)
    with app.app_context():
        repo.register(1, "tok-X", admin_id=1, platform="android")
        res = notifications.send_test_push(1)
        assert res["ok"] is True and res["sent"] == 1
        assert captured["tokens"] == ["tok-X"]
        assert "تجريبي" in captured["title"]


def test_push_status_reflects_devices(app):
    from app.radius.services import notifications
    from app.radius.db.repos import device_push_tokens_repo as repo
    with app.app_context():
        st = notifications.push_status(1)
        assert st["devices"] == 0 and st["enabled"] is False
        repo.register(1, "tok-1", platform="android")
        repo.register(1, "tok-2", platform="android")
        assert notifications.push_status(1)["devices"] == 2


# ───────────────── مسار + واجهة الإشعار التجريبي ─────────────────

def test_test_push_route_no_devices_flashes_guidance(app, client):
    res = client.post("/admin/radius/notifications/test-push",
                      data={"_csrf_token": "t"}, follow_redirects=True)
    assert res.status_code == 200
    h = res.get_data(as_text=True)
    assert "لا توجد أجهزة مُسجَّلة" in h


def test_test_push_route_dispatches_when_device_present(app, client, monkeypatch):
    from app.services import fcm_push
    from app.radius.db.repos import device_push_tokens_repo as repo

    calls = {"n": 0}

    def fake_send(tokens, title, body, data):
        calls["n"] += 1
        return {"ok": True, "sent": len(list(tokens)), "failed": 0,
                "invalid_tokens": []}

    monkeypatch.setattr(fcm_push, "send_to_tokens", fake_send)
    with app.app_context():
        repo.register(1, "tok-route", platform="android")
    res = client.post("/admin/radius/notifications/test-push",
                      data={"_csrf_token": "t"}, follow_redirects=True)
    assert res.status_code == 200
    assert calls["n"] == 1
    assert "تم إرسال الإشعار التجريبي" in res.get_data(as_text=True)


def test_center_renders_push_status_card(app, client):
    res = client.get("/admin/radius/notifications")
    assert res.status_code == 200
    h = res.get_data(as_text=True)
    assert "دفع الإشعارات للجوال" in h
    assert "أرسل إشعار تجريبي" in h
    assert "الدفع غير مُفعَّل" in h          # لا اعتماد في الاختبار
