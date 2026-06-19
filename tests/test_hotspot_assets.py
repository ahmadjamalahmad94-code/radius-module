# -*- coding: utf-8 -*-
"""رفع الأصول المستضافة (فيديو/خط): الريبو + راوت الرفع/الحذف + رفعها
للراوتر عند النشر + خط العلامة في منتقي الخطوط. شغّل الملف وحده."""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_as_")
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
    u = f"as_{uuid4().hex[:8]}"
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
                "connection_mode) VALUES (?,1,'r','203.0.113.3','s',"
                "'mikrotik','hotspot',1,?,'direct')", (nas_id, now))


# ── (1) الريبو ──
def test_repo_save_get_list_delete(app):
    with app.app_context():
        from app.radius.db.repos import hotspot_assets_repo as a
        a.save_asset(1, nas_id=1, kind="video", filename="ad.mp4",
                     content=b"\x00\x01\x02", content_type="video/mp4")
        lst = a.list_assets(1, 1)
        assert len(lst) == 1 and lst[0]["filename"] == "ad.mp4"
        assert "content" not in lst[0]  # القائمة بلا BLOB
        got = a.get_asset(1, 1, "ad.mp4")
        assert got["content"] == b"\x00\x01\x02"
        a.delete_asset(1, 1, lst[0]["id"])
        assert a.list_assets(1, 1) == []


def test_repo_size_cap(app):
    with app.app_context():
        from app.radius.db.repos import hotspot_assets_repo as a
        with pytest.raises(ValueError):
            a.save_asset(1, nas_id=1, kind="font", filename="big.ttf",
                         content=b"x" * (a.MAX_FONT_BYTES + 1))


def test_repo_brand_font_filename(app):
    with app.app_context():
        from app.radius.db.repos import hotspot_assets_repo as a
        assert a.brand_font_filename(1, 1) == ""
        a.save_asset(1, nas_id=1, kind="font", filename="brand.woff2",
                     content=b"OTTO")
        assert a.brand_font_filename(1, 1) == "brand.woff2"


# ── (2) راوت الرفع ──
def test_upload_route_stores_asset(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    tok = _csrf(c)
    data = {"_csrf_token": tok, "kind": "video",
            "asset_file": (io.BytesIO(b"\x00\x01vid"), "splash.mp4")}
    r = c.post("/admin/radius/mt/1/login-designer/asset/upload",
               data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    with app.app_context():
        from app.radius.db.repos import hotspot_assets_repo as a
        assert any(x["filename"] == "splash.mp4" for x in a.list_assets(1, 1))


def test_upload_rejects_bad_extension(app):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    tok = _csrf(c)
    data = {"_csrf_token": tok, "kind": "video",
            "asset_file": (io.BytesIO(b"x"), "evil.exe")}
    r = c.post("/admin/radius/mt/1/login-designer/asset/upload",
               data=data, content_type="multipart/form-data")
    assert "غير مدعوم" in r.get_data(as_text=True)
    with app.app_context():
        from app.radius.db.repos import hotspot_assets_repo as a
        assert a.list_assets(1, 1) == []


# ── (3) خط العلامة في منتقي الخطوط ──
def test_brand_font_uses_uploaded(app):
    from app.radius.services import hotspot_addons as ad
    cfg = ad.normalize_config({"font_picker": {"enabled": True,
                              "config": {"family": "brand"}}})
    # بلا خط مرفوع → لا تغيير
    assert ad.render_prelogin_fragments(cfg, {}) == ""
    # مع ctx['brand_font'] → @font-face نسبيّ
    html = ad.render_prelogin_fragments(cfg, {"brand_font": "brand.woff2"})
    assert "@font-face" in html and "brand.woff2" in html and "HRBrand" in html


# ── (4) النشر يرفع الأصول للراوتر (FTP) ──
def test_deploy_uploads_assets_via_ftp(app, monkeypatch):
    c = app.test_client()
    _seed(app, 1)
    _login(c)
    with app.app_context():
        from app.radius.db.repos import hotspot_assets_repo as a
        a.save_asset(1, nas_id=1, kind="video", filename="ad.mp4",
                     content=b"\x00\x01\x02movie")

    # عميل راوتر وهمي + FTP وهمي يلتقط الرفع.
    from app.radius.routes import mt_login_designer as routes

    class _Fake:
        def connect(self): pass
        def close(self): pass
        def run(self, p, attrs=None): return []
    monkeypatch.setattr(routes, "_connect_client", lambda nid: _Fake())
    monkeypatch.setattr(routes, "_ftp_config",
                        lambda nid: {"host": "h", "user": "u",
                                     "password": "p", "port": 21})
    uploaded = {}
    import app.radius.services.hotspot_file_transfer as hft
    monkeypatch.setattr(
        hft, "ftp_upload",
        lambda host, user, pw, path, data, **k: uploaded.update({path: data}) or len(data))
    # نشر متدفّق (يستهلك _iter_deploy عبر الراوت العادي مع confirm)
    tok = _csrf(c)
    c.post("/admin/radius/mt/1/login-designer/deploy",
           data={"_csrf_token": tok, "confirm": "1"})
    assert "hotspot/ad.mp4" in uploaded
    assert uploaded["hotspot/ad.mp4"] == b"\x00\x01\x02movie"
