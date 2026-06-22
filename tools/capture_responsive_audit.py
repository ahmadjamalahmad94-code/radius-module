# -*- coding: utf-8 -*-
"""تدقيق التجاوب (responsive) الشامل للوحة العميل.

يُشغّل التطبيق الحقيقي محليًّا على DB مؤقّتة مزروعة ببيانات تمثيلية كثيفة
(مشتركون/بطاقات/جلسات/أجهزة/معاملات) بجلسة سوبر-أدمن مُحقَنة + تجاوز بوّابة
الترخيص، ثم يمشي على كل صفحات الشريط الجانبي ويلتقط لقطتين لكل صفحة:
  • جوّال حقيقي 390×844 (deviceScaleFactor 3, isMobile, hasTouch)
  • سطح مكتب 1440×900

ولكل صفحة عند 390px يقيس آليًّا:
  • تجاوز أفقي للصفحة (scrollWidth > clientWidth)  ← الصنف 2/3
  • أي جدول يتجاوز حاويته بلا تمرير                ← الصنف 2
ويكتب تقريرًا JSON (preview/responsive/_audit.json).

التشغيل:
  python tools/capture_responsive_audit.py            # كل الصفحات
  python tools/capture_responsive_audit.py overview cards   # مرشّح بالأسماء
  TAG=after python tools/capture_responsive_audit.py  # بادئة ملفات after_
"""
from __future__ import annotations

import json
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
OUT_DIR = os.path.join(REPO, "preview", "responsive")
PORT = 5447
GB = 1073741824
TAG = os.environ.get("TAG", "").strip()


