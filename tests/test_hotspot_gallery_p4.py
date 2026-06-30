# -*- coding: utf-8 -*-
"""P4 — معرض القوالب الجاهزة حسب نوع المنشأة: السجلّ، الحلّ، صحّة
المعاينة (MikroTik)، وتطبيق القالب عبر الراوت. شغّل الملف وحده."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


# ── (1) سجلّ المعرض ──
def test_gallery_registry_valid():
    from app.radius.services import hotspot_gallery as hg
    from app.radius.services import hotspot_addons as ad
    from app.radius.services import hotspot_templates as ht
    assert hg.GALLERY, "المعرض فارغ"
    for t in hg.GALLERY:
        assert t.vertical in hg.VERTICALS, f"{t.key}: نوع منشأة مجهول"
        # القالب الأساسي موجود في المكتبة
        assert t.base_slug in ht.TEMPLATES_BY_SLUG, f"{t.key}: base_slug مجهول {t.base_slug}"
        # كل إضافة مُشار إليها مسجَّلة
        for ak in t.addons:
            assert ak in ad.ADDONS, f"{t.key}: إضافة مجهولة {ak}"


def test_every_vertical_has_at_least_one_template():
    from app.radius.services import hotspot_gallery as hg
    bv = hg.by_vertical()
    for v in hg.VERTICALS:
        assert bv.get(v), f"نوع منشأة بلا قالب: {v}"


def test_gallery_keys_unique():
    from app.radius.services import hotspot_gallery as hg
    keys = [t.key for t in hg.GALLERY]
    assert len(keys) == len(set(keys))


# ── (2) الحلّ (resolve) ──
def test_resolve_merges_over_base_vars():
    from app.radius.services import hotspot_gallery as hg
    base = {"TENANT_NAME": "مقهى فلان", "TENANT_LOGO_URL": "/img/x.png"}
    slug, variables, addons = hg.resolve("cafe_chill", base_vars=base)
    # يبقى اسم/شعار المستخدم
    assert variables["TENANT_NAME"] == "مقهى فلان"
    assert variables["TENANT_LOGO_URL"] == "/img/x.png"
    # وتُطبَّق تعديلات القالب
    assert variables["ACCENT_COLOR"] == "#0d9488"
    # الإضافات تركيبة غير فارغة
    assert addons and "theme_glass" in addons


def test_resolve_unknown_returns_none():
    from app.radius.services import hotspot_gallery as hg
    assert hg.resolve("nope_xyz") is None


# ── (3) صحّة المعاينة: كل قالب معرض يولّد سطح login صالح ──
def test_every_gallery_template_renders_valid_mikrotik(monkeypatch):
    from app.radius.services import hotspot_addons_content as c
    monkeypatch.setattr(c, "fetch_weather", lambda *a, **k: {"temp": 25, "code": 0})
    from app.radius.services import hotspot_gallery as hg
    from app.radius.services import hotspot_surfaces as sf
    from app.radius.services import hotspot_templates as ht
    vals = {v.slug: v.default for v in ht.TEMPLATE_VARIABLES}
    for t in hg.GALLERY:
        slug, variables, addons = hg.resolve(t.key, base_vars=vals)
        html = sf.render_login_surface(slug, variables, addons)
        for tok in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)"):
            assert tok in html, f"{t.key}: placeholder مفقود {tok}"


# ── (4) الراوت: تطبيق + معاينة ──
@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_p4_")
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
    u = f"p4_{uuid4().hex[:8]}"
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
                "connection_mode) VALUES (?,1,'r','203.0.113.7','s',"
                "'mikrotik','hotspot',1,?,'direct')", (nas_id, now))


def test_designer_get_shows_gallery(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    html = c.get("/admin/radius/mt/1/login-designer").get_data(as_text=True)
    # المعرضان القديمان («معرض التصاميم» + «قوالب جاهزة حسب نوع منشأتك»)
    # دُمِجا في معرضٍ موحّد بتبويبات أنواع المنشآت (انظر
    # test_designer_unified_gallery). نتحقّق من ظهور المعرض الموحّد وأنّ
    # بطاقاتِه تُصيَّر من نفس خطّ المعاينة (render_login_surface) لا mockup.
    assert "data-mtld-gtabs" in html              # شريط تبويبات الأنواع
    assert html.count('data-mtld-gsec="') == 7    # 7 لوحات أقسام
    assert "data-mt-designer-template" in html    # آليّة اختيار التصميم
    # المُصغّرات = iframe حيّ لنقطة المعاينة (WYSIWYG، لا مصغّر يدويّ).
    assert "mtld-thumb-frame" in html and "data-mtld-thumb-src" in html


def test_gallery_preview_route_renders(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    r = c.get("/admin/radius/mt/1/login-designer/gallery/preview/hotel_lux")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # معاينة مجرّدة من placeholders، لكن بنية الصفحة حاضرة
    assert "<form" in body or "login" in body
    assert "$(link-login-only)" not in body  # جُرِّدت للعرض


def test_gallery_apply_persists_combo(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    tok = _csrf(c)
    r = c.post("/admin/radius/mt/1/login-designer/gallery/apply",
               data={"_csrf_token": tok, "gallery_key": "restaurant_qr"})
    assert r.status_code == 200
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as repo
        d = repo.get_design(1, 1)
        assert d["template_slug"] == "card"            # base_slug للقالب
        assert d["variables"]["ACCENT_COLOR"] == "#d97706"
        assert d["addons"]["qr_menu"]["enabled"] is True
        assert d["addons"]["prayer_times"]["enabled"] is True


def test_gallery_apply_unknown_key_errors(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    tok = _csrf(c)
    r = c.post("/admin/radius/mt/1/login-designer/gallery/apply",
               data={"_csrf_token": tok, "gallery_key": "nope"})
    assert r.status_code == 200
    assert "غير معروف" in r.get_data(as_text=True)
