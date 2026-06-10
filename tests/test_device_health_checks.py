"""device_health — سجل الفحوصات + الإحصائيات + إعدادات الفحص الدوري (يونيو 2026).

يغطي «الإدارة الاحترافية» المضافة للصفحة:
  • كل دورة فحص (tick بمصدر) تُدوَّن في network_device_health_checks
    بملخّصها وتفاصيل كل جهاز ومدتها.
  • stats() يلخّص آخر 24 ساعة + آخر فحص.
  • إعدادات الفحص الدوري (tenant_settings) تُقرأ وتُحفظ عبر API الصفحة
    ويحترمها worker الخلفية (_poll_due).
  • الصفحة تعرض قسمَي «سجل الفحوصات» و«الفحص الدوري التلقائي».

Run individually:  pytest tests/test_device_health_checks.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_device_health_checks_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_router(app, router_id: int = 11) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction

        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as conn:
            conn.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password, created_at)
                   VALUES (?, 1, 'راوتر الاختبار', '10.0.0.1', 'secret',
                           'mikrotik', 'hotspot', 1, 'api', 'pw', ?)""",
                (router_id, now),
            )


def _device(app, ip="192.168.15.10", name="AP", iface="ether2"):
    with app.app_context():
        from app.radius.services import device_health as svc
        return svc.create_device(1, {
            "router_id": 11, "name": name, "interface_name": iface,
            "ip_address": ip})["device_id"]


class _FakeMt:
    def __init__(self, netwatch=None, ping_rows=None):
        self._nw = netwatch or []
        self._ping_rows = ping_rows or [{"time": "5ms"}]

    def read_netwatch(self, nas):
        return SimpleNamespace(ok=True, data=self._nw, error="")

    def ping(self, nas, target, count=4):
        return SimpleNamespace(ok=True, data=self._ping_rows, error="")


def _no_alerts(**kwargs):
    return []


CSRF = "dh-checks-csrf"