# ───────────────────────── SEED ─────────────────────────
def _seed(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()

        # ── plans + bandwidth + batches ──
        try:
            with transaction() as c:
                c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                          "VALUES(1,1,?,?,?)", ("باقة الهوت سبوت اليومية", "Hotspot", "2026-01-01"))
                c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                          "VALUES(2,1,?,?,?)", ("باقة الألياف الشهرية 50 ميجا", "PPPoE", "2026-01-01"))
                c.execute("INSERT INTO access_plans(id,tenant_id,name,service_type,created_at) "
                          "VALUES(3,1,?,?,?)", ("باقة الشركات اللامحدودة", "PPPoE", "2026-01-01"))
                c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,created_at) "
                          "VALUES(1,'B-2026-001',?,1,100,'2026-05-01')", ("حزمة 100 كرت ساعة",))
                c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,created_at) "
                          "VALUES(1,'B-2026-002',?,2,50,'2026-05-10')", ("حزمة 50 كرت يوم",))
                bid = c.execute("SELECT id FROM card_batches WHERE tenant_id=1 LIMIT 1").fetchone()["id"]
        except Exception as e:  # noqa: BLE001
            print("seed plans skip:", e); bid = None

        # ── routers + network devices ──
        try:
            with transaction() as c:
                for i, (nm, sn, ip) in enumerate([
                        ("راوتر المبنى الرئيسي — الطابق الأرضي", "rb-core", "10.0.0.1"),
                        ("راوتر الفرع الشمالي", "rb-north", "10.0.1.1"),
                        ("راوتر برج السكن", "rb-tower", "10.0.2.1")], 1):
                    c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                              "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                              (nm, sn, ip, "secret123", "mikrotik", "other", "2026-01-01"))
                rid = c.execute("SELECT id FROM nas_devices WHERE tenant_id=1 LIMIT 1").fetchone()["id"]
            from app.radius.db.repos import network_devices_repo
            for nm, dt, ip, loc, crit in [
                    ("سويتش التوزيع الرئيسي — 48 منفذ", "switch", "10.0.0.21", "غرفة الخوادم", True),
                    ("أكسس بوينت الطابق الأول", "ap", "10.0.0.22", "الطابق الأول", False),
                    ("أكسس بوينت السطح الخارجي", "ap", "10.0.0.23", "السطح", True),
                    ("جهاز UniFi البرج الشمالي", "ap", "10.0.2.22", "البرج الشمالي", False)]:
                network_devices_repo.create(tenant_id=1, router_id=rid, name=nm,
                                            device_type=dt, ip_address=ip, location=loc,
                                            watch_enabled=True, is_critical=crit)
        except Exception as e:  # noqa: BLE001
            print("seed network skip:", e); rid = None

        # ── pools ──
        try:
            with transaction() as c:
                if rid:
                    c.execute("INSERT INTO ip_pools(tenant_id,pool_name,range_ip,local_ip,router_id,created_at) "
                              "VALUES(1,?,?,?,?,?)", ("نطاق الهوت سبوت", "10.20.0.2-10.20.7.254",
                                                      "10.20.0.1", rid, "2026-01-01"))
        except Exception as e:  # noqa: BLE001
            print("seed pools skip:", e)

        # ── subscribers (كثيفة، أسماء طويلة) ──
        subs = [
            ("ahmad.alharbi", "أحمد بن عبدالله الحربي", "0590000001", 2, "PPPoE", "enabled"),
            ("sara.alotaibi", "سارة بنت محمد العتيبي", "0590000002", 2, "PPPoE", "enabled"),
            ("khaled.q", "خالد القحطاني الدوسري", "0590000003", 1, "Hotspot", "enabled"),
            ("noura.shammari", "نورة الشمري", "0590000004", 3, "PPPoE", "disabled"),
            ("faisal.d", "فيصل بن سعد الدوسري", "0590000005", 1, "Hotspot", "enabled"),
            ("huda.m", "هدى المطيري", "0590000006", 2, "PPPoE", "expired"),
            ("omar.z", "عمر الزهراني", "0590000007", 1, "Hotspot", "enabled"),
            ("layla.h", "ليلى حسن إبراهيم", "0590000008", 3, "PPPoE", "enabled"),
            ("yousef.k", "يوسف كمال", "0590000009", 1, "Hotspot", "disabled"),
            ("mona.a", "منى عبدالرحمن السبيعي", "0590000010", 2, "PPPoE", "enabled"),
            ("tariq.s", "طارق صالح", "0590000011", 1, "Hotspot", "enabled"),
            ("rania.f", "رانيا فؤاد", "0590000012", 3, "PPPoE", "enabled"),
            ("ali.n", "علي ناصر العمري", "0590000013", 1, "Hotspot", "expired"),
            ("dina.w", "دينا وليد", "0590000014", 2, "PPPoE", "enabled"),
            ("sami.t", "سامي التميمي", "0590000015", 1, "Hotspot", "enabled"),
            ("hana.b", "هناء بدر", "0590000016", 2, "PPPoE", "enabled"),
            ("majed.r", "ماجد الرشيدي", "0590000017", 1, "Hotspot", "disabled"),
            ("amal.s", "أمل السهلي", "0590000018", 3, "PPPoE", "enabled"),
            ("nawaf.g", "نواف الغامدي", "0590000019", 1, "Hotspot", "enabled"),
            ("reem.a", "ريم العنزي", "0590000020", 2, "PPPoE", "enabled"),
            ("badr.h", "بدر الحارثي", "0590000021", 1, "Hotspot", "enabled"),
            ("salwa.m", "سلوى مبارك", "0590000022", 2, "PPPoE", "enabled"),
        ]
        try:
            with transaction() as c:
                for i, (u, n, m, pid, st, status) in enumerate(subs):
                    c.execute(
                        "INSERT INTO subscribers(tenant_id, username, full_name, mobile, "
                        "plan_id, service_type, status, created_at) "
                        "VALUES(1,?,?,?,?,?,?,'2026-02-01')", (u, n, m, pid, st, status))

                    def acct(user, dl, ul, active, nasip):
                        c.execute(
                            "INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress, "
                            "callingstationid, framedipaddress, acctstarttime, acctstoptime, "
                            "acctsessiontime, acctinputoctets, acctoutputoctets) "
                            "VALUES(1,?,?,?,?,?,?,?,?,?,?)",
                            (f"{user}-s{active}", user, nasip,
                             f"AA:BB:CC:{i:02d}:11:22", f"10.20.0.{i+2}",
                             "2026-06-22 09:00:00",
                             None if active else "2026-06-22 12:00:00",
                             10800, int(dl * GB), int(ul * GB)))
                    acct(u, (i % 9) + 1, (i % 4) + 1, active=(1 if i < 8 else 0),
                         nasip="10.0.0.1" if i % 2 else "10.0.1.1")
        except Exception as e:  # noqa: BLE001
            print("seed subscribers skip:", e)

        # ── cards ──
        try:
            if bid:
                with transaction() as c:
                    for i in range(28):
                        c.execute("INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,"
                                  "used,created_at) VALUES(1,?,?,?,1,?,?)",
                                  (bid, f"CARD{10000+i}", f"pw{i:04d}",
                                   1 if i % 3 == 0 else 0, "2026-05-01"))
        except Exception as e:  # noqa: BLE001
            print("seed cards skip:", e)

        # ── radpostauth (لإحصائيات المتصلين الفاشلة) ──
        try:
            with transaction() as c:
                for i, u in enumerate(["ahmad.alharbi", "ghost.user", "sara.alotaibi",
                                       "bad.login", "khaled.q", "unknown99"]):
                    reply = "Access-Reject" if i % 2 else "Access-Accept"
                    c.execute("INSERT INTO radpostauth(tenant_id,username,reply,authdate,"
                              "nas) VALUES(1,?,?,?,?)",
                              (u, reply, "2026-06-22 10:0%d:00" % i, "10.0.0.1"))
        except Exception as e:  # noqa: BLE001
            print("seed radpostauth skip:", e)

        # ── store users + deposits/withdrawals (لصفحة دعم المتجر) ──
        try:
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            from app.radius.services.store_deposits import DepositRequestService
            from app.radius.services.store_withdrawals import WithdrawalRequestService
            Market = CardUsersMarketplaceService(tenant_id=1)
            dep = DepositRequestService(tenant_id=1)
            wd = WithdrawalRequestService(tenant_id=1)
            u1 = Market.create_card_user(display_name="سامي التاجر الكبير", mobile="0590000110")["id"]
            u2 = Market.create_card_user(display_name="ليلى حسن للتجارة", mobile="0590000111")["id"]
            dep.create_request(card_user_id=u1, amount_claimed="250.00", method="bank",
                               payer_name="سامي التاجر", payer_phone="0590000110")
            r2 = dep.create_request(card_user_id=u2, amount_claimed="400.00", method="cash",
                                    payer_name="ليلى حسن", payer_phone="0590000111")
            dep.confirm(r2["id"], actor="preview")
            Market.recharge_wallet(card_user_id=u1, amount="1000.00", actor="preview")
            wd.create_request(card_user_id=u1, amount="150.00", payee_name="سامي التاجر",
                              payee_account="SA0380000000608010167519")
        except Exception as e:  # noqa: BLE001
            print("seed store skip:", e)

        # ── print templates ──
        try:
            from app.radius.db.repos import operations_repo
            operations_repo.create_print_template(1, {"name": "قالب A4 — 3×5 بطاقات"}, actor="preview")
            operations_repo.create_print_template(1, {"name": "قالب ليتر أفقي — 4×6"},
                                                  actor="preview")
        except Exception as e:  # noqa: BLE001
            print("seed print templates skip:", e)

        # ── extra admins/distributors ──
        try:
            with transaction() as c:
                for u, n in [("dist_north", "موزّع المنطقة الشمالية"),
                             ("dist_south", "موزّع المنطقة الجنوبية"),
                             ("op_support", "موظّف الدعم الفنّي")]:
                    c.execute("INSERT INTO admins(username,full_name,password_hash,"
                              "is_super_admin,enabled,created_at) VALUES(?,?,?,0,1,'2026-01-01')",
                              (u, n, "x"))
        except Exception as e:  # noqa: BLE001
            print("seed admins skip:", e)


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


