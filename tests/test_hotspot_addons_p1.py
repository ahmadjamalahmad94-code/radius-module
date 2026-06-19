# -*- coding: utf-8 -*-
"""P1 — إطار إضافات مصمّم صفحة الدخول: السجل، التطبيع، السطحان،
وجامع walled-garden. شغّل الملف وحده.

تتحقّق:
  • سلامة السجل (كل إضافة صالحة، التصنيف/السطح/دالة التوليد).
  • تطبيع addons_json (مفاتيح مجهولة تُسقَط، defaults، bool/number/
    color/url/select، القصّ والتقييد).
  • enabled_specs يعيد المفعّل فقط.
  • جامع walled-garden: نطاقات الإضافات + مشتقّة من حقول الروابط،
    والمخبوزة خادميًّا لا تسرّب نطاقًا.
  • السطح pre: حقن مخبوز، هروب XSS، عدم المساس بـ$(...) المايكروتيك.
  • السطح post: ودجت وصفحة redirect صالحة.
  • walled-garden بالنطاقات: idempotent عبر عميل وهمي + أوامر النسخ.
"""
from __future__ import annotations

import os
import re

import pytest


# ════════════════════════════════════════════════════════════════
# (1) سلامة السجل
# ════════════════════════════════════════════════════════════════
def test_registry_integrity():
    from app.radius.services import hotspot_addons as ad
    assert ad.ADDONS, "السجل فارغ"
    for key, spec in ad.ADDONS.items():
        assert spec.key == key
        assert spec.category in ad.CATEGORY_LABELS
        assert spec.surface in ad._SURFACES
        if spec.runs_prelogin():
            assert spec.pre_fragment is not None, f"{key}: pre بلا دالة"
        if spec.runs_postlogin():
            assert spec.post_widget is not None, f"{key}: post بلا دالة"
        # كل حقل له نوع معروف
        for f in spec.fields:
            assert f.kind in {"text", "textarea", "bool", "url",
                              "color", "number", "select"}


def test_by_category_grouping():
    from app.radius.services import hotspot_addons as ad
    grouped = ad.by_category()
    # كل مفتاح تصنيف ضمن الترتيب المعرّف
    for cat in grouped:
        assert cat in ad.CATEGORY_ORDER
    flat = [s.key for specs in grouped.values() for s in specs]
    assert set(flat) == set(ad.ADDONS.keys())


# ════════════════════════════════════════════════════════════════
# (2) تطبيع الإعداد
# ════════════════════════════════════════════════════════════════
def test_normalize_drops_unknown_and_applies_defaults():
    from app.radius.services import hotspot_addons as ad
    norm = ad.normalize_config({"NOPE_xyz": {"enabled": True},
                                "live_clock": {"enabled": True}})
    assert "NOPE_xyz" not in norm
    assert set(norm.keys()) == set(ad.ADDONS.keys())
    assert norm["live_clock"]["enabled"] is True
    # حقل select الافتراضي
    assert norm["live_clock"]["config"]["format"] == "24h"


def test_normalize_field_kinds_and_clamping():
    from app.radius.services import hotspot_addons as ad
    norm = ad.normalize_config({
        "countdown_access": {"enabled": True,
                             "config": {"seconds": "9999", "label": "x" * 200}},
        "live_clock": {"enabled": True, "config": {"format": "evil"}},
        "social_links": {"enabled": True,
                         "config": {"facebook": "javascript:alert(1)",
                                    "whatsapp": "https://wa.me/123"}},
    })
    # number مقصوص للحد الأقصى (600)
    assert norm["countdown_access"]["config"]["seconds"] == "600"
    # text مقصوص لـmax_len (40)
    assert len(norm["countdown_access"]["config"]["label"]) <= 40
    # select غير صالح يعود للافتراضي
    assert norm["live_clock"]["config"]["format"] == "24h"
    # url خبيث يُرفض (يصبح فارغًا)، وurl صالح يبقى
    assert norm["social_links"]["config"]["facebook"] == ""
    assert norm["social_links"]["config"]["whatsapp"] == "https://wa.me/123"


def test_normalize_accepts_json_string():
    from app.radius.services import hotspot_addons as ad
    norm = ad.normalize_config('{"live_clock": {"enabled": true}}')
    assert norm["live_clock"]["enabled"] is True


def test_normalize_bad_input_safe():
    from app.radius.services import hotspot_addons as ad
    for bad in (None, "", "not json", 12, [], "[]"):
        norm = ad.normalize_config(bad)
        assert set(norm.keys()) == set(ad.ADDONS.keys())


