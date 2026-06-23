# -*- coding: utf-8 -*-
"""مقاييس موارد الراوتر (CPU/حرارة/ذاكرة/قرص/حركة) + تنبيهات العتبات.

يثبّت:
  • التحليل الصحيح لـ/system/resource + /system/health (وحالة CHR بلا حرارة → None).
  • عبور العتبة → تنبيه تلجرام عبر المُرسِل القانوني + جرس، والعودة → تنبيه.
  • hysteresis: لا تكرار كل دورة.
  • لا تنبيه على حسّاس حرارة مفقود.
  • الرسالة فيها الاسم + الوصف + قيمة المقياس + الوقت.
  • العتبات قابلة للضبط وتُحفَظ.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile

import pytest


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_res_metrics_")
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
        tenant_telegram_settings_repo.upsert(
            tenant_id=1, bot_token="123:ABC", chat_id="-100", enabled=True,
            thread_id="")
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


class _R:
    def __init__(self, ok, data):
        self.ok, self.data = ok, data


class FakeMt:
    """يحاكي طبقة RouterOS API (mikrotik_admin_client)."""
    def __init__(self, *, cpu=10, free_mem=800_000_000, total_mem=1_000_000_000,
                 free_hdd=900_000_000, total_hdd=1_000_000_000, temp=45.0,
                 rx=1_000_000, tx=2_000_000, health_empty=False):
        self.kw = dict(cpu=cpu, free_mem=free_mem, total_mem=total_mem,
                       free_hdd=free_hdd, total_hdd=total_hdd, temp=temp,
                       rx=rx, tx=tx, health_empty=health_empty)

    def system_resource(self, nas):
        k = self.kw
        return _R(True, [{
            "cpu-load": str(k["cpu"]), "free-memory": str(k["free_mem"]),
            "total-memory": str(k["total_mem"]), "free-hdd-space": str(k["free_hdd"]),
            "total-hdd-space": str(k["total_hdd"]), "uptime": "1d2h",
            "board-name": "RB5009", "version": "7.15"}])

    def system_health(self, nas):
        if self.kw["health_empty"]:
            return _R(True, [])                      # CHR/x86 — لا حسّاس
        return _R(True, [{"name": "temperature", "value": str(self.kw["temp"])},
                         {"name": "voltage", "value": "24.1"}])

    def interface_list(self, nas):
        k = self.kw
        return _R(True, [{"name": "ether1", "rx-byte": str(k["rx"]), "tx-byte": str(k["tx"])}])


def _router(name="ccr3", description="راوتر المبنى", address="192.168.15.1",
            last_check_status="") -> int:
    from app.radius.db.connection import db
    from app.radius.db.helpers import now_iso
    cur = db().execute(
        "INSERT INTO nas_devices(tenant_id,name,address,secret,vendor,api_port,"
        "description,enabled,connection_mode,vpn_peer_address,last_check_status,"
        "created_at,updated_at) "
        "VALUES(1,?,?,'s','mikrotik',8728,?,1,'direct','',?,?,?)",
        (name, address, description, last_check_status, now_iso(), now_iso()))
    return int(cur.lastrowid)


def _capture_telegram(monkeypatch):
    sent: list = []
    import app.radius.services.telegram_notifier as tn
    monkeypatch.setattr(tn, "send_to_tenant",
                        lambda tid, text: (sent.append((tid, text)) or (True, "")))
    return sent


# ───────────────────── parsing ─────────────────────

def test_collect_parses_resource_and_health(app):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        s = rrm.collect_one({"id": 1}, None, client=FakeMt(cpu=92, temp=65))
        assert s["ok"] == 1 and s["cpu_load"] == 92
        assert s["mem_used_pct"] == 20.0       # (1000-800)/1000
        assert s["disk_free_pct"] == 90.0
        assert s["temperature_c"] == 65.0 and s["voltage"] == 24.1
        assert s["board_name"] == "RB5009" and s["version"] == "7.15"


def test_collect_chr_no_temperature(app):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        s = rrm.collect_one({"id": 1}, None, client=FakeMt(health_empty=True))
        assert s["ok"] == 1 and s["temperature_c"] is None   # لا حسّاس → None


def test_traffic_rate_derived_from_prev(app):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        prev = {"rx_bytes_total": 1_000_000, "tx_bytes_total": 2_000_000,
                "recorded_at": (_dt.datetime.utcnow() - _dt.timedelta(seconds=10)).isoformat() + "Z"}
        rin, rout = rrm._derive_rate(prev, 2_000_000, 3_000_000)
        assert rin == int(8 * 1_000_000 / 10) and rout == int(8 * 1_000_000 / 10)
        # تصفير العدّاد (rx<prev) ⇒ None
        assert rrm._derive_rate(prev, 5, 5) == (None, None)


# ───────────────────── thresholds + alerts ─────────────────────

def test_threshold_cross_alerts_via_canonical_and_bell(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        from app.radius.db.repos import notifications_repo
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3", description="راوتر المبنى")
        stats = rrm.sweep_once(1, client=FakeMt(cpu=92))   # cpu 92 > 85
        assert stats["alerts"] == 1 and len(sent) == 1
        msg = sent[0][1]
        assert "ارتفاع حمل المعالج" in msg and "ccr3" in msg
        assert "الوصف: راوتر المبنى" in msg
        # القيمة معزولة (RTL): نتحقّق من المقياس + القيمة + الحدّ منفصلة.
        assert "المعالج:" in msg and "92%" in msg and "85%" in msg
        assert "الوقت:" in msg
        assert any("المعالج" in (n.get("title") or "") for n in notifications_repo.list_for(1))


def test_recovery_alert_when_back_under(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3")
        rrm.sweep_once(1, client=FakeMt(cpu=92))     # عبور
        rrm.sweep_once(1, client=FakeMt(cpu=40))     # عودة
        assert len(sent) == 2
        assert "ارتفاع حمل المعالج" in sent[0][1]
        assert "عاد المعالج لطبيعته" in sent[1][1]


def test_hysteresis_no_repeat_each_tick(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3")
        rrm.sweep_once(1, client=FakeMt(cpu=92))
        rrm.sweep_once(1, client=FakeMt(cpu=93))     # ما زال متجاوزاً ⇒ لا تكرار
        rrm.sweep_once(1, client=FakeMt(cpu=95))
        assert len(sent) == 1                        # تنبيه واحد فقط عند العبور


def test_no_alert_on_missing_temperature(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3")
        # CHR بلا حرارة + كل شيء طبيعي ⇒ لا تنبيه (لا انهيار على temp None).
        rrm.sweep_once(1, client=FakeMt(cpu=10, health_empty=True))
        assert all("الحرارة" not in t for _, t in sent)


def test_temperature_threshold_cross(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3")
        rrm.sweep_once(1, client=FakeMt(cpu=10, temp=78))   # 78 > 70
        assert any("ارتفاع حرارة الراوتر" in t for _, t in sent)


def test_disk_low_free_alert(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3")
        # حرّ 5% < 10% ⇒ تنبيه انخفاض المساحة.
        rrm.sweep_once(1, client=FakeMt(cpu=10, free_hdd=50_000_000, total_hdd=1_000_000_000))
        assert any("انخفاض مساحة القرص" in t for _, t in sent)


def test_thresholds_persist_and_disabled_suppresses(app, monkeypatch):
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        rrm.set_thresholds(1, {"cpu_pct": 50, "temp_c": 60, "enabled": False})
        th = rrm.get_thresholds(1)
        assert th["cpu_pct"] == 50.0 and th["temp_c"] == 60.0 and th["enabled"] is False
        sent = _capture_telegram(monkeypatch)
        _router(name="ccr3")
        rrm.sweep_once(1, client=FakeMt(cpu=99))     # متجاوز لكن التنبيهات مُعطّلة
        assert sent == []


# ───────────────────── تحصين الكنس ضد الراوتر المُعطّل ─────────────────────

def test_known_down_router_is_not_dialed(app, monkeypatch):
    """راوتر آخر فحص وصول له = unreachable ⇒ كنس الموارد لا يتّصل بـAPI إطلاقاً
    (لا هدر ~3ث ولا قفل مجمّع)، بل يُسجّل عيّنة ok=0 ويتابع. يمنع تباطؤ اللوحة."""
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        import app.radius.services.mikrotik_admin_client as mac
        called = {"n": 0}

        def _boom(nas):  # لو نودي ⇒ فشل الاختبار: المُعطّل يجب ألّا يُتّصل به
            called["n"] += 1
            raise AssertionError("dialed a known-down router")
        monkeypatch.setattr(mac, "system_resource", _boom)
        rid = _router(name="ccr3", last_check_status="unreachable")
        stats = rrm.sweep_once(1)                     # المسار الحقيقي (client=None)
        assert called["n"] == 0 and stats["skipped_down"] == 1
        from app.radius.db.repos import router_resource_repo as repo
        latest = repo.latest(1, rid)
        assert latest and latest["ok"] == 0          # سُجّلت فجوة بلا اتصال


def test_reachable_router_still_dialed(app):
    """ضدّ-حالة: راوتر غير معروف-مُعطّل ⇒ المسار الطبيعي يُجمَع (يثبت أنّ التحصين
    لا يُسكِت الراوترات السليمة)."""
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        rid = _router(name="ok-rtr", last_check_status="reachable")
        stats = rrm.sweep_once(1, client=FakeMt(cpu=20))   # client محقون ⇒ يتجاوز بوّابة المعطّل
        assert stats["skipped_down"] == 0 and stats["ok"] == 1
        from app.radius.db.repos import router_resource_repo as repo
        assert repo.latest(1, rid)["cpu_load"] == 20


# ───────────────────── واجهة (render) ─────────────────────

def _login(app, client) -> None:
    from uuid import uuid4
    with app.app_context():
        from app.radius.db.repos import admins_repo
        u = f"res_{uuid4().hex[:8]}"
        admins_repo.create_admin(username=u, password="p", full_name="T",
                                 is_super_admin=True)
    r = client.post("/admin/radius/login", data={"username": u, "password": "p"})
    assert r.status_code in (302, 303)


def test_dashboard_metrics_card_renders_unavailable(app):
    client = app.test_client()
    with app.app_context():
        rid = _router(name="ccr3")
    _login(app, client)
    html = client.get(f"/admin/radius/mt/{rid}/dashboard").get_data(as_text=True)
    assert "data-mt-resource" in html and "موارد الراوتر" in html
    assert "غير متوفر" in html                 # لا عيّنة بعد ⇒ كل القيم غير متوفّرة


def test_dashboard_metrics_card_renders_values(app):
    client = app.test_client()
    with app.app_context():
        from app.radius.db.repos import router_resource_repo
        rid = _router(name="ccr3")
        router_resource_repo.insert_sample(1, rid, sample={
            "ok": 1, "cpu_load": 77, "mem_used_pct": 55.0, "disk_free_pct": 40.0,
            "temperature_c": None, "board_name": "RB5009", "version": "7.15",
            "traffic_in_bps": 5_000_000, "traffic_out_bps": 1_000_000})
    _login(app, client)
    html = client.get(f"/admin/radius/mt/{rid}/dashboard").get_data(as_text=True)
    assert "77%" in html and "RB5009" in html
    assert "غير متوفر" in html                 # الحرارة None (CHR) ⇒ «غير متوفر»


def _csrf(body: str) -> str:
    import re
    m = re.search(r'name="_csrf_token" value="([^"]+)"', body)
    return m.group(1) if m else ""


def test_thresholds_page_renders_and_saves(app):
    client = app.test_client()
    _login(app, client)
    page = client.get("/admin/radius/alerts/resource-thresholds")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "حدود تنبيهات" in body and 'name="cpu_pct"' in body
    save = client.post("/admin/radius/alerts/resource-thresholds",
                       data={"_csrf_token": _csrf(body), "enabled": "1",
                             "cpu_pct": "75", "temp_c": "65",
                             "ram_pct": "88", "disk_free_pct": "12",
                             "traffic_mbps": "500"})
    assert save.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import router_resource_monitor as rrm
        th = rrm.get_thresholds(1)
        assert th["cpu_pct"] == 75.0 and th["traffic_mbps"] == 500.0
