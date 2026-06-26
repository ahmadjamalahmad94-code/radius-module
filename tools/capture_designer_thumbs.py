# -*- coding: utf-8 -*-
"""لقطة + قياس مصغّرات معرض مصمّم صفحة الدخول (library picker + P4 gallery).

يَبوت التطبيق، يَزرع راوترًا، يَحقن جلسة سوبر، يَفتح صفحة المصمّم، يَلتقط
شبكتَي المصغّرات ويَقيس أبعاد بطاقة/مصغّرة نموذجيّة (قبل/بعد التصغير).

التشغيل:  [TAG=before|after] python tools/capture_designer_thumbs.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
OUT = os.path.join(REPO, "preview", "designer_thumbs")
PORT = 5471
TAG = os.environ.get("TAG", "").strip()


def _seed(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admin = admins_repo.create_admin(
            username="thumbs_super", password="x", full_name="معاينة",
            is_super_admin=True)
        with transaction() as c:
            c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                      "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                      ("راوتر المبنى", "rb1", "10.0.0.1", "s", "mikrotik",
                       "other", "2026-01-01"))
        return admin.id


def _cookie(app, admin_id):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": admin_id, "admin_user": "thumbs_super",
                    "admin_name": "م", "is_super_admin": True, "tenant_id": 1,
                    "_csrf_token": "preview"})


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="thumbs_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    admin_id = _seed(app)
    cookie = _cookie(app, admin_id)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)
    prefix = (TAG + "_") if TAG else ""
    url = f"http://127.0.0.1:{PORT}/admin/radius/mt/1/login-designer"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 1000},
                            device_scale_factor=2, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        pg.goto(url, wait_until="load", timeout=25000)
        pg.wait_for_timeout(1200)

        # قياس بطاقة/مصغّرة نموذجيّة من كل نظام
        info = pg.evaluate(r"""() => {
          const out = {};
          const lib = document.querySelector('.mtld-gallery .mtld-card');
          const libThumb = document.querySelector('.mtld-gallery .mtld-thumb');
          const v = document.querySelector('.mtld-vgrid .mtld-vcard');
          const vt = document.querySelector('.mtld-vgrid .mtld-vthumb');
          const r = el => el ? {w: Math.round(el.getBoundingClientRect().width),
                                h: Math.round(el.getBoundingClientRect().height)} : null;
          out.lib_card = r(lib); out.lib_thumb = r(libThumb);
          out.v_card = r(v); out.v_thumb = r(vt);
          // عدد الأعمدة في كل شبكة
          const cols = sel => { const g=document.querySelector(sel);
            return g ? getComputedStyle(g).gridTemplateColumns.split(' ').length : 0; };
          out.lib_cols = cols('.mtld-gallery'); out.v_cols = cols('.mtld-vgrid');
          return out;
        }""")
        print(prefix or "(current)", info)

        # لقطة شبكة المكتبة
        lib = pg.query_selector(".mtld-gallery")
        if lib:
            lib.screenshot(path=os.path.join(OUT, f"{prefix}library_grid.png"))
        # لقطة شبكة المعرض P4 (إن وُجدت)
        vg = pg.query_selector(".mtld-vgrid")
        if vg:
            vg.screenshot(path=os.path.join(OUT, f"{prefix}gallery_grid.png"))
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