# ════════════════════════════════════════════════════════════════
# (3) enabled_specs
# ════════════════════════════════════════════════════════════════
def test_enabled_specs_only_enabled():
    from app.radius.services import hotspot_addons as ad
    cfg = {"live_clock": {"enabled": True},
           "social_links": {"enabled": False}}
    keys = {s.key for s, _c in ad.enabled_specs(cfg)}
    assert "live_clock" in keys
    assert "social_links" not in keys


# ════════════════════════════════════════════════════════════════
# (4) جامع walled-garden
# ════════════════════════════════════════════════════════════════
def test_collect_walled_garden_social_domains():
    from app.radius.services import hotspot_addons as ad
    cfg = {"social_links": {"enabled": True,
                            "config": {"facebook": "https://facebook.com/x"}}}
    doms = ad.collect_walled_garden_domains(cfg)
    # النطاقات المعلَنة في التعريف تظهر
    for d in ("facebook.com", "instagram.com", "wa.me", "t.me"):
        assert d in doms


def test_server_side_addon_adds_no_domains():
    from app.radius.services import hotspot_addons as ad
    # live_clock/announcements/countdown مخبوزة → لا نطاقات
    cfg = {"live_clock": {"enabled": True},
           "announcements": {"enabled": True, "config": {"body": "x"}},
           "countdown_access": {"enabled": True}}
    assert ad.collect_walled_garden_domains(cfg) == []


def test_disabled_addon_contributes_no_domains():
    from app.radius.services import hotspot_addons as ad
    cfg = {"social_links": {"enabled": False,
                            "config": {"facebook": "https://facebook.com/x"}}}
    assert ad.collect_walled_garden_domains(cfg) == []


# ════════════════════════════════════════════════════════════════
# (5) السطح pre — حقن مخبوز + أمان
# ════════════════════════════════════════════════════════════════
def test_prelogin_fragments_baked():
    from app.radius.services import hotspot_addons as ad
    cfg = {"announcements": {"enabled": True,
                            "config": {"title": "أهلًا", "body": "سطر١\nسطر٢"}},
           "live_clock": {"enabled": True}}
    html = ad.render_prelogin_fragments(cfg, {"accent": "#2563EB"})
    assert "أهلًا" in html
    assert "سطر١" in html and "سطر٢" in html
    assert "hr-clock" in html
    # معلَّمة باسم الإضافة
    assert "hr-addon:announcements" in html


def test_prelogin_escapes_user_text_xss():
    from app.radius.services import hotspot_addons as ad
    cfg = {"announcements": {"enabled": True,
                            "config": {"title": "<script>bad()</script>",
                                       "body": "<img src=x onerror=alert(1)>"}}}
    html = ad.render_prelogin_fragments(cfg, {})
    assert "<script>bad()" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_emergency_both_surfaces():
    from app.radius.services import hotspot_addons as ad
    cfg = {"emergency_notice": {"enabled": True,
                               "config": {"text": "صيانة الليلة"}}}
    pre = ad.render_prelogin_fragments(cfg, {})
    post = ad.render_postlogin_widgets(cfg, {})
    assert "صيانة الليلة" in pre
    assert "صيانة الليلة" in post


def test_empty_config_yields_no_fragments():
    from app.radius.services import hotspot_addons as ad
    assert ad.render_prelogin_fragments({}, {}) == ""
    assert ad.render_postlogin_widgets({}, {}) == ""


# ════════════════════════════════════════════════════════════════
# (6) السطحان عبر hotspot_surfaces — صحّة المايكروتيك
# ════════════════════════════════════════════════════════════════
_REQUIRED_MT = ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)")


def _vals():
    from app.radius.services.hotspot_templates import TEMPLATE_VARIABLES
    return {v.slug: v.default for v in TEMPLATE_VARIABLES}


def test_render_login_surface_preserves_mikrotik_vars():
    from app.radius.services import hotspot_surfaces as sf
    cfg = {"live_clock": {"enabled": True}}
    html = sf.render_login_surface("mikrotik", _vals(), cfg)
    for tok in _REQUIRED_MT:
        assert tok in html, f"placeholder المايكروتيك مفقود: {tok}"
    # الجزء حُقن قبل </body>
    assert "hr-clock" in html
    assert html.count("</body>") >= 1
    assert html.index("hr-clock") < html.rindex("</body>")


