# -*- coding: utf-8 -*-
"""لقطات «خدمات المنافذ» — منع البث (bt_wifi_block) + تتبّع اللوب (loop_detect).

يُشغّل التطبيق الحقيقي على DB مؤقّتة، يزرع راوترًا واحدًا ويُفعّل الخدمتين
على المنافذ ether2..ether8 (عبر tenant_settings)، ويُموّه اكتشاف الواجهات
(ether1 WAN + ether2..ether8 LAN) فتظهر قائمة المنافذ المطبّقة كاملةً، ثم
يلتقط لقطة جوّال حقيقيّة 390px لكل خدمة لإثبات وجود/غياب زر الحذف لكل واجهة.

التشغيل:
  python tools/capture_port_iface_delete.py            # قبل
  TAG=after python tools/capture_port_iface_delete.py  # بعد
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
OUT_DIR = os.path.join(REPO, "preview", "port_iface_delete")
PORT = 5462
TAG = os.environ.get("TAG", "").strip()

PORTS = ["ether2", "ether3", "ether4", "ether5", "ether6", "ether7", "ether8"]


def _fake_interfaces(*_args, **_kw):
    """قائمة الواجهات المُموّهة (يُستبدل بها pss.discover_interfaces)
    — ether1 WAN يُستبعد بالمرشّح، ether2..8 LAN تَبقى."""
    return (
        [{"name": "ether1", "type": "ether", "running": True, "disabled": "no"}]
        + [{"name": p, "type": "ether", "running": (i % 3 != 0),
            "disabled": "no"} for i, p in enumerate(PORTS)]
    )


def _seed(app, nas_id_box):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        with transaction() as c:
            c.execute(
                "INSERT OR REPLACE INTO admins(id,username,password_hash,full_name,"
                "is_super_admin,enabled,created_at) "
                "VALUES(1,'preview','x','معاينة',1,1,'2026-01-01')")
            c.execute(
                "INSERT OR IGNORE INTO tenant_memberships(tenant_id,admin_id) "
                "VALUES(1,1)")
            c.execute(
                "INSERT INTO nas_devices(tenant_id,name,shortname,address,secret,"
                "vendor,nas_type,api_port,api_user,api_password,api_use_tls,"
                "enabled,created_at) VALUES(1,?,?,?,?,?,?,?,?,?,0,1,'2026-01-01')",
                ("راوتر برج السكن — المبنى أ", "rb-tower", "10.0.2.1",
                 "secret123", "mikrotik", "other", 8728, "admin", "pw"))
            nas_id = c.execute(
                "SELECT id FROM nas_devices WHERE tenant_id=1 LIMIT 1").fetchone()["id"]
        nas_id_box.append(nas_id)
        # فعّل الخدمتين على ether2..ether8
        for slug in ("bt_wifi_block", "loop_detect"):
            tenants_repo.set_setting(
                1, f"pss.{nas_id}.{slug}.ports", ",".join(PORTS), by=1)
            tenants_repo.set_setting(
                1, f"pss.{nas_id}.{slug}.enabled", "1", by=1)


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="pss_iface_")
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
    nas_box: list[int] = []
    _seed(app, nas_box)
    nas_id = nas_box[0]

    # موّه اكتشاف الواجهات على مستوى وحدة الخدمة (الراوت يستدعي pss.discover_interfaces)
    from app.radius.services import port_script_services as pss
    pss.discover_interfaces = _fake_interfaces  # type: ignore

    cookie = _cookie(app)
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)
    os.makedirs(OUT_DIR, exist_ok=True)
    prefix = (TAG + "_") if TAG else ""

    pages = [
        ("bt_wifi_block", f"/admin/radius/mt/{nas_id}/port-services?slug=bt_wifi_block"),
        ("loop_detect", f"/admin/radius/mt/{nas_id}/port-services?slug=loop_detect"),
    ]
    # عدّ أزرار الحذف لكل واجهة في القائمة المطبّقة
    COUNT_JS = r"""
    () => {
      const chips = document.querySelectorAll('[data-pss-iface-chip]').length;
      const dels  = document.querySelectorAll('[data-pss-iface-del]').length;
      // مربّعات الاختيار الكبيرة (الشبكة) + نصّها
      const grid  = document.querySelectorAll('[data-pss-port-grid] label').length;
      return {chips, dels, grid, winW: window.innerWidth};
    }
    """

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        mob = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=3, is_mobile=True, has_touch=True,
                            locale="ar")
        mob.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        mp = mob.new_page()
        for name, path in pages:
            url = f"http://127.0.0.1:{PORT}{path}"
            resp = mp.goto(url, wait_until="load", timeout=25000)
            mp.wait_for_timeout(900)
            counts = mp.evaluate(COUNT_JS)
            mp.screenshot(path=os.path.join(OUT_DIR, f"{prefix}{name}_390.png"),
                          full_page=True)
            print(f"{name:16s} status={resp.status if resp else '?'} "
                  f"chips={counts['chips']} dels={counts['dels']} "
                  f"grid_labels={counts['grid']}")
        b.close()
    srv.shutdown()
    print(f"\nSaved to {OUT_DIR}")


if __name__ == "__main__":
    main()
