# -*- coding: utf-8 -*-
"""تفعيل قناتَي «دفع الجوال» و«ويندوز» في «إشعارات الإدارة».

بعد مركزة الدفع في لوحة التراخيص (forward-to-licensing, 9b345c5) صارتا قناتين
حقيقيّتين بدل «قريبًا»:
  • «دفع الجوال» (push): حين تُفعَّل لحدث، dispatch يَطلب من notify() تحويل طلب
    الدفع للوحة (سلطة FCM المركزيّة) فتُرسله لأجهزة العميل (أندرويد). نُثبِت
    التحويل بمحاكاة الجسر (_fire_push).
  • «ويندوز» (windows): تطبيق سطح المكتب يَستطلع مركز الإشعارات الموحّد
    (panel_notifications)، فالحدث يَصِله عبر كتابة الجرس الدائمة.

يَشمل: حالة القنوات (deliverable لا deferred)، حفظ التفضيل، التحويل عند التفعيل
فقط (toggle)، بقاء الجرس مكتوبًا دائمًا، وتصيير الصفحة بلا «قريبًا» للقناتين.
شغّل الملف وحده. (واتساب/SMS تبقيان «قريبًا» — قناتا الإدارة لهما مرحلة لاحقة.)
"""
from __future__ import annotations

import os
import re

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "push_windows.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def _client(app_ctx):
    c = app_ctx.test_client()
    with c.session_transaction() as s:
        s["tenant_id"] = 1
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["_csrf_token"] = "tok"
    return c


# ════════════════════════════════════════════════════════════════════════
# (1) حالة القنوات — push/windows صارتا تُسلَّمان لا مؤجَّلتَين
# ════════════════════════════════════════════════════════════════════════
def test_push_and_windows_are_deliverable_not_deferred():
    from app.radius.services import admin_alerts as aa
    assert "push" in aa.DELIVERABLE_CHANNELS
    assert "windows" in aa.DELIVERABLE_CHANNELS
    assert "push" not in aa.DEFERRED_CHANNELS
    assert "windows" not in aa.DEFERRED_CHANNELS
    # واتساب/SMS تبقيان مؤجَّلتَين لقناة الإدارة.
    assert aa.DEFERRED_CHANNELS == frozenset({"whatsapp", "sms"})
    # ما تزالان ضمن قائمة القنوات الكاملة للعرض.
    assert "push" in aa.CHANNELS and "windows" in aa.CHANNELS


def test_set_channels_persists_push_and_windows(app_ctx):
    from app.radius.services import admin_alerts as aa
    aa.set_channels(1, "subscriber_new", ["bell", "push", "windows"])
    chans = aa.channels_for(1, "subscriber_new")
    assert "push" in chans and "windows" in chans and "bell" in chans
    # القراءة بعد الكتابة ثابتة (مخزّنة في tenant_settings).
    assert aa.channels_for(1, "subscriber_new") == chans


# ════════════════════════════════════════════════════════════════════════
# (2) «دفع الجوال» — يُحوَّل للوحة فقط حين تُفعَّل القناة (toggle)
# ════════════════════════════════════════════════════════════════════════
def test_dispatch_with_push_channel_forwards_to_licensing(app_ctx, monkeypatch):
    from app.radius.services import admin_alerts as aa
    from app.radius.services import notifications as notif
    calls = []
    # محاكاة الجسر: نلتقط طلب التحويل بدل ضرب الشبكة.
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    aa.set_channels(1, "subscriber_new", ["bell", "push"])
    aa.dispatch(1, "subscriber_new", {"username": "u1", "full_name": "Ali"})
    assert len(calls) == 1
    # حُمِّل العنوان (تسمية الحدث) + النوع للوحة.
    assert calls[0]["title"] == "إضافة مشترك جديد"
    assert calls[0]["ntype"]  # نوع إشعار المركز للمجموعة


def test_dispatch_without_push_channel_does_not_forward(app_ctx, monkeypatch):
    from app.radius.services import admin_alerts as aa
    from app.radius.services import notifications as notif
    from app.radius.db.repos import notifications_repo as R
    calls = []
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    aa.set_channels(1, "subscriber_new", ["bell"])  # لا push
    aa.dispatch(1, "subscriber_new", {"username": "u1"})
    assert calls == []                 # لا تحويل دفع
    assert R.unread_count(1) == 1       # لكن الجرس كُتب دائمًا


