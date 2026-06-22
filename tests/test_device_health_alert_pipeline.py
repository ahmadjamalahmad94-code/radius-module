# -*- coding: utf-8 -*-
"""خط أنابيب تنبيهات تتبّع حالة الأجهزة (device-health) — انقطاع/عودة.

يثبّت الإصلاح: تحوّل حالة جهاز (down / recovery) يُطلق تنبيهًا ويُحاول
التسليم عبر القناة الخارجية، **ويُسطِّحه دائمًا في مركز الإشعارات الموحّد
(الجرس) بلا إسقاط صامت** — حتى لو كان تلجرام غير مُفعّل، وعندها تحمل
رسالة الجرس تلميح «فعّل تلجرام».
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_dh_alert_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "t.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1", FLASK_SECRET="k")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _make_device(alert_channel: str = "") -> int:
    """راوتر + جهاز مُراقَب، ويُضبط على «down» مرّتين فيتجاوز عتبة DOWN_AFTER_N."""
    from app.radius.core.types import NasDevice
    from app.radius.db.repos import nas_repo, device_health_repo as repo
    nas = nas_repo.upsert_nas(NasDevice(
        id=None, name="RB-test", address="10.0.0.1", secret="s",
        vendor="mikrotik", enabled=True))
    did = repo.create_device(
        tenant_id=1, router_id=int(nas.id), name="AP السطح",
        interface_name="", ip_address="192.168.88.50",
        network_cidr="192.168.88.0/24", gateway_address="192.168.88.1",
        device_type="access_point", alert_channel=alert_channel)
    # عيّنتان «down» متتاليتان ⇒ consecutive_down_count = 2 (>= DOWN_AFTER_N).
    repo.set_status(tenant_id=1, device_id=did, status="down")
    repo.set_status(tenant_id=1, device_id=did, status="down")
    return did


def _panel_rows(type_="system"):
    from app.radius.db.repos import notifications_repo
    return [n for n in notifications_repo.list_for(1, limit=50)]


def test_down_transition_surfaces_in_panel_even_without_telegram(app):
    """تلجرام غير مُفعّل ⇒ لا تسليم خارجي، لكن يَظهر إشعار «انقطاع» في الجرس
    (لا إسقاط صامت) ويُسجَّل التنبيه كـfailed، والرسالة تحمل تلميح تلجرام."""
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo
        did = _make_device(alert_channel="")  # القناة الافتراضية → محرّك الإشعارات (تلجرام)
        fresh = repo.get_device(1, did)

        fired = dha.evaluate_and_dispatch(
            tenant_id=1, device=fresh, prev_status="up",
            new_status="down", latency_ms=None)

        # تلجرام غير مُفعّل ⇒ لم يُسلَّم خارجيًا.
        assert fired == []
        # لكن سُجِّل التنبيه (لا إسقاط صامت).
        alerts = repo.list_alerts(1, device_id=did)
        assert any(a.get("alert_type") == "down" for a in alerts)
        # وظهر في مركز الإشعارات الموحّد (الجرس) كإشعار حرِج.
        rows = _panel_rows()
        down_notif = [n for n in rows if "انقطاع" in (n.get("title") or "")]
        assert down_notif, "لم يَظهر إشعار الانقطاع في مركز الإشعارات"
        assert down_notif[0]["severity"] == "critical"
        assert down_notif[0]["link"] == "/admin/radius/device-health"
        # ويحمل تلميح تفعيل تلجرام (تسطيح واضح لا صامت).
        assert "تلجرام" in (down_notif[0].get("body") or "")


def test_recovery_transition_surfaces_recovery_notification(app):
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo
        did = _make_device(alert_channel="")
        # الجهاز عاد: down → up.
        repo.set_status(tenant_id=1, device_id=did, status="up")
        fresh = repo.get_device(1, did)
        dha.evaluate_and_dispatch(
            tenant_id=1, device=fresh, prev_status="down",
            new_status="up", latency_ms=12.0)
        rows = _panel_rows()
        rec = [n for n in rows if "عاد الاتصال" in (n.get("title") or "")]
        assert rec, "لم يَظهر إشعار العودة في مركز الإشعارات"
        assert rec[0]["severity"] == "success"


def test_delivered_channel_marks_fired_and_no_telegram_hint(app, monkeypatch):
    """عند نجاح التسليم الخارجي: fired يحوي النوع، ويظهر إشعار الجرس بلا تلميح
    «تلجرام غير مُفعّل»."""
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo
        did = _make_device(alert_channel="telegram")
        fresh = repo.get_device(1, did)
        # حاكِ تسليمًا ناجحًا عبر القناة الخارجية.
        monkeypatch.setattr(dha, "_send", lambda *a, **k: (True, "sent"))
        fired = dha.evaluate_and_dispatch(
            tenant_id=1, device=fresh, prev_status="up",
            new_status="down", latency_ms=None)
        assert "down" in fired
        alerts = repo.list_alerts(1, device_id=did)
        assert any(a.get("status") == "sent" and a.get("alert_type") == "down"
                   for a in alerts)
        rows = _panel_rows()
        down_notif = [n for n in rows if "انقطاع" in (n.get("title") or "")]
        assert down_notif and "تلجرام غير" not in (down_notif[0].get("body") or "")


def _configure_canonical_telegram():
    """يُهيّئ تلجرام في المتجر الرسمي نفسه الذي تكتبه صفحة «تنبيهات تلجرام»
    (tenant_telegram_settings) — تمكين + توكن + chat id."""
    from app.radius.db.repos import tenant_telegram_settings_repo as tg
    tg.upsert(tenant_id=1, bot_token="123:ABC", chat_id="-100999",
              enabled=True, thread_id="")


def test_configured_telegram_sends_via_canonical_sender_not_engine(app, monkeypatch):
    """جوهر الإصلاح: حين يكون تلجرام مُهيّأ في متجر صفحة «تنبيهات تلجرام»،
    تنبيه الانقطاع يُرسَل عبر telegram_notifier.send_to_tenant (نفس مُرسِل تلك
    الصفحة) مباشرةً — لا عبر notifications_engine (بوّابة router_down الثانية)."""
    with app.app_context():
        import app.radius.services.device_health_alerts as dha
        import app.radius.services.telegram_notifier as tn
        import app.radius.services.notifications_engine as ne
        from app.radius.db.repos import device_health_repo as repo

        _configure_canonical_telegram()
        assert dha.telegram_ready(1) is True       # المتجر الرسمي يراه مُهيّأً

        sent, engine_called = [], []
        monkeypatch.setattr(tn, "send_to_tenant",
                            lambda tid, text: (sent.append((tid, text)) or (True, "")))
        monkeypatch.setattr(ne, "notify_event",
                            lambda *a, **k: engine_called.append(a) or None)

        did = _make_device(alert_channel="")   # القناة الافتراضية
        fresh = repo.get_device(1, did)
        fired = dha.evaluate_and_dispatch(
            tenant_id=1, device=fresh, prev_status="up",
            new_status="down", latency_ms=None)

        assert fired == ["down"]                 # سُلِّم فعلًا
        assert len(sent) == 1                    # عبر المُرسِل الرسمي
        assert "انقطع الاتصال" in sent[0][1]
        assert engine_called == []               # لا عبر محرّك الإشعارات
        alerts = repo.list_alerts(1, device_id=did)
        assert any(a.get("status") == "sent" and a.get("alert_type") == "down"
                   for a in alerts)
        rows = _panel_rows()                      # لا تلميح «تلجرام غير مُفعّل»
        down = [n for n in rows if "انقطاع" in (n.get("title") or "")]
        assert down and "تلجرام غير" not in (down[0].get("body") or "")


def test_telegram_ready_reflects_canonical_store(app):
    with app.app_context():
        import app.radius.services.device_health_alerts as dha
        assert dha.telegram_ready(1) is False      # لا شيء مُهيّأ ⇒ الشريط يظهر
        _configure_canonical_telegram()
        assert dha.telegram_ready(1) is True        # بعد التهيئة ⇒ لا شريط زورًا


def test_poller_alert_hook_wires_to_panel(app):
    """يثبّت ربط العامل: الدالة التي يستدعيها المُستطلِع (_default_alert_fn)
    تمرّ فعلًا إلى evaluate_and_dispatch فيظهر الإشعار في الجرس."""
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo, notifications_repo
        did = _make_device(alert_channel="")
        fresh = repo.get_device(1, did)
        before = notifications_repo.unread_count(1)
        poller._default_alert_fn(
            tenant_id=1, device=fresh, prev_status="up",
            new_status="down", latency_ms=None)
        assert notifications_repo.unread_count(1) > before