def _login(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "tester"
        s["admin_name"] = "Tester"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["permissions"] = []
        s["_csrf_token"] = CSRF


def _hdr():
    return {"X-CSRFToken": CSRF, "Content-Type": "application/json"}


# ─── سجل الفحوصات من tick ─────────────────────────────────────────


def test_tick_with_log_source_records_check(app):
    """tick(log_source=…) يدوّن الدورة: الملخّص + تفاصيل كل جهاز + المدة."""
    _seed_router(app)
    _device(app, ip="192.168.15.10", name="AP-1")
    _device(app, ip="192.168.20.11", name="AP-2", iface="ether3")
    with app.app_context():
        from app.radius.db.repos import device_health_checks_repo as checks
        from app.radius.services import device_health_poller as poller

        mt = _FakeMt(netwatch=[{"host": "192.168.15.10", "status": "up"}],
                     ping_rows=[{"time": "5ms"}])
        poller.tick(tenant_id=1, mt=mt, alert_fn=_no_alerts,
                    log_source="manual")
        rows = checks.list_checks(1)
        assert len(rows) == 1
        c = rows[0]
        assert c["source"] == "manual" and c["ok"] == 1
        assert c["scanned"] == 2
        assert c["up_count"] == 2  # كلاهما يرد على البنج (FakeMt)
        assert {d["name"] for d in c["details"]} == {"AP-1", "AP-2"}
        assert all("status" in d for d in c["details"])


def test_tick_without_log_source_records_nothing(app):
    """الاستدعاءات القديمة (بلا log_source) لا تكتب في السجل — لا تغيير
    في سلوكها."""
    _seed_router(app)
    _device(app)
    with app.app_context():
        from app.radius.db.repos import device_health_checks_repo as checks
        from app.radius.services import device_health_poller as poller

        poller.tick(tenant_id=1, mt=_FakeMt(), alert_fn=_no_alerts)
        assert checks.list_checks(1) == []


def test_checks_stats_summary(app):
    """stats() يلخّص فحوصات آخر 24 ساعة + آخر فحص (وقت/مصدر)."""
    with app.app_context():
        from app.radius.db.repos import device_health_checks_repo as checks

        checks.insert_check(
            tenant_id=1, source="manual",
            summary={"scanned": 3, "up": 2, "down": 1, "changed": 1,
                     "alerts": 1},
            details=[{"device_id": 1, "name": "AP", "status": "down",
                      "latency_ms": None}])
        checks.insert_check(
            tenant_id=1, source="poller",
            summary={"scanned": 3, "up": 3, "down": 0})
        s = checks.stats(1)
        assert s["checks"] == 2
        assert s["downs"] == 1
        assert s["changes"] == 1
        assert s["alerts"] == 1
        assert s["last_source"] == "poller"
        assert s["last_at"]


# ─── API: السجل + إعدادات الفحص الدوري ────────────────────────────


def test_checks_api_returns_rows_and_stats(app, client):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import device_health_checks_repo as checks
        checks.insert_check(tenant_id=1, source="manual",
                            summary={"scanned": 1, "up": 1})
    _login(client)
    res = client.get("/admin/radius/device-health/api/checks")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert len(data["checks"]) == 1
    assert data["stats"]["checks"] == 1


def test_poll_settings_roundtrip_and_worker_reads_them(app, client):
    """GET يعيد الافتراضي (مفعّل/5 دقائق)، POST يحفظ، وworker الخلفية
    يقرأ نفس القيم ويحترم التعطيل والفترة في _poll_due."""
    _login(client)
    res = client.get("/admin/radius/device-health/api/poll-settings")
    assert res.status_code == 200
    d = res.get_json()
    assert d["ok"] is True and d["enabled"] is True and d["minutes"] == 5

    res2 = client.post("/admin/radius/device-health/api/poll-settings",
                       json={"enabled": False, "minutes": 30},
                       headers=_hdr())
    assert res2.status_code == 200
    d2 = res2.get_json()
    assert d2["enabled"] is False and d2["minutes"] == 30

    with app.app_context():
        from app.workers import device_health_poll_worker as worker
        settings = worker.poll_settings(1)
        assert settings == {"enabled": False, "minutes": 30}
        # معطَّل ⇒ غير مستحق
        assert worker._poll_due(1, settings) is False
        # مفعَّل بلا سجل دوري سابق ⇒ مستحق
        assert worker._poll_due(1, {"enabled": True, "minutes": 30}) is True
        # فحص دوري للتو ⇒ غير مستحق قبل انقضاء الفترة
        from app.radius.db.repos import device_health_checks_repo as checks
        checks.insert_check(tenant_id=1, source="poller",
                            summary={"scanned": 0})
        assert worker._poll_due(1, {"enabled": True, "minutes": 30}) is False


def test_poll_worker_once_runs_due_tenant_and_logs(app, monkeypatch):
    """poll_once: مستأجر لديه أجهزة مُراقَبة وفترته مستحقة ⇒ يفحصه ويدوّن
    دورة source=poller في السجل."""
    _seed_router(app)
    _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        from app.workers import device_health_poll_worker as worker

        # نوجّه tick إلى عميل وهمي حتى لا يحاول الاتصال براوتر حقيقي.
        real_tick = poller.tick

        def fake_tick(tenant_id=None, log_source=""):
            return real_tick(tenant_id=tenant_id, mt=_FakeMt(),
                             alert_fn=_no_alerts, log_source=log_source)

        monkeypatch.setattr(poller, "tick", fake_tick)
        stats = worker.poll_once()
        assert stats["tenants"] == 1
        assert stats["polled"] == 1
        assert stats["scanned"] == 1

        from app.radius.db.repos import device_health_checks_repo as checks
        rows = checks.list_checks(1)
        assert len(rows) == 1 and rows[0]["source"] == "poller"
        # الدورة الثانية فورًا: الفترة لم تنقضِ ⇒ يتخطّى
        stats2 = worker.poll_once()
        assert stats2["polled"] == 0 and stats2["not_due"] == 1


def test_manual_poll_route_logs_check(app, client, monkeypatch):
    """زر «فحص الكل» (غير المتدفق) يدوّن الدورة source=manual."""
    _seed_router(app)
    _device(app)
    with app.app_context():
        from app.radius.services import device_health_poller as poller
        real_tick = poller.tick

        def fake_tick(tenant_id=None, log_source=""):
            return real_tick(tenant_id=tenant_id, mt=_FakeMt(),
                             alert_fn=_no_alerts, log_source=log_source)

        monkeypatch.setattr(poller, "tick", fake_tick)
        _login(client)
        res = client.post("/admin/radius/device-health/api/poll",
                          json={}, headers=_hdr())
        assert res.status_code == 200

        from app.radius.db.repos import device_health_checks_repo as checks
        rows = checks.list_checks(1)
        assert len(rows) == 1 and rows[0]["source"] == "manual"


# ─── الصفحة تعرض الأقسام الجديدة ──────────────────────────────────


def test_page_renders_checks_log_and_poll_settings(app, client):
    _seed_router(app)
    with app.app_context():
        from app.radius.db.repos import device_health_checks_repo as checks
        checks.insert_check(tenant_id=1, source="poller",
                            summary={"scanned": 2, "up": 1, "down": 1},
                            details=[{"device_id": 1, "name": "AP",
                                      "status": "down", "latency_ms": None}])
    _login(client)
    res = client.get("/admin/radius/device-health")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # قسم الإعدادات الدورية بعناصره
    assert "الفحص الدوري التلقائي" in body
    assert 'id="dh-poll-minutes"' in body
    assert 'id="dh-poll-save"' in body
    # قسم سجل الفحوصات + صفّ الدورة المبذورة + زر «المزيد»
    assert "سجل الفحوصات" in body
    assert 'id="dh-checks-table"' in body
    assert "data-dh-check-details" in body
    # شريط تقدم تركيب الإعدادات داخل نافذة الإضافة
    assert 'id="dh-install-progress"' in body