# ════════════════════════════════════════════════════════════════════════
# (3) «ويندوز» — يَصِل عبر كتابة الجرس التي يَستطلعها التطبيق
# ════════════════════════════════════════════════════════════════════════
def test_dispatch_windows_writes_bell_for_polling(app_ctx, monkeypatch):
    from app.radius.services import admin_alerts as aa
    from app.radius.services import notifications as notif
    from app.radius.db.repos import notifications_repo as R
    calls = []
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    # ويندوز فقط (بلا push) — يَصِل عبر مركز الإشعارات (الجرس)، لا تحويل FCM.
    aa.set_channels(1, "subscriber_new", ["bell", "windows"])
    aa.dispatch(1, "subscriber_new", {"username": "u1", "full_name": "Ali"})
    assert calls == []                 # ويندوز لا يُطلق دفع FCM
    rows = R.list_for(1)
    assert len(rows) == 1              # الحدث في المركز كي يَستطلعه تطبيق ويندوز
    assert rows[0]["title"] == "إضافة مشترك جديد"


# ════════════════════════════════════════════════════════════════════════
# (4) وحدة notify(push=...) — البوّابة الدقيقة للتحويل
# ════════════════════════════════════════════════════════════════════════
def test_notify_push_false_suppresses_forward(app_ctx, monkeypatch):
    from app.radius.services import notifications as notif
    calls = []
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    nid = notif.notify(1, type="system", title="t", body="b",
                       source="local", dedup_key="p-off", push=False)
    assert nid is not None and calls == []


def test_notify_push_true_forwards(app_ctx, monkeypatch):
    from app.radius.services import notifications as notif
    calls = []
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    notif.notify(1, type="system", title="t", body="b",
                 source="local", dedup_key="p-on", push=True)
    assert len(calls) == 1


def test_notify_push_default_still_forwards(app_ctx, monkeypatch):
    """التوافق الخلفي: المُتّصِل المباشر (بلا push) يَبقى يُحوّل افتراضيًّا."""
    from app.radius.services import notifications as notif
    calls = []
    monkeypatch.setattr(notif, "_fire_push",
                        lambda tenant_id, **kw: calls.append(kw))
    notif.notify(1, type="license", title="t", body="b",
                 source="local", dedup_key="p-def")
    assert len(calls) == 1


# ════════════════════════════════════════════════════════════════════════
# (5) الصفحة — القناتان توجلان كأزرار حقيقيّة بلا «قريبًا»
# ════════════════════════════════════════════════════════════════════════
def _chip(html: str, chan: str) -> str:
    m = re.search(r'<button[^>]*data-an-chan="%s".*?</button>' % chan, html, re.S)
    return m.group(0) if m else ""


def test_page_renders_push_windows_as_real_toggles(app_ctx):
    res = _client(app_ctx).get("/admin/radius/admin-notifications")
    assert res.status_code == 200
    h = res.get_data(as_text=True)
    assert "دفع الجوال" in h and "ويندوز" in h
    # push/windows لم تَعُد مؤجَّلة → بلا شارة «قريبًا» (is-soon/an-soon).
    push_chip = _chip(h, "push")
    win_chip = _chip(h, "windows")
    assert push_chip and "is-soon" not in push_chip and "an-soon" not in push_chip
    assert win_chip and "is-soon" not in win_chip and "an-soon" not in win_chip
    # واتساب/SMS تبقيان مؤجَّلتَين → ما تزال شارة «قريبًا» تظهر لهما.
    assert "is-soon" in _chip(h, "whatsapp")
    assert "is-soon" in _chip(h, "sms")


def test_page_set_channels_persists_push_via_route(app_ctx):
    from app.radius.services import admin_alerts as aa
    c = _client(app_ctx)
    r = c.post("/admin/radius/admin-notifications/channels",
               headers={"X-CSRFToken": "tok", "X-Requested-With": "XMLHttpRequest"},
               data={"key": "subscriber_new", "channels": ["bell", "push"],
                     "_csrf_token": "tok"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and "push" in body["channels"]
    assert "push" in aa.channels_for(1, "subscriber_new")
