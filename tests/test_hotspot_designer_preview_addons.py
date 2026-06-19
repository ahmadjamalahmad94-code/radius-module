# -*- coding: utf-8 -*-
"""المعاينة الكبيرة في المصمّم تعكس الإضافات + الثيم حيًّا (POST
addons_json)، والتحميل الأولي يعكس إضافات التصميم المحفوظ. شغّل وحده."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_pv_")
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


def _login(client):
    from app.radius.db.repos import admins_repo
    u = f"pv_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="p", full_name="T",
                             is_super_admin=True)
    client.post("/admin/radius/login", data={"username": u, "password": "p"})


def _csrf(client):
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as s:
        return s["_csrf_token"]


def _seed(app, nas_id=1):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices (id, tenant_id, name, address, "
                "secret, vendor, nas_type, enabled, created_at, "
                "connection_mode) VALUES (?,1,'r','203.0.113.5','s',"
                "'mikrotik','hotspot',1,?,'direct')", (nas_id, now))


def test_live_preview_reflects_addons(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    tok = _csrf(c)
    addons = {"live_clock": {"enabled": True},
              "theme_dark": {"enabled": True}}
    r = c.post("/admin/radius/mt/1/login-designer/preview", data={
        "_csrf_token": tok, "template_slug": "classic",
        "addons_json": json.dumps(addons)})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "hr-clock" in html          # إضافة محقونة في المعاينة
    assert "background:#0f172a" in html  # الثيم الليلي مطبَّق
    assert "$(link-login-only)" not in html  # placeholders مجرّدة للعرض


def test_live_preview_without_addons_has_no_addon_markup(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    tok = _csrf(c)
    r = c.post("/admin/radius/mt/1/login-designer/preview", data={
        "_csrf_token": tok, "template_slug": "classic",
        "addons_json": "{}"})
    assert "hr-addon:" not in r.get_data(as_text=True)


def test_initial_preview_reflects_saved_addons(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as repo
        repo.save_design(1, 1, template_slug="classic", variables={},
                         addons={"news_ticker": {"enabled": True,
                                 "config": {"items": "خبر مهم"}}})
    # GET أولي بلا template_slug → يسقط للتصميم المحفوظ بإضافاته
    html = c.get("/admin/radius/mt/1/login-designer/preview").get_data(as_text=True)
    assert "hr-ticker" in html and "خبر مهم" in html