# (name, path) — كل صفحات الشريط الجانبي
PAGES = [
    ("dashboard", "/admin/radius/"),
    ("subs_overview", "/admin/radius/subscribers/overview"),
    ("subs_360", "/admin/radius/subscribers"),
    ("subs_new", "/admin/radius/users/new"),
    ("subs_groups", "/admin/radius/subscriber-groups"),
    ("subs_online", "/admin/radius/online"),
    ("subs_connected_stats", "/admin/radius/connected-stats"),
    ("subs_usage", "/admin/radius/reports/subscriber-consumption"),
    ("cards_overview", "/admin/radius/cards/overview"),
    ("cards_checker", "/admin/radius/cards/checker"),
    ("cards_batches", "/admin/radius/cards/batches"),
    ("cards_generate", "/admin/radius/cards/generate"),
    ("cards_print", "/admin/radius/cards/print"),
    ("cards_templates", "/admin/radius/print-templates"),
    ("ecards_marketplace", "/admin/radius/card-marketplace"),
    ("ecards_users", "/admin/radius/card-users"),
    ("ecards_store_support", "/admin/radius/store-support"),
    ("offers_overview", "/admin/radius/plans/overview"),
    ("offers_list", "/admin/radius/plans"),
    ("offers_new", "/admin/radius/plans/new"),
    ("offers_bw", "/admin/radius/bandwidth"),
    ("net_routers_ops", "/admin/radius/mt/operations"),
    ("net_devices", "/admin/radius/network/devices"),
    ("net_pools", "/admin/radius/pools"),
    ("net_device_health", "/admin/radius/device-health"),
    ("net_speed_sched", "/admin/radius/operations/speed-control"),
    ("net_alerts", "/admin/radius/alerts"),
    ("net_audit", "/admin/radius/audit"),
    ("net_setup_quick", "/admin/radius/mt/setup"),
    ("fin_recharge", "/admin/radius/recharge"),
    ("fin_center", "/admin/radius/finance-center"),
    ("fin_accounting", "/admin/radius/finance/accounting"),
    ("fin_billing", "/admin/radius/finance/billing"),
    ("fin_inventory", "/admin/radius/company-inventory"),
    ("ops_communications", "/admin/radius/communications"),
    ("ops_events", "/admin/radius/events"),
    ("ops_center", "/admin/radius/operations"),
    ("reports_home", "/admin/radius/reports"),
    ("reports_sessions", "/admin/radius/reports/sessions"),
    ("reports_login_states", "/admin/radius/reports/login_states"),
    ("reports_financial", "/admin/radius/reports/financial"),
    ("notif_center", "/admin/radius/notifications"),
    ("notif_integrations", "/admin/radius/integrations"),
    ("notif_admin", "/admin/radius/admin-notifications"),
    ("notif_subscriber", "/admin/radius/subscriber-notifications"),
    ("support_tickets", "/admin/radius/tickets"),
    ("support_services", "/admin/radius/services"),
    ("support_portals", "/admin/radius/customer-portals"),
    ("admin_operators", "/admin/radius/business-operators"),
    ("admin_roles", "/admin/radius/roles"),
    ("admin_settings", "/admin/radius/settings"),
    ("admin_system", "/admin/radius/settings/system"),
    ("admin_backups", "/admin/radius/backups"),
    ("integ_webhooks", "/admin/radius/webhooks"),
    ("integ_tunnels", "/admin/radius/tunnels"),
    ("integ_tokens", "/admin/radius/tokens"),
    ("integ_bridge", "/admin/radius/admin-bridge"),
]

