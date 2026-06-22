# -*- coding: utf-8 -*-
"""لقطات تفاعل القوائم المنسدلة على جوّال 390px (الصنف 1 من الأعطال).

يفتح قوائم الشريط العلوي (الجرس/الظرف/اللغة/المستخدم) وقائمة إجراءات صفّ
(«⋮») في جدول، ويلتقط كلًّا منها ليُظهر إن كانت تُقصّ أو تخرج عن الشاشة.

التشغيل:  [TAG=before|after] python tools/capture_responsive_menus.py
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

import tools.capture_responsive_audit as aud  # reuse seed + cookie

OUT_DIR = os.path.join(REPO, "preview", "responsive")
PORT = 5448
TAG = os.environ.get("TAG", "").strip()


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="resp_menus_")
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
    aud._seed(app)
    cookie = aud._cookie(app)

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)
    os.makedirs(OUT_DIR, exist_ok=True)
    prefix = (TAG + "_") if TAG else ""

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=2, is_mobile=True, has_touch=True,
                            locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()

        def overflow_report(label):
            r = pg.evaluate(r"""() => {
              const out = [];
              document.querySelectorAll('#bell-menu,#notif-menu,#lang-menu,#user-menu,#tenant-menu,.uds-menu:not([hidden])').forEach(m=>{
                const cs = getComputedStyle(m);
                if (cs.display === 'none' || m.hidden) return;
                const r = m.getBoundingClientRect();
                out.push({ id: m.id || m.className, left: Math.round(r.left), right: Math.round(r.right),
                           width: Math.round(r.width), offLeft: r.left < 0, offRight: r.right > window.innerWidth });
              });
              return { winW: window.innerWidth, menus: out };
            }""")
            print(label, r)
            return r

        url = f"http://127.0.0.1:{PORT}/admin/radius/subscribers"
        pg.goto(url, wait_until="load", timeout=25000)
        pg.wait_for_timeout(1200)

        # ── top-bar menus ──
        for mid, toggle in [("bell-menu", "#bell-toggle"),
                            ("notif-menu", "#notif-toggle"),
                            ("lang-menu", ".lang-pill"),
                            ("user-menu", ".user-pill")]:
            try:
                pg.evaluate("document.querySelectorAll('#bell-menu,#notif-menu,#lang-menu,#user-menu').forEach(m=>{m.style.display='none';m.classList.remove('open')})")
                pg.eval_on_selector(toggle, "el => el.click()")
                pg.wait_for_timeout(350)
                overflow_report(f"[{mid}]")
                pg.screenshot(path=os.path.join(OUT_DIR, f"{prefix}menu_{mid}_390.png"))
            except Exception as e:  # noqa: BLE001
                print(f"{mid} err:", str(e)[:150])

        # ── row-action menu («⋮») if present ──
        try:
            pg.evaluate("document.querySelectorAll('#bell-menu,#notif-menu,#lang-menu,#user-menu').forEach(m=>{m.style.display='none'})")
            has = pg.query_selector("[data-uds-menu-trigger]")
            if has:
                has.scroll_into_view_if_needed()
                pg.wait_for_timeout(200)
                has.click()
                pg.wait_for_timeout(350)
                overflow_report("[row-menu]")
                pg.screenshot(path=os.path.join(OUT_DIR, f"{prefix}menu_rowaction_390.png"))
            else:
                print("no [data-uds-menu-trigger] on subscribers page")
        except Exception as e:  # noqa: BLE001
            print("row-menu err:", str(e)[:150])

        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
