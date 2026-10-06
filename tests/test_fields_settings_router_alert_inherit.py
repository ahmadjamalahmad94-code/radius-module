# -*- coding: utf-8 -*-
"""Owner 2026-10-06 «وصّله»: per-router alert thresholds are overrides.

blank = inherit the global value (NULL stored), so a later global change
applies to that router; editing one router's limit keeps the others (and the
router's other limits) inheriting; the UI says «يرث العامّ (X)».
Proved through the reader the smart-alert evaluator uses
(``smart_alerts.effective_for_router`` over the stored rows).
"""
from __future__ import annotations

import os
import re
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "router_alert_inherit.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    return flask_app


def _nas(client, address):
    res = client.post("/api/v1/nas", headers=AUTH, json={
        "name": f"ra-{uuid4().hex[:6]}", "address": address,
        "secret": "s3cret-ok", "enabled": False})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["id"]


def _effective(app, rid):
    from app.radius.db.repos import router_alert_settings_repo
    from app.radius.services import smart_alerts
    with app.app_context():
        return smart_alerts.effective_for_router(
            rid, smart_alerts.global_settings(1),
            router_alert_settings_repo.list_for_tenant(1))


def _patch(client, body):
    res = client.patch("/api/v1/router-alerts/settings", headers=AUTH, json=body)
    assert res.status_code == 200, res.get_json()
    return res.get_json()["data"]


def test_blank_inherits_and_follows_later_global_changes(app):
    c = app.test_client()
    a, b = _nas(c, "192.0.2.61"), _nas(c, "192.0.2.62")
    _patch(c, {"settings": {"default_speed_mbps": 100, "default_usage_gb": 200}})
    # A gets its own speed; B is only switched off (all limits blank).
    _patch(c, {"routers": [{"id": a, "normal_speed_mbps": 50},
                           {"id": b, "enabled": False, "normal_speed_mbps": None}]})
    _patch(c, {"settings": {"default_speed_mbps": 300, "default_usage_gb": 900}})
    ea, eb = _effective(app, a), _effective(app, b)
    assert ea["normal_speed_mbps"] == 50 and ea["normal_usage_gb"] == 900
    assert eb["normal_speed_mbps"] == 300 and eb["normal_usage_gb"] == 900
    assert eb["enabled"] is False


def test_editing_one_limit_keeps_the_routers_other_overrides(app):
    c = app.test_client()
    a = _nas(c, "192.0.2.63")
    _patch(c, {"routers": [{"id": a, "enabled": False, "normal_speed_mbps": 50,
                            "offline_after_min": 9}]})
    # A partial edit (old app / script): only the usage limit.
    data = _patch(c, {"routers": [{"id": a, "normal_usage_gb": 10}]})
    ea = _effective(app, a)
    assert (ea["normal_speed_mbps"], ea["offline_after_min"], ea["normal_usage_gb"]) == (50, 9, 10)
    assert ea["enabled"] is False                 # not silently re-enabled
    row = next(r for r in data["routers"] if r["id"] == a)
    assert row["override"]["normal_speed_mbps"] == 50
    # explicit null clears just that one back to «inherit»
    _patch(c, {"routers": [{"id": a, "normal_speed_mbps": None}]})
    assert _effective(app, a)["normal_speed_mbps"] == 100
    assert _effective(app, a)["offline_after_min"] == 9


def test_payload_and_web_form_say_inherits_global_value(app):
    c = app.test_client()
    a = _nas(c, "192.0.2.64")
    data = _patch(c, {"settings": {"default_speed_mbps": 250}})
    row = next(r for r in data["routers"] if r["id"] == a)
    assert row["inherited"]["normal_speed_mbps"] == 250
    assert row["override"]["normal_speed_mbps"] is None
    from flask import url_for
    with app.test_request_context():
        url = url_for("radius.mt_alerts_index")
    with c.session_transaction() as sess:
        sess.update(admin_id=1, admin_user="owner_root", admin_name="Owner",
                    is_super_admin=True, tenant_id=1, _csrf_token="ra-csrf")
    html = c.get(url).get_data(as_text=True)
    tag = re.search(r'<input[^>]*name="r_%d_speed"[^>]*>' % a, html).group(0)
    assert "يرث العامّ (250)" in tag, tag
    assert 'value=""' in tag
