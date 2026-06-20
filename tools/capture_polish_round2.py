# -*- coding: utf-8 -*-
"""لقطات PNG (سطح مكتب + جوّال ~390px) لصفحات جولة الصقل 2:
  دعم المتجر (جداول موحّدة) · التقارير المالية (لقطة مطويّة في الشريط) ·
  إعدادات النظام (انهيار الجوّال) · أجهزة الشبكة (أزرار أيقونية) ·
  دفعات الكروت + قائمة الطباعة (نافذة الطباعة المشتركة — تُفتَح للّقطة).

التشغيل:  python tools/capture_polish_round2.py
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
PORT = 5396


def _seed(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()

        # ── راوتر + جهازا شبكة (لإظهار أزرار الإجراءات الأيقونية) ──
        try:
            with transaction() as c:
                c.execute("INSERT INTO nas_devices(tenant_id,name,shortname,address,"
                          "secret,vendor,nas_type,created_at) VALUES(1,?,?,?,?,?,?,?)",
                          ("راوتر المبنى", "rb1", "10.0.0.1", "s", "mikrotik",
                           "other", "2026-01-01"))
                rid = c.execute("SELECT id FROM nas_devices WHERE tenant_id=1").fetchone()["id"]
            from app.radius.db.repos import network_devices_repo
            network_devices_repo.create(tenant_id=1, router_id=rid, name="سويتش الطابق 1",
                                        device_type="switch", ip_address="10.0.0.21",
                                        location="الطابق 1", watch_enabled=True)
            network_devices_repo.create(tenant_id=1, router_id=rid, name="أكسس بوينت السطح",
                                        device_type="ap", ip_address="10.0.0.22",
                                        location="السطح", is_critical=True)
        except Exception as e:  # noqa: BLE001
            print("seed network skip:", e)

        # ── قوالب طباعة + دفعة كروت (لتعبئة نافذة الطباعة + صفحة الدفعات) ──
        try:
            from app.radius.db.repos import operations_repo
            operations_repo.create_print_template(1, {"name": "قالب A4 — 3×5"}, actor="preview")
            operations_repo.create_print_template(1, {"name": "قالب ليتر — أفقي",
                                                      "page_size": "Letter",
                                                      "orientation": "landscape"}, actor="preview")
            with transaction() as c:
                c.execute("INSERT INTO access_plans(id,tenant_id,name,created_at) "
                          "VALUES(1,1,?,?)", ("باقة الساعة", "2026-01-01"))
                c.execute("INSERT INTO card_batches(tenant_id,batch_code,package_name,"
                          "plan_id,count,created_at) VALUES(1,'B-2026-1',?,1,50,'2026-05-01')",
                          ("حزمة 50 كرت",))
        except Exception as e:  # noqa: BLE001
            print("seed cards skip:", e)

        # ── دعم المتجر: طلبات شحن/سحب (معلّقة + محسومة) ──
        try:
            from app.radius.services.card_users_marketplace import CardUsersMarketplaceService
            from app.radius.services.store_deposits import DepositRequestService
            from app.radius.services.store_withdrawals import WithdrawalRequestService
            Market = CardUsersMarketplaceService(tenant_id=1)
            dep = DepositRequestService(tenant_id=1)
            wd = WithdrawalRequestService(tenant_id=1)
            u1 = Market.create_card_user(display_name="سامي التاجر", mobile="0590000010")["id"]
            u2 = Market.create_card_user(display_name="ليلى حسن", mobile="0590000011")["id"]
            # شحن: معلّق + محسوم (مؤكَّد)
            dep.create_request(card_user_id=u1, amount_claimed="25.00", method="bank",
                               payer_name="سامي التاجر", payer_phone="0590000010")
            r2 = dep.create_request(card_user_id=u2, amount_claimed="40.00", method="cash",
                                    payer_name="ليلى حسن", payer_phone="0590000011")
            dep.confirm(r2["id"], actor="preview")
            # سحب: معلّق + محسوم (بعد شحن المحفظة)
            Market.recharge_wallet(card_user_id=u1, amount="100.00", actor="preview")
            wd.create_request(card_user_id=u1, amount="15.00", payee_name="سامي التاجر",
                              payee_account="0011223344")
            r4 = wd.create_request(card_user_id=u1, amount="10.00", payee_name="سامي التاجر",
                                   payee_account="0011223344")
            wd.confirm(r4["id"], actor="preview")
        except Exception as e:  # noqa: BLE001
            print("seed store skip:", e)


def _cookie(app):
    from flask.sessions import SecureCookieSessionInterface
    s = SecureCookieSessionInterface().get_signing_serializer(app)
    return s.dumps({"admin_id": 1, "admin_user": "preview", "admin_name": "معاينة",
                    "is_super_admin": True, "tenant_id": 1, "_csrf_token": "preview"})


# (name, path, open_modal?)
PAGES = [
    ("store_support_polish", "/admin/radius/store-support", False),
    ("accounting_reports_polish", "/admin/radius/finance/reports", False),
    ("system_settings_polish", "/admin/radius/settings/system", False),
    ("network_devices_polish", "/admin/radius/network/devices", False),
    ("cards_batches_print_modal", "/admin/radius/cards/batches", True),
    ("cards_print_list_modal", "/admin/radius/cards/print", True),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="polish2_")
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
    time.sleep(0.6)
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1366, "height": 900},
                            device_scale_factor=2, locale="ar")
        ctx.add_cookies([{"name": "session", "value": cookie,
                          "domain": "127.0.0.1", "path": "/"}])
        pg = ctx.new_page()
        for name, path, open_modal in PAGES:
            url = f"http://127.0.0.1:{PORT}{path}"
            pg.set_viewport_size({"width": 1366, "height": 900})
            pg.goto(url, wait_until="load", timeout=20000)
            pg.wait_for_timeout(900)
            if open_modal:
                pg.eval_on_selector("[data-batch-print-modal]",
                                    "el => { el.hidden = false; }")
                pg.wait_for_timeout(300)
            pg.screenshot(path=os.path.join(PREVIEW_DIR, f"{name}.png"), full_page=True)
            print("DESKTOP:", name)
            pg.set_viewport_size({"width": 390, "height": 844})
            pg.wait_for_timeout(400)
            if open_modal:
                pg.eval_on_selector("[data-batch-print-modal]",
                                    "el => { el.hidden = false; }")
                pg.wait_for_timeout(200)
            pg.screenshot(path=os.path.join(PREVIEW_DIR, f"{name}_mobile.png"), full_page=True)
            print("MOBILE:", name)
        b.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