# قياس آليّ للتجاوز عند 390px
PROBE_JS = r"""
() => {
  const de = document.scrollingElement || document.documentElement;
  const bodyOverflow = Math.max(0, de.scrollWidth - de.clientWidth);
  const tables = [];
  document.querySelectorAll('[data-uds-table], .hub-table-wrap, .uds-table-wrap, table').forEach((el) => {
    let wrap = el.matches('table') ? (el.closest('[data-uds-table],.hub-table-wrap,.uds-table-wrap,.table-scroll') || el.parentElement) : el;
    const tbl = el.matches('table') ? el : el.querySelector('table');
    if (!tbl || !wrap) return;
    const over = tbl.scrollWidth - wrap.clientWidth;
    if (over > 2) {
      const cs = getComputedStyle(wrap);
      tables.push({ over: Math.round(over), wrapClass: wrap.className || wrap.tagName,
                    overflowX: cs.overflowX, tblW: Math.round(tbl.scrollWidth),
                    wrapW: Math.round(wrap.clientWidth) });
    }
  });
  return { bodyOverflow: Math.round(bodyOverflow), winW: window.innerWidth, tables };
}
"""


def main() -> None:
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    tmp = tempfile.mkdtemp(prefix="resp_audit_")
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

    prefix = (TAG + "_") if TAG else ""
    report = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        # سياق جوّال حقيقي
        mob = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=3, is_mobile=True, has_touch=True,
                            locale="ar")
        mob.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        desk = b.new_context(viewport={"width": 1440, "height": 900},
                             device_scale_factor=1, locale="ar")
        desk.add_cookies([{"name": "session", "value": cookie,
                           "domain": "127.0.0.1", "path": "/"}])
        mp = mob.new_page()
        dp = desk.new_page()

        for name, path in PAGES:
            if wanted and not any(w in name for w in wanted):
                continue
            url = f"http://127.0.0.1:{PORT}{path}"
            entry = {"name": name, "path": path}
            # ── MOBILE 390 ──
            try:
                resp = mp.goto(url, wait_until="load", timeout=25000)
                entry["status"] = resp.status if resp else None
                mp.wait_for_timeout(1100)
                probe = mp.evaluate(PROBE_JS)
                entry["mobile"] = probe
                mp.screenshot(path=os.path.join(OUT_DIR, f"{prefix}{name}_390.png"),
                              full_page=True)
            except Exception as e:  # noqa: BLE001
                entry["mobile_error"] = str(e)[:200]
            # ── DESKTOP 1440 ──
            try:
                dp.goto(url, wait_until="load", timeout=25000)
                dp.wait_for_timeout(900)
                dp.screenshot(path=os.path.join(OUT_DIR, f"{prefix}{name}_1440.png"),
                              full_page=True)
            except Exception as e:  # noqa: BLE001
                entry["desktop_error"] = str(e)[:200]
            mflag = entry.get("mobile", {})
            print(f"{name:24s} status={entry.get('status')} "
                  f"bodyOverflow={mflag.get('bodyOverflow')} "
                  f"tables_over={len(mflag.get('tables', []))}")
            report.append(entry)

        b.close()
    srv.shutdown()

    with open(os.path.join(OUT_DIR, f"{prefix}_audit.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    # ملخّص الأعطال
    print("\n==== DEFECT SUMMARY (390px) ====")
    for e in report:
        m = e.get("mobile", {})
        bo = m.get("bodyOverflow", 0)
        to = m.get("tables", [])
        if bo > 2 or to or e.get("status") not in (200, None) or e.get("mobile_error"):
            print(f"  {e['name']:24s} status={e.get('status')} bodyOverflow={bo}px "
                  f"tables={[t['over'] for t in to]} "
                  f"{e.get('mobile_error','')}")


if __name__ == "__main__":
    main()