def test_render_login_surface_no_addons_equals_plain_render():
    from app.radius.services import hotspot_surfaces as sf
    from app.radius.services import hotspot_templates as tpl
    vals = _vals()
    assert sf.render_login_surface("classic", vals, {}) == \
        tpl.render("classic", vals)


def test_build_redirect_page_valid_and_widgets():
    from app.radius.services import hotspot_surfaces as sf
    vals = _vals()
    vals["TENANT_NAME"] = "شبكة الاختبار"
    cfg = {"social_links": {"enabled": True,
                            "config": {"whatsapp": "https://wa.me/123"}}}
    html = sf.build_redirect_page(vals, cfg)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert 'dir="rtl"' in html
    assert "شبكة الاختبار" in html
    assert "wa.me/123" in html
    assert sf.has_redirect_surface(cfg) is True


def test_redirect_page_escapes_tenant_name():
    from app.radius.services import hotspot_surfaces as sf
    vals = _vals()
    vals["TENANT_NAME"] = "<b>x</b>"
    html = sf.build_redirect_page(vals, {})
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x" in html


def test_walled_garden_domains_for_helper():
    from app.radius.services import hotspot_surfaces as sf
    cfg = {"social_links": {"enabled": True, "config": {}}}
    assert "facebook.com" in sf.walled_garden_domains_for(cfg)


# ════════════════════════════════════════════════════════════════
# (7) walled-garden بالنطاقات — عميل وهمي
# ════════════════════════════════════════════════════════════════
class _FakeClient:
    def __init__(self, existing=None, fail_on=None):
        self._existing = existing or []
        self._fail_on = fail_on
        self.added = []

    def run(self, path, attrs=None):
        if path == "/ip/hotspot/walled-garden/print":
            return [{"dst-host": h} for h in self._existing]
        if path == "/ip/hotspot/walled-garden/add":
            host = (attrs or {}).get("dst-host")
            if self._fail_on and host == self._fail_on:
                raise RuntimeError("denied")
            self.added.append(host)
            return {}
        return []


def test_ensure_hosts_adds_new_idempotent():
    from app.radius.services import hotspot_store_page as sp
    c = _FakeClient(existing=["facebook.com"])
    res = sp.ensure_walled_garden_hosts(c, hosts=["facebook.com", "t.me"])
    assert res.ok is True
    assert res.existing == 1
    assert res.added == 1
    assert c.added == ["t.me"]


def test_ensure_hosts_empty_is_ok():
    from app.radius.services import hotspot_store_page as sp
    res = sp.ensure_walled_garden_hosts(_FakeClient(), hosts=[])
    assert res.ok is True and res.added == 0


def test_ensure_hosts_failure_returns_command():
    from app.radius.services import hotspot_store_page as sp
    c = _FakeClient(fail_on="t.me")
    res = sp.ensure_walled_garden_hosts(c, hosts=["t.me"])
    assert res.ok is False
    assert "dst-host=t.me" in res.command


def test_hosts_command_generation():
    from app.radius.services import hotspot_store_page as sp
    cmd = sp.walled_garden_hosts_command(["facebook.com", "wa.me"])
    assert "dst-host=facebook.com" in cmd
    assert "dst-host=wa.me" in cmd
    assert "HobeRadius-Addon" in cmd


# ════════════════════════════════════════════════════════════════
# (8) المثابرة عبر القاعدة (migration 128) — جولة كاملة
# ════════════════════════════════════════════════════════════════
@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "addons.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret-key")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield flask_app


def test_migration_128_columns_exist(app_ctx):
    from app.radius.db.connection import db
    for tbl in ("hotspot_designs", "hotspot_design_presets"):
        cols = {r["name"] for r in db().execute(f"PRAGMA table_info({tbl})")}
        assert "addons_json" in cols, f"{tbl}: عمود addons_json مفقود"


def test_repo_design_addons_roundtrip(app_ctx):
    from app.radius.db.repos import hotspot_designs_repo as repo
    addons = {"live_clock": {"enabled": True, "config": {"format": "12h"}}}
    repo.save_design(1, 7, template_slug="classic",
                     variables={"TENANT_NAME": "X"}, addons=addons)
    got = repo.get_design(1, 7)
    assert got["addons"]["live_clock"]["enabled"] is True
    assert got["addons"]["live_clock"]["config"]["format"] == "12h"
    # تحديث (UPSERT) يبدّل الإضافات
    repo.save_design(1, 7, template_slug="classic", variables={}, addons={})
    assert repo.get_design(1, 7)["addons"] == {}


