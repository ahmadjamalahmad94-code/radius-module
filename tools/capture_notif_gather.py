# -*- coding: utf-8 -*-
"""لقطات «جمع كل الإشعارات تحت المركز».

يُشغّل التطبيق الحقيقي على DB مؤقّتة بجلسة سوبر-أدمن + تجاوز الترخيص، ويلتقط:
  • كل صفحات مركز الإشعارات الأربع — تُظهر شريط التنقّل الموحّد (notifications-nav)
    أعلاها يصل لكل سطوح الإشعارات.
  • صفحة «التكاملات والقنوات» — تُظهر روابط الصفحات الكاملة للقنوات.
  • لقطة للشريط الجانبي ومجموعة «الإشعارات والتواصل» مفتوحة (سطح مكتب).

التشغيل:
  python tools/capture_notif_gather.py
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
OUT_DIR = os.path.join(REPO, "preview", "notif_gather")
PORT = 5471


def _seed(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        with transaction() as c:
            c.execute(
                "INSERT OR REPLACE INTO admins(id,username,password_hash,"
                "full_name,is_super_admin,enabled,created_at) "
                "VALUES(1,'preview','x','معاينة',1,1,'2026-01-01')")


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


PAGES = [
    ("center",      "/admin/radius/notifications"),
    ("channels",    "/admin/radius/integrations"),
    ("admin",       "/admin/radius/admin-notifications"),
    ("subscriber",  "/admin/radius/subscriber-notifications"),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="notif_gather_")
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
    _seed(app)
    cookie = _cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)
    os.makedirs(OUT_DIR, exist_ok=True)

    # عدّ روابط شريط التنقّل لكل صفحة
    NAV_JS = r"""
    () => {
      const nav = document.querySelector('[data-testid="notifications-nav"]');
      const links = nav ? Array.from(nav.querySelectorAll('a')).map(a => a.textContent.trim()) : [];
      return {hasNav: !!nav, links};
    }
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        desk = b.new_context(viewport={"width": 1440, "height": 1000},
                             device_scale_factor=1, locale="ar")
        desk.add_cookies([{"name": "session", "value": cookie,
                           "domain": "127.0.0.1", "path": "/"}])
        dp = desk.new_page()
        for name, path in PAGES:
            url = f"http://127.0.0.1:{PORT}{path}"
            resp = dp.goto(url, wait_until="load", timeout=25000)
            dp.wait_for_timeout(800)
            info = dp.evaluate(NAV_JS)
            dp.screenshot(path=os.path.join(OUT_DIR, f"{name}_1440.png"),
                          full_page=True)
            print(f"{name:12s} status={resp.status if resp else '?'} "
                  f"nav={info['hasNav']} tabs={info['links']}")
        # لقطة الشريط الجانبي ومجموعة «الإشعارات والتواصل» مفتوحة
        dp.goto(f"http://127.0.0.1:{PORT}/admin/radius/notifications",
                wait_until="load", timeout=25000)
        dp.wait_for_timeout(600)
        # افتح مجموعة notifhub إن لم تكن مفتوحة
        dp.evaluate("""() => {
          const sec = document.querySelector('[data-hb-section="notifhub"]');
          if (sec && !sec.classList.contains('is-open')) {
            const head = sec.querySelector('.hb-side-section-head');
            if (head) head.click();
          }
        }""")
        dp.wait_for_timeout(400)
        side = dp.query_selector('#hb-side')
        if side:
            side.screenshot(path=os.path.join(OUT_DIR, "sidebar_group_1440.png"))
            print("sidebar_group captured")
        b.close()
    srv.shutdown()
    print(f"\nSaved to {OUT_DIR}")


if __name__ == "__main__":
    main()
