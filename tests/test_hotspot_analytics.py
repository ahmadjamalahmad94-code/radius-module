# -*- coding: utf-8 -*-
"""تحليلات صفحة الدخول: الريبو + نقطة beacon العامّة + اللوحة +
تكامل A/B (المجموعة في البيكون والتقرير). شغّل الملف وحده."""
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
    tmp = tempfile.mkdtemp(prefix="hr_an_")
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
    u = f"an_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="p", full_name="T",
                             is_super_admin=True)
    client.post("/admin/radius/login", data={"username": u, "password": "p"})


# ── (1) الريبو ──
def test_repo_record_and_summary(app):
    with app.app_context():
        from app.radius.db.repos import hotspot_analytics_repo as an
        for _ in range(10):
            an.record_event(1, nas_id=3, template_slug="card",
                            vertical="cafe", event="impression", ab_bucket="A")
        for _ in range(4):
            an.record_event(1, nas_id=3, template_slug="card",
                            vertical="cafe", event="connect", ab_bucket="A")
        an.record_event(1, nas_id=3, template_slug="card", vertical="cafe",
                        event="click", ab_bucket="B")
        s = an.summary(1)
        assert s["totals"]["impressions"] == 10
        assert s["totals"]["connects"] == 4
        assert s["totals"]["cvr"] == 40.0
        assert any(r["template_slug"] == "card" for r in s["by_template"])
        assert any(r["vertical"] == "cafe" for r in s["by_vertical"])
        buckets = {r["ab_bucket"]: r for r in s["by_ab"]}
        assert buckets["A"]["connects"] == 4


def test_repo_ignores_unknown_event(app):
    with app.app_context():
        from app.radius.db.repos import hotspot_analytics_repo as an
        assert an.record_event(1, event="bogus") is False
        assert an.summary(1)["totals"]["impressions"] == 0


# ── (2) نقطة beacon العامّة ──
def test_collect_is_public_and_stores(app):
    c = app.test_client()  # بلا تسجيل دخول — النقطة عامّة
    r = c.post("/admin/radius/hotspot-analytics/collect?t=1&n=5&tpl=card",
               data=json.dumps({"e": "impression", "v": "cafe", "ab": "B"}),
               content_type="application/json")
    assert r.status_code == 204
    with app.app_context():
        from app.radius.db.repos import hotspot_analytics_repo as an
        s = an.summary(1, nas_id=5)
        assert s["totals"]["impressions"] == 1
        assert {r["ab_bucket"] for r in s["by_ab"]} == {"B"}


def test_collect_failopen_no_tenant(app):
    c = app.test_client()
    r = c.post("/admin/radius/hotspot-analytics/collect",
               data=json.dumps({"e": "impression"}),
               content_type="application/json")
    assert r.status_code == 204  # لا يكسر شيئًا


# ── (3) اللوحة ──
def test_dashboard_renders(app):
    c = app.test_client()
    _login(c)
    with app.app_context():
        from app.radius.db.repos import hotspot_analytics_repo as an
        an.record_event(1, nas_id=2, template_slug="dark", vertical="hotel",
                        event="impression")
        an.record_event(1, nas_id=2, template_slug="dark", vertical="hotel",
                        event="connect")
    html = c.get("/admin/radius/hotspot-analytics").get_data(as_text=True)
    assert "تحليلات صفحات الدخول" in html
    assert "معدّل التحويل" in html
    assert "اختبار A/B" in html


# ── (4) تكامل الإضافة: ctx url + مجموعة A/B ──
def test_analytics_addon_uses_ctx_url_and_ab(app):
    from app.radius.services import hotspot_addons as ad
    cfg = ad.normalize_config({"analytics": {"enabled": True,
                              "config": {"vertical": "cafe"}}})
    html = ad.render_prelogin_fragments(cfg, {"analytics_url": "/x/collect?t=1"})
    assert "sendBeacon" in html
    assert "/x/collect?t=1" in html
    assert "hr-ab" in html  # يضمّ مجموعة A/B في البيكون


def test_analytics_no_url_local_only(app):
    from app.radius.services import hotspot_addons as ad
    cfg = ad.normalize_config({"analytics": {"enabled": True, "config": {}}})
    html = ad.render_prelogin_fragments(cfg, {})
    assert "hr-an-imp" in html        # عدّ محلّي
    assert "sendBeacon" not in html   # لا بيكون بلا رابط


# ── (5) المعاينة تبني رابط تحليلات (نسبي) ──
def test_preview_bakes_analytics_url(app):
    c = app.test_client()
    _login(c)
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as cur:
            cur.execute(
                "INSERT INTO nas_devices (id, tenant_id, name, address, "
                "secret, vendor, nas_type, enabled, created_at, "
                "connection_mode) VALUES (1,1,'r','203.0.113.4','s',"
                "'mikrotik','hotspot',1,?,'direct')", (now,))
    tok = None
    c.get("/admin/radius/mt/operations")
    with c.session_transaction() as s:
        tok = s["_csrf_token"]
    r = c.post("/admin/radius/mt/1/login-designer/preview", data={
        "_csrf_token": tok, "template_slug": "classic",
        "addons_json": json.dumps({"analytics": {"enabled": True,
                                   "config": {"vertical": "cafe"}}})})
    html = r.get_data(as_text=True)
    assert "hotspot-analytics/collect" in html