def test_repo_design_addons_default_empty(app_ctx):
    from app.radius.db.repos import hotspot_designs_repo as repo
    repo.save_design(1, 8, template_slug="classic", variables={})
    assert repo.get_design(1, 8)["addons"] == {}


def test_repo_preset_addons_roundtrip(app_ctx):
    from app.radius.db.repos import hotspot_designs_repo as repo
    addons = {"social_links": {"enabled": True,
                               "config": {"whatsapp": "https://wa.me/1"}}}
    repo.save_preset(1, 9, name="مطعم", template_slug="card",
                     variables={}, addons=addons)
    presets = repo.list_presets(1, 9)
    assert presets and presets[0]["addons"]["social_links"]["enabled"] is True
    pid = presets[0]["id"]
    one = repo.get_preset(1, 9, pid)
    assert one["addons"]["social_links"]["config"]["whatsapp"] == "https://wa.me/1"


# ════════════════════════════════════════════════════════════════
# (9) تكامل المصمّر — لوح الإضافات يُرسَم، والحفظ يثبّتها
# ════════════════════════════════════════════════════════════════
import sys as _sys  # noqa: E402
import tempfile as _tempfile  # noqa: E402
from datetime import datetime as _dt  # noqa: E402
from uuid import uuid4 as _uuid4  # noqa: E402


@pytest.fixture
def rt_app(monkeypatch):
    tmp = _tempfile.mkdtemp(prefix="hr_addui_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(_sys.modules):
        if k.startswith("app."):
            del _sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(_sys.modules):
        if k.startswith("app."):
            del _sys.modules[k]


def _login(client):
    from app.radius.db.repos import admins_repo
    u = f"adui_{_uuid4().hex[:8]}"
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
        now = _dt.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices (id, tenant_id, name, address, "
                "secret, vendor, nas_type, enabled, created_at, "
                "connection_mode) VALUES (?,1,'r','203.0.113.9','s',"
                "'mikrotik','hotspot',1,?,'direct')", (nas_id, now))


def test_designer_get_shows_addons_panel(rt_app):
    c = rt_app.test_client()
    _seed(rt_app, 1)
    _login(c)
    html = c.get("/admin/radius/mt/1/login-designer").get_data(as_text=True)
    assert "data-mtld-addons" in html
    assert 'name="addons_json"' in html
    # كل إضافة مسجَّلة لها بطاقة + تسمية عربية
    from app.radius.services import hotspot_addons as ad
    for spec in ad.all_addons():
        assert f'data-addon-key="{spec.key}"' in html
        assert spec.label_ar in html


def test_designer_save_persists_addons(rt_app):
    import json as _j
    c = rt_app.test_client()
    _seed(rt_app, 1)
    _login(c)
    tok = _csrf(c)
    addons = {"live_clock": {"enabled": True, "config": {"format": "12h"}},
              "social_links": {"enabled": True,
                               "config": {"whatsapp": "https://wa.me/9"}}}
    res = c.post("/admin/radius/mt/1/login-designer/save", data={
        "_csrf_token": tok, "template_slug": "classic",
        "TENANT_NAME": "مقهى", "ACCENT_COLOR": "#16A34A",
        "TENANT_LOGO_URL": "/img/logo.png", "WELCOME_TEXT": "أهلاً",
        "BG_COLOR": "#F8FAFC", "addons_json": _j.dumps(addons),
    })
    assert res.status_code == 200
    with rt_app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        row = r.get_design(1, 1)
        assert row["addons"]["live_clock"]["enabled"] is True
        assert row["addons"]["live_clock"]["config"]["format"] == "12h"
        assert row["addons"]["social_links"]["config"]["whatsapp"] == \
            "https://wa.me/9"


def test_designer_save_ignores_unknown_addon(rt_app):
    import json as _j
    c = rt_app.test_client()
    _seed(rt_app, 1)
    _login(c)
    tok = _csrf(c)
    res = c.post("/admin/radius/mt/1/login-designer/save", data={
        "_csrf_token": tok, "template_slug": "classic",
        "TENANT_NAME": "x", "ACCENT_COLOR": "#16A34A",
        "TENANT_LOGO_URL": "/img/logo.png", "WELCOME_TEXT": "hi",
        "BG_COLOR": "#F8FAFC",
        "addons_json": _j.dumps({"evil_xyz": {"enabled": True}}),
    })
    assert res.status_code == 200
    with rt_app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        assert "evil_xyz" not in (r.get_design(1, 1)["addons"])
