# -*- coding: utf-8 -*-
"""إعادة إنتاج علّتي صفحة «خدمات المنافذ» (loop_detect):

  BUG 1 — زرّ «تركيب» (data-pss-port-action=apply) لا يفعل شيئًا عند الضغط.
  BUG 2 — البانر العلوي «مفعّلة على كل المنافذ» يناقض الجدول السفلي الذي
          يُظهر منافذ غير مركّبة فعلًا.

يزرع راوترًا + حالة محفوظة (8 منافذ enabled) + قراءات poller حيث 4 منافذ
‫no-rule (غير مركّبة) و4 searching (مركّبة)، يُموّه دفع الراوتر لينجح، ثم
يفتح الصفحة في Chrome، يلتقط أخطاء console وطلبات الشبكة، ويضغط «تركيب»
على أوّل منفذ غير مركّب ويرصد ما يحدث.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PORT = 5468

SAVED = ["ether2", "ether3", "ether4", "ether5",
         "ether6", "ether7", "ether8", "ether9"]
NOT_INSTALLED = {"ether2", "ether3", "ether4", "ether5"}  # no-rule


def _fake_interfaces(*_a, **_k):
    return [{"name": "ether1", "type": "ether", "running": True, "disabled": "no"}] + [
        {"name": p, "type": "ether", "running": True, "disabled": "no"} for p in SAVED]


def _fake_push(nas, plan, comment):
    """يحاكي دفعًا ناجحًا للراوتر (بلا اتصال حقيقي)."""
    step = types.SimpleNamespace(path="/ip/dhcp-client/add", ok=True, error="")
    res = types.SimpleNamespace(ok=True, error="", steps=[step])
    return res, ""


def _seed(app, box):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import (admins_repo, tenants_repo,
                                          router_loop_probes_repo)
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        with transaction() as c:
            c.execute("INSERT OR REPLACE INTO admins(id,username,password_hash,"
                      "full_name,is_super_admin,enabled,created_at) "
                      "VALUES(1,'preview','x','معاينة',1,1,'2026-01-01')")
            c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                      "secret,vendor,nas_type,api_port,api_user,api_password,"
                      "api_use_tls,enabled,created_at) VALUES(1,?,?,?,?,?,?,?,?,?,"
                      "0,1,'2026-01-01')",
                      ("راوتر برج السكن", "rb", "10.0.2.1", "s", "mikrotik",
                       "other", 8728, "admin", "pw"))
            nas_id = c.execute("SELECT id FROM nas_devices WHERE tenant_id=1 "
                               "LIMIT 1").fetchone()["id"]
        box.append(nas_id)
        tenants_repo.set_setting(1, f"pss.{nas_id}.loop_detect.ports",
                                 ",".join(SAVED), by=1)
        tenants_repo.set_setting(1, f"pss.{nas_id}.loop_detect.enabled", "1", by=1)
        # قراءات poller: no-rule (غير مركّبة) لبعضها، searching (مركّبة) للباقي
        for p in SAVED:
            router_loop_probes_repo.upsert_reading(
                tenant_id=1, router_id=nas_id, interface=p,
                status=("no-rule" if p in NOT_INSTALLED else "searching"))


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="pss_repro_")
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
    box: list[int] = []
    _seed(app, box)
    nas_id = box[0]
    from app.radius.services import port_script_services as pss
    pss.discover_interfaces = _fake_interfaces  # type: ignore
    from app.radius.routes import port_script_services as route
    route._push_to_router = _fake_push  # type: ignore

    cookie = _cookie(app)
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.8)

    url = f"http://127.0.0.1:{PORT}/admin/radius/mt/{nas_id}/port-services?slug=loop_detect"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 1000}, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        console_msgs, net, failed = [], [], []
        pg.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text}"))
        pg.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {e}"))
        pg.on("request", lambda r: net.append(r.url) if "apply-port" in r.url else None)
        pg.on("requestfailed", lambda r: failed.append(r.url))
        pg.on("response", lambda r: failed.append(f"{r.status} {r.url}")
              if r.status >= 400 else None)
        pg.goto(url, wait_until="load", timeout=25000)
        pg.wait_for_timeout(900)
        out = os.path.join(REPO, "preview", "pss_status_fix")
        os.makedirs(out, exist_ok=True)
        # لقطة الحالة الابتدائيّة: البانر «partial» + رقائق كهرمانيّة + الجدول
        top = pg.query_selector("[data-mt-port-services]")
        (top or pg).screenshot(path=os.path.join(out, "before_partial_banner.png"))
        uds = pg.evaluate("() => ({ hasUDS: !!window.UDS, "
                          "hasConfirm: !!(window.UDS && window.UDS.confirm), "
                          "hasToast: !!(window.UDS && window.UDS.toast) })")
        print("── UDS / TOAST AVAILABILITY ──")
        print(f"  window.UDS          : {uds['hasUDS']}")
        print(f"  UDS.confirm         : {uds['hasConfirm']}")
        print(f"  UDS.toast           : {uds['hasToast']}")
        print("── FAILED / 4xx RESOURCES ──")
        for f in failed[:10]:
            print("  " + f)
        if not failed:
            print("  (none)")
        # نُجبر confirm ليُرجِع true (المستخدم الحقيقي يضغط «تأكيد» في مودال
        # UDS؛ هنا نتجاوزه لاختبار المسار الكامل برمجيًّا).
        net.clear()
        pg.evaluate("() => { window.confirm = () => true; "
                    "if (window.UDS) window.UDS.confirm = async () => true; }")
        # حالة الصفحة قبل النقر
        info = pg.evaluate("""() => ({
          mismatch: !!document.querySelector('[data-pss-loop-mismatch]'),
          banner: (document.querySelector('[data-pss-state-banner]')||{}).getAttribute
                  ? document.querySelector('[data-pss-state-banner]').getAttribute('data-pss-state-banner') : null,
          chips: document.querySelectorAll('[data-pss-iface-chip]').length,
          applyBtns: document.querySelectorAll('[data-pss-port-action="apply"]').length,
          tableHasUrl: !!(document.querySelector('[data-pss-loop-table]')||{}).getAttribute
                       && !!document.querySelector('[data-pss-loop-table]').getAttribute('data-pss-port-url'),
        })""")
        print("── PAGE STATE ──")
        print(f"  mismatch banner present : {info['mismatch']}")
        print(f"  top banner state        : {info['banner']}")
        print(f"  green chips             : {info['chips']}")
        print(f"  «تركيب» buttons         : {info['applyBtns']}")
        print(f"  table has port-url      : {info['tableHasUrl']}")
        # راقب نداءات التوست
        pg.evaluate("""() => { window.__toasts=[];
          if (window.UDS) { const o=UDS.toast; UDS.toast=(m,k)=>{window.__toasts.push((k||'')+':' +m); return o&&o(m,k);} } }""")
        # اضغط أوّل «تركيب» (على منفذ غير مركّب)
        btn = pg.query_selector('[data-pss-port-action="apply"]')
        cell_before = ""
        target_port = btn.evaluate("b => b.getAttribute('data-pss-port')") if btn else None
        if btn:
            cell = btn.evaluate_handle("b => b.closest('[data-pss-port-action-cell]')")
            cell_before = cell.evaluate("c => c.innerText")
            btn.click()
            pg.wait_for_timeout(1500)
            cell_after = cell.evaluate("c => c.innerText")
        else:
            cell_after = "(no apply button found)"
        after = pg.evaluate("""(port) => {
          const row = document.querySelector('[data-pss-loop-row="'+port+'"]');
          const ruleCell = row && row.querySelector('[data-pss-rule-cell]');
          const chip = document.querySelector('[data-pss-iface-chip="'+port+'"]');
          const banner = document.querySelector('[data-pss-state-banner]');
          const mm = document.querySelector('[data-pss-loop-mismatch]');
          return {
            ruleText: ruleCell ? ruleCell.innerText.trim() : null,
            ruleInstalled: ruleCell ? ruleCell.getAttribute('data-pss-installed') : null,
            actionText: (row && row.querySelector('[data-pss-port-action-cell]')) ? row.querySelector('[data-pss-port-action-cell]').innerText.trim() : null,
            chipWarn: chip ? chip.classList.contains('pss-iface-chip--warn') : null,
            bannerState: banner ? banner.getAttribute('data-pss-state-banner') : null,
            bannerHead: banner ? banner.innerText.split('\\n')[0] : null,
            mismatchHidden: mm ? mm.hidden : 'no-banner',
            toasts: window.__toasts || [],
          };
        }""", target_port)
        print("\n── CLICK «تركيب» on first not-installed port (" + str(target_port) + ") ──")
        print(f"  cell before          : {cell_before!r}")
        print(f"  apply-port requests  : {len(net)} {net}")
        print(f"  TOAST shown          : {after['toasts']}")
        print(f"  row rule pill -> text : {after['ruleText']!r}  installed={after['ruleInstalled']}")
        print(f"  row action -> text    : {after['actionText']!r}")
        print(f"  banner chip warn?     : {after['chipWarn']}")
        print(f"  banner state/head     : {after['bannerState']} | {after['bannerHead']!r}")
        print(f"  mismatch banner hidden: {after['mismatchHidden']}")
        # لقطة بعد التركيب: الصفّ صار «مركّبة» + زرّ «إزالة» + رأس 5/8
        (top or pg).screenshot(path=os.path.join(out, "after_install_click.png"))
        print(f"\n  screenshots saved to {out}")
        print("\n── CONSOLE / PAGE ERRORS ──")
        for m in console_msgs[:20]:
            print("  " + m)
        if not console_msgs:
            print("  (none)")
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
