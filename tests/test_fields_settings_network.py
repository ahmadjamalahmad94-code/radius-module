# -*- coding: utf-8 -*-
"""Owner's dead-field decisions 2026-10-06 (team settings) — network misc.

REMOVE: NAS monitoring/auth+acct ports/SNMP/ports count · temp-speed
«تفعيل التحكم بالباندويث» · web-block schedule + safe mode · target/entry
notes · IP pools page · speed-profile priority · lifecycle «حزمة»/«ملف خارجي»
+ unused triggers · maintenance «days» for VACUUM / failed webhooks.
KEEP + label: message templates / campaigns are «معاينة فقط».
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fields_network.db")
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


@pytest.fixture
def web(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess.update(admin_id=1, admin_user="owner_root", admin_name="Owner",
                    is_super_admin=True, tenant_id=1, _csrf_token="fn-csrf")
    return c


def _url(app, endpoint, **kw):
    from flask import url_for
    with app.test_request_context():
        return url_for(endpoint, **kw)


def _db():
    from app.radius.db.connection import db
    return db()


def _nas(client, address, **extra):
    res = client.post("/api/v1/nas", headers=AUTH, json={
        "name": f"fn-{uuid4().hex[:6]}", "address": address,
        "secret": "s3cret-ok", "enabled": False, **extra})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["id"]


def test_nas_dead_fields_gone_and_ignored(app, web):
    c = app.test_client()
    nid = _nas(c, "192.0.2.71", auth_port=1645, acct_port=1646,
               snmp_community="secret-comm", ports=24, monitoring_enabled=False)
    r = c.patch(f"/api/v1/nas/{nid}", headers=AUTH,
                json={"auth_port": "garbage-is-ignored", "ports": -5, "location": "Roof"})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        row = _db().execute("SELECT auth_port, acct_port, snmp_community, ports, "
                            "monitoring_enabled, location FROM nas_devices WHERE id=?",
                            (nid,)).fetchone()
    assert (row["auth_port"], row["acct_port"], row["snmp_community"], row["ports"]) == (1812, 1813, "", 0)
    assert row["monitoring_enabled"] == 1 and row["location"] == "Roof"
    html = web.get(_url(app, "radius.devices_edit", nas_id=nid)).get_data(as_text=True)
    for name in ("auth_port", "acct_port", "snmp_community", "ports", "monitoring_enabled"):
        assert f'name="{name}"' not in html, name
    # a web edit (form without the toggle) must not switch monitoring off
    with app.app_context():
        _db().execute("UPDATE nas_devices SET monitoring_enabled=1 WHERE id=?", (nid,))
    web.post(_url(app, "radius.devices_update", nas_id=nid), data={
        "_csrf_token": "fn-csrf", "name": "edited", "address": "192.0.2.71",
        "vendor": "mikrotik", "nas_type": "hotspot"})
    with app.app_context():
        row = _db().execute("SELECT name, monitoring_enabled FROM nas_devices WHERE id=?",
                            (nid,)).fetchone()
    assert row["name"] == "edited" and row["monitoring_enabled"] == 1


def test_temp_speed_has_no_bandwidth_control_switch(app, web):
    html = web.get(_url(app, "radius.online_list")).get_data(as_text=True)
    assert 'name="bw_control"' not in html


def test_web_block_schedule_and_safe_mode_removed(app, web):
    c = app.test_client()
    nid = _nas(c, "192.0.2.72")
    res = c.post("/api/v1/network-policy/web-block/policies", headers=AUTH,
                 json={"name": "wb", "router_id": nid, "schedule_id": "evenings",
                       "fail_open": False})
    assert res.status_code == 201, res.get_json()
    pid = res.get_json()["data"]["id"]
    r = c.patch(f"/api/v1/network-policy/web-block/policies/{pid}", headers=AUTH,
                json={"schedule_id": "x", "fail_open": False, "name": "wb2"})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pol = wb.get_policy(1, pid)
    assert pol["name"] == "wb2" and (pol["schedule_id"] or "") == "" and pol["fail_open"]
    t = c.post(f"/api/v1/network-policy/web-block/policies/{pid}/targets", headers=AUTH,
               json={"value": "example.com", "notes": "hidden note"})
    assert t.status_code in (200, 201), t.get_json()
    with app.app_context():
        note = _db().execute("SELECT notes FROM npc_web_block_targets WHERE policy_id=?",
                             (pid,)).fetchone()["notes"]
    assert (note or "") == ""
    html = web.get(_url(app, "radius.npc_web_block_edit", policy_id=pid)).get_data(as_text=True)
    assert 'name="schedule_id"' not in html and 'name="fail_open"' not in html


def test_ip_pools_page_is_gone(app, web):
    r = web.get("/admin/radius/pools")
    assert r.status_code in (302, 303)
    assert _url(app, "radius.devices_list") in r.headers["Location"]
    side = web.get(_url(app, "radius.devices_list")).get_data(as_text=True)
    assert _url(app, "radius.pool_list") + '"' not in side


def test_speed_profile_priority_removed(app, web):
    c = app.test_client()
    r = c.post("/api/v1/bandwidth-profiles", headers=AUTH,
               json={"name": "P1", "rate_down": 5000, "rate_up": 1000, "priority": "bad"})
    assert r.status_code == 201, r.get_json()
    pid = r.get_json()["data"]["id"]
    with app.app_context():
        _db().execute("UPDATE bandwidth_profiles SET priority=7 WHERE id=?", (pid,))
    r = c.patch(f"/api/v1/bandwidth-profiles/{pid}", headers=AUTH,
                json={"name": "P1b", "rate_down": 6000, "rate_up": 1000, "priority": 1})
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        assert _db().execute("SELECT priority FROM bandwidth_profiles WHERE id=?",
                             (pid,)).fetchone()["priority"] == 7
    html = web.get(_url(app, "radius.bw_edit", bw_id=pid)).get_data(as_text=True)
    assert 'name="priority"' not in html


def test_lifecycle_form_offers_only_cards_and_subscribers(app, web):
    html = web.get(_url(app, "radius.lifecycle_settings")).get_data(as_text=True)
    assert 'value="external_file"' not in html and 'value="card_batch"' not in html
    assert 'value="card"' in html and 'value="subscriber"' in html


def test_maintenance_days_ignored_for_vacuum_and_failed_webhooks(app):
    c = app.test_client()
    for action in ("vacuum", "purge_failed_webhooks"):
        r = c.post("/api/v1/tools/maintenance/preview", headers=AUTH,
                   json={"action": action, "days": "not-a-number"})
        assert r.status_code == 200, (action, r.get_json())
        assert r.get_json()["data"]["days"] is None
    r = c.post("/api/v1/tools/maintenance/preview", headers=AUTH,
               json={"action": "purge_audit", "days": "x"})
    assert r.status_code == 422


def test_templates_and_campaigns_are_labelled_preview_only(app, web):
    for ep in ("radius.communications_templates", "radius.communications_campaigns"):
        html = web.get(_url(app, ep)).get_data(as_text=True)
        assert 'data-testid="preview-only-banner"' in html, ep
        assert "معاينة فقط" in html, ep
