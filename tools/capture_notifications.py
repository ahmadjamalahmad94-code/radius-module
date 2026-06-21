# -*- coding: utf-8 -*-
"""لقطات PNG لمركز الإشعارات + جرس الظرف (سطح مكتب + جوّال ~390px).

يشغّل اللوحة على DB مؤقّتة مزروعة (مشغّل + إشعارات محلّية/جسر + رسائل
للمزوّد)، كوكي جلسة سوبر، ثم Playwright. للجرس: يَفتح قائمة الظرف
(notif-menu) قبل اللقطة.

التشغيل:  python tools/capture_notifications.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PREVIEW_DIR = os.path.join(REPO, "preview")
PORT = 5398


def _seed(app):
    with app.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="op", password="op123456",
                                 full_name="مشغّل اللوحة")
        from app.radius.services import notifications as N
        # محلّي: عدّ تنازلي للترخيص.
        N.notify(1, type="license", severity="critical",
                 title="يتبقّى يوم واحد على انتهاء ترخيص اللوحة",
                 body="ينتهي الترخيص غدًا (2026-06-22) — جدّد لتفادي الإيقاف.",
                 link="/admin/radius/account", dedup_key="license_expiry:2026-06-22:1")
        # من لوحة التراخيص عبر الجسر.
        N.notify(1, type="billing", severity="info",
                 title="فاتورة جديدة من المزوّد بقيمة 250₪",
                 body="فاتورة تجديد الباقة السنوية جاهزة للدفع.",
                 link="/admin/radius/account", source="bridge", dedup_key="bridge:inv-1001")
        N.notify(1, type="service", severity="success",
                 title="تم تفعيل خدمة الـIP الثابت",
                 body="فعّلت لوحة التراخيص طلبك لخدمة IP ثابت.",
                 source="bridge", dedup_key="bridge:svc-22")
        N.notify(1, type="support", severity="info",
                 title="رد جديد على تذكرتك #7",
                 body="أجاب فريق المزوّد على استفسارك حول التجديد.",
                 link="/admin/radius/notifications", source="bridge", dedup_key="bridge:tk-7")
        # قناة التواصل: رسائل صادرة للمزوّد (سجلّ التسليم).
        from app.radius.services.provider_comms import ProviderCommsService

        class _OkClient:
            def post_support_ticket(s, **kw):
                return {"ok": True, "ref": "PRV-31"}
        ProviderCommsService(client=_OkClient()).submit_ticket(
            1, subject="استفسار عن تكلفة الترقية", body="...", priority="high")

        class _DownClient:
            def post_support_ticket(s, **kw):
                raise RuntimeError("offline")
        ProviderCommsService(client=_DownClient()).submit_ticket(
            1, subject="بلاغ بطء في المزامنة", body="...", kind="complaint")


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "op", "admin_name": "مشغّل اللوحة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="notify_shot_")
    os.environ.update(HOBERADIUS_DB_PATH=os.path.join(tmp, "s.db"),
                      HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
                      HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1",
                      FLASK_SECRET="preview-secret-key")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    _seed(app)
    cookie = _cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    base = f"http://127.0.0.1:{PORT}/admin/radius/notifications"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1366, "height": 950},
                            device_scale_factor=2, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        # 1) مركز الإشعارات — سطح مكتب.
        pg.goto(base, wait_until="load", timeout=20000)
        pg.wait_for_timeout(800)
        pg.screenshot(path=os.path.join(PREVIEW_DIR, "notifications_center.png"),
                      full_page=True)
        print("DESKTOP center")
        # 2) جرس الظرف مفتوحًا (لقطة للأعلى).
        pg.eval_on_selector("#notif-menu", "el => { el.classList.add('open'); el.style.display='block'; }")
        pg.wait_for_timeout(300)
        pg.screenshot(path=os.path.join(PREVIEW_DIR, "notifications_bell.png"),
                      clip={"x": 0, "y": 0, "width": 1366, "height": 430})
        print("DESKTOP bell")
        # 3) مركز الإشعارات — جوّال.
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(400)
        pg.screenshot(path=os.path.join(PREVIEW_DIR, "notifications_center_mobile.png"),
                      full_page=True)
        print("MOBILE center")
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
