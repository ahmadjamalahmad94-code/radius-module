"""Smart Alerts phase 3 — DHCP-client loop detection.

إعادة تصميم: الكشف صار باستطلاع من جهة اللوحة (loop_probe_poller يقرأ
/ip dhcp-client عبر النفق) بدل آلية /tool fetch + scheduler على الراوتر.
هذه الاختبارات تغطّي مسار التسجيل/التقييم الجديد (record_router_probes)،
واختفاء نقطة الـingest القديمة، وصفحة الحالة المعاد تصميمها.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_loop_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"lp_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="lp-pass",
                             full_name="Loop", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "lp-pass"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _seed_router(rid: int = 77, name: str = "راوتر الفرع") -> int:
    from app.radius.db.connection import transaction
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO nas_devices(id, tenant_id, name, address, secret,
                vendor, nas_type, enabled, created_at, connection_mode)
            VALUES(?,1,?,?,'sek','mikrotik','hotspot',1,?,'direct')
            """,
            (rid, name, f"10.0.0.{rid}", datetime.utcnow().isoformat()),
        )
    return rid


def _row(iface, status, address="", server=""):
    """صفّ /ip dhcp-client كما يعيده عميل الـMT API (موسوم HR-LoopDetect)."""
    return {"comment": f"HR-LoopDetect {iface}", "interface": iface,
            "status": status, "address": address, "dhcp-server": server}


def test_poll_records_probe_and_opens_alert_when_bound(app, client):
    """قراءة dhcp-client (bound + عنوان) → تُخزَّن كلوب + تُفتح auto.router.loop."""
    from app.workers.loop_probe_poller import record_router_probes
    with app.app_context():
        _seed_router(77)
        stats = record_router_probes(1, 77, [
            _row("ether2", "bound", "10.0.0.7/24", "10.0.0.1"),
            _row("ether3", "searching", "", ""),
        ])
        assert stats["recorded"] == 2

        from app.radius.db.repos import alerts_repo, router_loop_probes_repo
        probes = router_loop_probes_repo.list_for_router(1, 77)
        by_iface = {p["interface"]: p for p in probes}
        assert by_iface["ether2"]["last_status"] == "bound"
        assert {p["interface"] for p in probes} == {"ether2", "ether3"}
        open_alerts = {a["dedup_key"]: a for a in alerts_repo.list_open(1)}
        assert "auto.router.loop:77:ether2" in open_alerts
        # the loop IP is surfaced in the alert
        assert "10.0.0.7" in open_alerts["auto.router.loop:77:ether2"]["explanation_ar"]
        # the clean port did NOT raise an alert
        assert "auto.router.loop:77:ether3" not in open_alerts


def test_poll_resolves_when_probe_back_to_searching(app, client):
    from app.workers.loop_probe_poller import record_router_probes
    with app.app_context():
        _seed_router(77)
        record_router_probes(1, 77, [_row("ether2", "bound", "10.0.0.7/24", "10.0.0.1")])
        from app.radius.db.repos import alerts_repo
        assert "auto.router.loop:77:ether2" in {a["dedup_key"] for a in alerts_repo.list_open(1)}

        # next poll: probe back to searching (no lease) → loop cleared
        record_router_probes(1, 77, [_row("ether2", "searching", "", "")])
        assert "auto.router.loop:77:ether2" not in {a["dedup_key"] for a in alerts_repo.list_open(1)}


def test_loop_enabled_routers_reads_pss_state(app, client):
    """الاستطلاع يستهدف فقط الراوترات المُفعَّل عليها loop_detect عبر حالة PSS."""
    from app.workers.loop_probe_poller import _loop_enabled_routers
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "pss.77.loop_detect.enabled", "1")
        tenants_repo.set_setting(1, "pss.77.loop_detect.ports", "ether2,ether3")
        tenants_repo.set_setting(1, "pss.88.loop_detect.enabled", "0")  # off → skip
        tenants_repo.set_setting(1, "pss.99.bt_wifi_block.enabled", "1")  # other svc → skip
        got = dict(_loop_enabled_routers(1))
        assert got == {77: ["ether2", "ether3"]}


def test_old_ingest_endpoint_is_gone(app, client):
    """آلية /tool fetch أُلغيت بالكامل: لم يعد هناك مسار/endpoint للـingest."""
    # the route + endpoint are unregistered (no router-side push path)
    endpoints = {r.endpoint for r in app.url_map.iter_rules()}
    assert "api.v1.router_loop_ingest" not in endpoints
    assert not any(r.rule.endswith("/loop/ingest")
                   for r in app.url_map.iter_rules())
    # and POSTing to it no longer hits a handler (404/405 — never 200)
    res = client.post("/api/v1/routers/77/loop/ingest",
                      json={"probes": [{"interface": "ether2", "status": "bound"}]},
                      headers={"Authorization": "Bearer dev-token-please-change"})
    assert res.status_code in {404, 405}


def test_loop_setup_page_renders(app, client):
    with app.app_context():
        _seed_router(77, name="راوتر الفرع")
    _login(client)
    html = client.get("/admin/radius/alerts/loop-setup").get_data(as_text=True)
    # the page no longer generates a router-side fetch/scheduler script
    assert "/loop/ingest" not in html
    assert "lp-script-body" not in html
    # new design: status + install via port-services + old-scheduler cleanup
    assert "راوتر الفرع" in html                                  # router row
    assert "خدمات المنافذ" in html                                # install entrypoint
    assert "/admin/radius/mt/77/port-services" in html            # per-router link
    assert "hoberadius-loop-probe" in html                        # cleanup command


def test_my_services_tab_has_loop_tracking_tile(app, client):
    """The «خدماتي» tab shows a «تتبّع اللوب» tile that opens the loop page.

    تصميم «صفحة خدمة واحدة لكل خدمة» (يونيو 2026): بطاقة «تتبّع اللوب» صارت
    تفتح صفحتها الواحدة المخصّصة عبر port-services?slug=loop_detect (نفس
    سجلّ/مسارات port_script_services) بدل صفحة alerts/loop-setup المشتركة —
    زرّ واحد = صفحة خدمة واحدة، مطابقة لبقية الخدمات."""
    with app.app_context():
        _seed_router(77, name="راوتر الفرع")
    _login(client)
    html = client.get("/admin/radius/mt/77/dashboard").get_data(as_text=True)
    assert "data-rh-loop-tile" in html                              # the tile
    assert "تتبّع اللوب" in html                                     # title
    assert "كشف اللوب عبر مجس DHCP على منافذ الزبائن" in html        # description
    # links to its own dedicated single-service page (loop_detect), per-router
    assert "/admin/radius/mt/77/port-services?slug=loop_detect" in html
    # no probes for this router yet → غير مفعّل badge
    assert "غير مفعّل" in html
