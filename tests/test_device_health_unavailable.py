# -*- coding: utf-8 -*-
"""حالة «غير متاح» للجهاز خلف راوتر مفصول (device-health).

العَرَض (المالك): جهاز «test» (192.168.15.10) خلف راوتر ccr3 المفصول كان يبقى
«غير معروف» (unknown) بلا أيّ تنبيه (تنبيه الانقطاع يَشترط حالة down)، وملخّص
الصفحة يَعرضه «سليماً» (المتصلة 0 / المفصولة 0) — تناقض.

يثبّت الإصلاح:
  • تعذّر الفحص لأنّ الراوتر الأمّ مفصول ⇒ حالة «unavailable» لا «unknown» الصامت.
  • «unavailable» يُراكِم عدّاد الانقطاع ويُطلق تنبيه «غير متاح» عبر المسار
    القانوني (تلجرام + الجرس) بعد العتبة، مع ضدّ-تكرار.
  • الملخّص يَحسب unavailable/unknown «تحتاج انتباه» (healthy=False)، وجهاز
    سليم يبقى up/healthy.
  • سجل الفحوصات يَعدّ «غير متاح» مشكلة (لا «سليم» كاذب).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_dh_unavail_")
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


class _Res:
    def __init__(self, ok, data=None, error=""):
        self.ok, self.data, self.error = ok, data, error


class FakeMtDown:
    """يحاكي راوتراً أمّاً مفصولاً: لا ping ولا netwatch."""
    def ping(self, nas, target, count=3):
        return _Res(False, error="connect failure — router unreachable")

    def read_netwatch(self, nas):
        return _Res(False, error="connect failure — router unreachable")


def _router(name="ccr3", address="192.168.15.1") -> int:
    from app.radius.core.types import NasDevice
    from app.radius.db.repos import nas_repo
    nas = nas_repo.upsert_nas(NasDevice(
        id=None, name=name, address=address, secret="s",
        vendor="mikrotik", enabled=True))
    return int(nas.id)


def _device(router_id, name="test", ip="192.168.15.10") -> int:
    from app.radius.db.repos import device_health_repo as repo
    return repo.create_device(
        tenant_id=1, router_id=int(router_id), name=name,
        interface_name="", ip_address=ip,
        network_cidr="192.168.15.0/24", gateway_address="192.168.15.1",
        device_type="access_point", alert_channel="")


def _capture_send(monkeypatch):
    sent: list = []
    from app.radius.services import device_health_alerts as dha
    monkeypatch.setattr(dha, "_send",
                        lambda tid, ch, atype, msg, dev, lat: (sent.append((atype, msg)) or (True, "sent")))
    return sent


# ───────────────────── probe + status model ─────────────────────

def test_probe_returns_unavailable_when_router_unreachable(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        device = {"ip_address": "192.168.15.10", "ping_threshold_ms": 80}
        nas = {"id": 1, "address": "192.168.15.1", "api_port": 8728}
        probe = svc.probe_reachability(device, nas, mt=FakeMtDown())
        assert probe["status"] == "unavailable"     # لا «unknown» الصامت


def test_unavailable_increments_down_counter(app):
    with app.app_context():
        from app.radius.db.repos import device_health_repo as repo
        did = _device(_router())
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")
        d = repo.get_device(1, did)
        assert d["status"] == "unavailable"
        assert d["consecutive_down_count"] == 2      # يُعامَل كـ«انقطاع» للعتبة


# ───────────────────── alert dispatch ─────────────────────

def test_unavailable_alerts_after_threshold_and_surfaces_bell(app, monkeypatch):
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo, notifications_repo
        sent = _capture_send(monkeypatch)
        did = _device(_router(), name="test")
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")  # count=2
        fresh = repo.get_device(1, did)
        fired = dha.evaluate_and_dispatch(
            tenant_id=1, device=fresh, prev_status="up", new_status="unavailable")
        assert "unavailable" in fired
        assert any("غير متاح" in m and "test" in m for _, m in sent)
        # الجرس (panel_notifications) يحمل الحدث دائماً (لا إسقاط صامت).
        assert any("غير متاح" in (n.get("title") or "")
                   for n in notifications_repo.list_for(1, limit=50))


def test_unavailable_below_threshold_is_silent(app, monkeypatch):
    """أوّل عيّنة «غير متاح» (count=1 < DOWN_AFTER_N) لا تُنبّه — لا إزعاج لجهاز
    جديد غير مُهيّأ أو وميض عابر."""
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo
        sent = _capture_send(monkeypatch)
        did = _device(_router())
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")  # count=1
        fresh = repo.get_device(1, did)
        fired = dha.evaluate_and_dispatch(
            tenant_id=1, device=fresh, prev_status="unknown", new_status="unavailable")
        assert fired == [] and sent == []


def test_unavailable_no_repeat_within_cooldown(app, monkeypatch):
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo
        sent = _capture_send(monkeypatch)
        did = _device(_router())
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")
        fresh = repo.get_device(1, did)
        dha.evaluate_and_dispatch(tenant_id=1, device=fresh,
                                  prev_status="up", new_status="unavailable")
        # عيّنة ثالثة «غير متاح» ⇒ لا تكرار ضمن نافذة التهدئة.
        repo.set_status(tenant_id=1, device_id=did, status="unavailable")
        fresh = repo.get_device(1, did)
        dha.evaluate_and_dispatch(tenant_id=1, device=fresh,
                                  prev_status="unavailable", new_status="unavailable")
        assert len(sent) == 1


def test_recovery_from_unavailable(app, monkeypatch):
    with app.app_context():
        from app.radius.services import device_health_alerts as dha
        from app.radius.db.repos import device_health_repo as repo
        sent = _capture_send(monkeypatch)
        did = _device(_router())
        repo.set_status(tenant_id=1, device_id=did, status="up")
        fresh = repo.get_device(1, did)
        fired = dha.evaluate_and_dispatch(
            tenant_id=1, device=fresh, prev_status="unavailable", new_status="up")
        assert "recovery" in fired
        assert any("عاد الاتصال" in m for _, m in sent)


# ───────────────────── summary / لا «سليم» كاذب ─────────────────────

def test_summary_counts_unavailable_and_unknown_as_attention(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.db.repos import device_health_repo as repo
        rid = _router()
        d1 = _device(rid, name="test", ip="192.168.15.10")
        d2 = _device(rid, name="new", ip="192.168.15.11")   # يبقى unknown (لم يُفحَص)
        repo.set_status(tenant_id=1, device_id=d1, status="unavailable")
        s = svc.summary(1)
        assert s["unavailable"] == 1 and s["unknown"] == 1
        assert s["attention"] == 2 and s["healthy"] is False
        assert s["up"] == 0 and s["down"] == 0          # لا يُحسبان «متصل/مفصول»


def test_summary_healthy_when_all_up(app):
    with app.app_context():
        from app.radius.services import device_health as svc
        from app.radius.db.repos import device_health_repo as repo
        did = _device(_router())
        repo.set_status(tenant_id=1, device_id=did, status="up")
        s = svc.summary(1)
        assert s["up"] == 1 and s["attention"] == 0 and s["healthy"] is True


# ───────────────────── end-to-end poller ─────────────────────

def test_poller_marks_unavailable_and_alerts(app, monkeypatch):
    """جهاز خلف راوتر مفصول: دورتان ⇒ الحالة «unavailable» (لا unknown) + تنبيه."""
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_repo as repo
        sent = _capture_send(monkeypatch)
        did = _device(_router(), name="test")
        for _ in range(2):                              # عيّنتان لتجاوز العتبة
            list(poller.iter_tick(1, mt=FakeMtDown(), log_source="poller"))
        d = repo.get_device(1, did)
        assert d["status"] == "unavailable"
        assert any("غير متاح" in m and "test" in m for _, m in sent)


def test_check_history_counts_unavailable_as_problem(app):
    """سجل الفحوصات: «غير متاح» يُطوى في عمود «المفصول» فلا تَظهر الدورة «سليمة»."""
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.radius.db.repos import device_health_checks_repo as checks
        _device(_router(), name="test")
        list(poller.iter_tick(1, mt=FakeMtDown(), log_source="poller"))
        last = checks.list_checks(1, limit=1)[0]
        assert last["down_count"] >= 1                  # «غير متاح» محسوب مشكلة
