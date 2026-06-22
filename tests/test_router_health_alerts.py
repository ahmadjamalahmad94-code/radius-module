# -*- coding: utf-8 -*-
"""تغطية تنبيهات الراوترات (NAS) + محتوى التنبيهات الموحّد.

يثبّت:
  • COVERAGE: انتقال راوتر متصل→غير متصل (حالة ccr3) يُرسل تنبيه تلجرام عبر
    المُرسِل القانوني (telegram_notifier.send_to_tenant) ويكتب الجرس — دون
    بوّابة notif.*.enabled. والعودة غير متصل→متصل كذلك.
  • لا إنذار كاذب بلا أساس معروف، ولا تكرار حين لا تتغيّر الحالة.
  • CONTENT: «عاد الاتصال» للجهاز يحمل «الوقت» (كان ناقصاً)، وكل التنبيهات
    تحمل الاسم + الوصف + الوقت.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_router_alert_")
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
        from app.radius.db.repos import tenants_repo, tenant_telegram_settings_repo
        tenants_repo.ensure_default_tenant()
        # تلجرام مُهيّأ في المتجر القانوني (نفس صفحة «تنبيهات تلجرام»).
        tenant_telegram_settings_repo.upsert(
            tenant_id=1, bot_token="123:ABC", chat_id="-100", enabled=True,
            thread_id="")
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _router(*, name="ccr3", address="192.168.15.1", description="راوتر المبنى الرئيسي",
            last_check_status="", vpn_peer="", api_port=8728) -> int:
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    cur = db().execute(
        "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
        "api_port, description, enabled, connection_mode, vpn_peer_address, "
        "last_check_status, created_at, updated_at) "
        "VALUES(1,?,?,'s','mikrotik',?,?,1,?,?,?,?,?)",
        (name, address, api_port, description,
         "vpn" if vpn_peer else "direct", vpn_peer, last_check_status,
         now_iso(), now_iso()))
    return int(cur.lastrowid)


def _capture_telegram(monkeypatch):
    """يحاكي طبقة HTTP لتلجرام ويلتقط الرسائل (tid, text)."""
    sent: list = []
    import app.radius.services.telegram_notifier as tn
    monkeypatch.setattr(tn, "send_to_tenant",
                        lambda tid, text: (sent.append((tid, text)) or (True, "")))
    return sent


def _bell_rows():
    from app.radius.db.repos import notifications_repo
    return notifications_repo.list_for(1, limit=50)


# ───────────────────── COVERAGE: router offline/online ─────────────────────

def test_router_offline_transition_alerts_via_canonical_sender_and_bell(app, monkeypatch):
    """ccr3 كان متصلاً (reachable) ثم صار غير متصل ⇒ تنبيه تلجرام + جرس."""
    with app.app_context():
        from app.radius.services import router_health_monitor as rhm
        sent = _capture_telegram(monkeypatch)
        nid = _router(name="ccr3", address="192.168.15.1",
                      description="راوتر المبنى الرئيسي", last_check_status="reachable")
        stats = rhm.sweep_once(1, probe=lambda addr, port: "unreachable")
        assert stats["alerts"] == 1 and stats["offline"] == 1
        # أُرسل عبر المُرسِل القانوني.
        assert len(sent) == 1
        msg = sent[0][1]
        assert "غير متصل" in msg and "ccr3" in msg
        assert "العنوان: 192.168.15.1" in msg
        assert "الوصف: راوتر المبنى الرئيسي" in msg
        assert "الوقت:" in msg
        # كُتب في الجرس.
        assert any("ccr3" in (n.get("title") or "") for n in _bell_rows())
        # الحالة الجديدة سُجّلت.
        from app.radius.db.repos import nas_repo
        assert nas_repo.get_nas(1, nid) is not None


def test_router_reconnect_transition_alerts_with_timestamp(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_health_monitor as rhm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3", description="الموزّع الرئيسي", last_check_status="unreachable")
        rhm.sweep_once(1, probe=lambda addr, port: "reachable")
        assert len(sent) == 1
        msg = sent[0][1]
        assert "عاد اتصال الراوتر" in msg and "ccr3" in msg
        assert "الوصف: الموزّع الرئيسي" in msg
        assert "الوقت:" in msg


def test_router_offline_matches_tunnel_address(app, monkeypatch):
    """راوتر عبر نفق: يُفحَص عنوان النفق المُحلّ، ويُذكر في الرسالة."""
    with app.app_context():
        from app.radius.services import router_health_monitor as rhm
        sent = _capture_telegram(monkeypatch)
        captured_addr = []
        _router(name="ccr3", address="41.public.1.2", vpn_peer="10.10.0.9",
                last_check_status="reachable")
        def probe(addr, port):
            captured_addr.append(addr)
            return "unreachable"
        rhm.sweep_once(1, probe=probe)
        assert captured_addr == ["10.10.0.9"]      # فُحص عنوان النفق لا العام
        assert "العنوان: 10.10.0.9" in sent[0][1]


def test_no_alert_without_known_baseline(app, monkeypatch):
    """راوتر بلا حالة سابقة معروفة ⇒ نُسجّل فقط، لا إنذار كاذب."""
    with app.app_context():
        from app.radius.services import router_health_monitor as rhm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3", last_check_status="")     # لا أساس
        stats = rhm.sweep_once(1, probe=lambda addr, port: "unreachable")
        assert sent == [] and stats["alerts"] == 0


def test_no_duplicate_when_status_unchanged(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_health_monitor as rhm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3", last_check_status="reachable")
        rhm.sweep_once(1, probe=lambda addr, port: "reachable")   # متصل→متصل
        assert sent == []


def test_disabled_router_not_swept(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_health_monitor as rhm
        from app.radius.db.connection import db
        sent = _capture_telegram(monkeypatch)
        nid = _router(name="ccr3", last_check_status="reachable")
        db().execute("UPDATE nas_devices SET enabled=0 WHERE id=?", (nid,))
        stats = rhm.sweep_once(1, probe=lambda addr, port: "unreachable")
        assert stats["checked"] == 0 and sent == []


# ───────────────────── CONTENT: device messages ─────────────────────

def test_device_recovery_message_now_has_timestamp_and_description(app, monkeypatch):
    """جوهر إصلاح المحتوى: «عاد الاتصال» للجهاز يحمل «الوقت» (كان ناقصاً) +
    «الوصف» + الاسم + العنوان."""
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo, nas_repo
        from app.radius.core.types import NasDevice
        sent = _capture_telegram(monkeypatch)
        nas = nas_repo.upsert_nas(NasDevice(
            id=None, name="RB", address="10.0.0.1", secret="s",
            vendor="mikrotik", enabled=True))
        did = repo.create_device(
            tenant_id=1, router_id=int(nas.id), name="test",
            interface_name="", ip_address="192.168.15.10",
            network_cidr="192.168.15.0/24", gateway_address="192.168.15.1",
            device_type="access_point", notes="كاميرا المدخل")
        fresh = repo.get_device(1, did)
        dha.evaluate_and_dispatch(tenant_id=1, device=fresh,
                                  prev_status="down", new_status="up",
                                  latency_ms=12.0)
        assert len(sent) == 1
        msg = sent[0][1]
        assert "عاد الاتصال" in msg and "«test»" in msg
        assert "العنوان: 192.168.15.10" in msg
        assert "الوصف: كاميرا المدخل" in msg
        assert "الوقت:" in msg          # ← الإصلاح: كان ناقصاً
        assert "البنج: 12.0 ms" in msg


def test_formatter_omits_empty_fields(app):
    with app.app_context():
        from app.radius.services.device_health_alerts import format_alert_message
        msg = format_alert_message("down", name="x", ip="", description="", when="2026-06-22 20:03")
        assert "العنوان:" not in msg and "الوصف:" not in msg and "البنج:" not in msg
        assert "الوقت: 2026-06-22 20:03" in msg
