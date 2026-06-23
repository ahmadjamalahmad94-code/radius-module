# -*- coding: utf-8 -*-
"""الإشعارات الدوريّة للمراقبة (monitoring_digest) — تذكير + تقرير أسطول.

يثبّت:
  • التذكير يُعاد إرساله بعد الفترة فقط (لا كل دورة) ويتوقّف عند التعافي.
  • التقرير الدوريّ يُرسَل على فترته، بمحتوى «كل شيء سليم» مقابل تقرير منظّم
    (أعداد + أسماء + نقطة الضعف المحدّدة CPU/حرارة/بنج).
  • الفترتان + تشغيل/إيقاف قابلة للضبط والخَنق يحترمها.
طبقة تلجرام مُموّهة (device_health_alerts._send).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_mon_digest_")
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


def _capture(monkeypatch):
    sent: list = []
    from app.radius.services import device_health_alerts as dha
    monkeypatch.setattr(dha, "_send",
                        lambda tid, ch, atype, msg, dev, lat: (sent.append((atype, msg)) or (True, "sent")))
    return sent


def _router(name="ccr3", status="reachable") -> int:
    from app.radius.core.types import NasDevice
    from app.radius.db.repos import nas_repo
    from app.radius.db.connection import db
    nas = nas_repo.upsert_nas(NasDevice(
        id=None, name=name, address="10.0.0.1", secret="s",
        vendor="mikrotik", enabled=True))
    db().execute("UPDATE nas_devices SET last_check_status=? WHERE id=?",
                 (status, int(nas.id)))
    db().commit()
    return int(nas.id)


def _device(router_id, name="test", ip="192.168.15.10", status="up") -> int:
    from app.radius.db.repos import device_health_repo as repo
    did = repo.create_device(
        tenant_id=1, router_id=int(router_id), name=name, interface_name="",
        ip_address=ip, network_cidr="192.168.15.0/24",
        gateway_address="192.168.15.1", device_type="access_point")
    repo.set_status(tenant_id=1, device_id=did, status=status)
    return did


# ───────────────────── reminder ─────────────────────

def test_reminder_resends_after_interval_not_every_tick(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        sent = _capture(monkeypatch)
        rid = _router(status="reachable")
        _device(rid, name="test", status="down")
        t0 = dt.datetime.utcnow()
        md.reminder_sweep(1, now=t0)                              # seed، لا إرسال
        assert sent == []
        md.reminder_sweep(1, now=t0 + dt.timedelta(minutes=10))  # ضمن النافذة
        assert sent == []
        md.reminder_sweep(1, now=t0 + dt.timedelta(minutes=31))  # بعد الفترة (30د)
        assert len(sent) == 1
        atype, msg = sent[0]
        assert atype == "reminder_down" and "ما زال" in msg and "test" in msg


def test_reminder_stops_on_recovery(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        from app.radius.db.repos import device_health_repo as repo, monitoring_notify_repo as nrepo
        sent = _capture(monkeypatch)
        rid = _router(status="reachable")
        did = _device(rid, name="test", status="down")
        t0 = dt.datetime.utcnow()
        md.reminder_sweep(1, now=t0 + dt.timedelta(minutes=31))   # تذكير 1
        assert len(sent) == 1
        repo.set_status(tenant_id=1, device_id=did, status="up")  # تعافى
        md.reminder_sweep(1, now=t0 + dt.timedelta(minutes=62))
        assert len(sent) == 1                                     # لا تذكير بعد التعافي
        assert nrepo.get(1, f"reminder:device:{did}") is None     # مُسح صفّ الحلقة


def test_reminder_covers_routers_too(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        sent = _capture(monkeypatch)
        _router(name="ccr3", status="unreachable")               # راوتر مفصول
        t0 = dt.datetime.utcnow()
        md.reminder_sweep(1, now=t0)
        md.reminder_sweep(1, now=t0 + dt.timedelta(minutes=31))
        assert len(sent) == 1 and "الراوتر «ccr3»" in sent[0][1]


def test_reminder_disabled_no_send(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        sent = _capture(monkeypatch)
        rid = _router()
        _device(rid, name="test", status="down")
        md.set_periodic_config(1, {"reminder_enabled": False})
        md.reminder_sweep(1, now=dt.datetime.utcnow() + dt.timedelta(hours=3))
        assert sent == []


# ───────────────────── digest ─────────────────────

def test_digest_all_good(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        sent = _capture(monkeypatch)
        rid = _router(status="reachable")
        _device(rid, name="cam-1", status="up")
        n = md.digest_sweep(1, now=dt.datetime.utcnow())
        assert n == 1 and sent[0][0] == "fleet_digest_ok"
        assert "كل الأجهزة والراوترات سليمة" in sent[0][1]


def test_digest_issues_has_counts_names_and_weakness(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        from app.radius.db.repos import router_resource_repo as rr
        sent = _capture(monkeypatch)
        rid_down = _router(name="ccr3", status="unreachable")     # مفصول
        rid_weak = _router(name="rb-1", status="reachable")       # متصل لكن ضعيف
        rr.insert_sample(1, rid_weak, sample={
            "ok": 1, "cpu_load": 91, "mem_used_pct": 40.0,
            "disk_free_pct": 55.0, "temperature_c": 50.0})        # CPU 91% > 85
        _device(rid_weak, name="cam-3", ip="192.168.15.30", status="high_latency")
        from app.radius.db.repos import device_health_repo as repo
        # اضبط بنجًا للجهاز عالي البنج.
        repo.set_status(tenant_id=1, device_id=_device(rid_weak, name="t2", ip="192.168.15.40", status="up"), status="up")
        _device(rid_weak, name="ok-dev", ip="192.168.15.50", status="up")
        n = md.digest_sweep(1, now=dt.datetime.utcnow())
        assert n == 1 and sent[0][0] == "fleet_digest_issues"
        msg = sent[0][1]
        assert "تقرير الفحص الدوري" in msg
        assert "🔴 مفصول:" in msg and "ccr3" in msg
        assert "🟠 ضعف موارد:" in msg and "rb-1" in msg and "المعالج 91%" in msg
        assert "🐌 بنج عالٍ:" in msg and "cam-3" in msg
        assert "✅ سليم:" in msg


def test_digest_throttled_to_interval(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        sent = _capture(monkeypatch)
        rid = _router(status="reachable")
        _device(rid, name="cam-1", status="up")
        t0 = dt.datetime.utcnow()
        assert md.digest_sweep(1, now=t0) == 1                    # أول مرّة
        assert md.digest_sweep(1, now=t0 + dt.timedelta(minutes=10)) == 0  # ضمن 60د
        assert md.digest_sweep(1, now=t0 + dt.timedelta(minutes=61)) == 1  # بعد الفترة
        assert len(sent) == 2


def test_digest_disabled_no_send(app, monkeypatch):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        sent = _capture(monkeypatch)
        rid = _router(status="reachable")
        _device(rid, name="cam-1", status="up")
        md.set_periodic_config(1, {"digest_enabled": False})
        assert md.digest_sweep(1, now=dt.datetime.utcnow()) == 0 and sent == []


# ───────────────────── config ─────────────────────

def test_config_persists_and_clamps(app):
    with app.app_context():
        from app.radius.services import monitoring_digest as md
        md.set_periodic_config(1, {"reminder_enabled": False, "reminder_minutes": 15,
                                   "digest_enabled": True, "digest_minutes": 120})
        c = md.get_periodic_config(1)
        assert c["reminder_enabled"] is False and c["reminder_minutes"] == 15
        assert c["digest_enabled"] is True and c["digest_minutes"] == 120
