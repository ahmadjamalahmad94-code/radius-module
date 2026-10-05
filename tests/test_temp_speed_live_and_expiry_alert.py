# -*- coding: utf-8 -*-
"""السرعة المؤقتة على كرت هوت سبوت (client20 2026-10-05).

1. «سرعة مؤقتة» ⇒ CoA بمفاتيح radacct رفضه الراوتر ⇒ سقط على الفصل فطُرد
   الكرت لصفحة الدخول. تغيير السرعة صار يسأل الراوتر أوّلًا (مثل الفصل).
2. «خلص الوقت المؤقت وما جاب إشعار» ⇒ تنبيه إدارة «speed_boost_ended».
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_ts_live_")
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


def test_rate_change_uses_the_live_session_keys_from_the_router(app, monkeypatch):
    with app.app_context():
        from app.radius.integration import radius_coa
        from app.radius.services import mikrotik_active_reconciler as mar

        live = mar.LiveSession(tenant_id=1, router_id=17, nas_address="10.50.0.2",
                               username="79876297", framed_ip_address="10.30.31.248",
                               calling_station_id="7E:AD:69:17:1F:C2",
                               acct_session_id="", nas_secret="s3cret",
                               coa_dial_ip="10.50.0.2", coa_port=3799)
        monkeypatch.setattr(mar.MikroTikActiveSessionReconciler, "resolve_disconnect_targets",
                            lambda self, u, **k: mar.ReconcileOutcome(
                                sessions=[live], routers_queried=1, routers_reachable=1))
        legacy = []
        monkeypatch.setattr(radius_coa, "find_all_nas_for_sessions",
                            lambda *a, **k: legacy.append(1) or [])
        sent = []

        def fake_send_coa(**kw):
            sent.append(kw)
            return radius_coa.CoaResult(ok=True, code=44, code_name="CoA-ACK")
        monkeypatch.setattr(radius_coa, "send_coa", fake_send_coa)

        res = radius_coa.change_user_rate(1, "79876297", new_rate_limit="2560k/2560k")
        assert res.ok
        assert len(sent) == 1
        assert sent[0]["framed_ip"] == "10.30.31.248"
        assert sent[0]["calling_station_id"] == "7E:AD:69:17:1F:C2"
        assert sent[0]["nas_ip"] == "10.50.0.2"
        assert legacy == []          # no stale radacct keys used


def test_expiry_fires_the_admin_alert(app, monkeypatch):
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO subscribers(tenant_id,username,password,status,created_at,"
                "temporary_speed,bandwidth_control_enabled,download_speed_kbps,"
                "upload_speed_kbps,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (1, "79876297", "pw", "enabled", now, 1, 1, 2560, 2560, "{}"),
            )
        from app.radius.services import admin_alerts
        assert admin_alerts.get_spec("speed_boost_ended") is not None
        fired = []
        monkeypatch.setattr(admin_alerts, "dispatch",
                            lambda t, key, ctx=None, **k: fired.append((key, ctx)) or True)
        from app.radius.services.temp_speed import expire_due_temp_speeds
        assert expire_due_temp_speeds(tenant_id=1) == 1
        keys = [k for k, _ in fired]
        assert "speed_boost_ended" in keys
        ctx = dict(fired[keys.index("speed_boost_ended")][1])
        assert ctx["username"] == "79876297"
