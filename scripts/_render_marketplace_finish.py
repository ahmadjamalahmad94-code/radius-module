"""Render the card-marketplace surfaces affected by the finish session and
rewrite /static to file:// for offline screenshots.

Seeds two offers (instant + inventory), inventory stock, and one purchase so
the offers gallery (with the persisted sale-system seg), the «آخر عمليات
الشراء» unified hub-table, and the offer-detail file (cards inside the offer)
all render populated.
"""
from __future__ import annotations

import os
import tempfile

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

OUT_DIR = os.path.abspath("_mkt_finish_shots")
os.makedirs(OUT_DIR, exist_ok=True)
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")

db_file = os.path.join(tempfile.mkdtemp(), "render_mkt_finish.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402

flask_app = create_app()
PKG_INV = None
PKG_INSTANT = None
with flask_app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.db.helpers import now_iso
    from app.radius.db.connection import transaction
    from app.radius.services.card_users_marketplace import CardUsersMarketplaceService

    run_pending_migrations()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    admins_repo.create_admin(username="owner", password="x12345678",
                             full_name="Owner", is_super_admin=True)

    with transaction() as conn:
        conn.execute(
            "INSERT INTO access_plans(id,tenant_id,name,code,plan_type,service_type,"
            "duration_minutes,validity_days,speed_down_kbps,speed_up_kbps,price,"
            "currency,enabled,created_at) VALUES"
            "(1,1,'بطاقة 8 ساعات','C8H','time','Hotspot',480,1,2048,512,5,'JOD',1,?)",
            (now_iso(),),
        )

    svc = CardUsersMarketplaceService(tenant_id=1)
    PKG_INV = svc.create_package(
        name="8 ساعات / 2 ميجا — مخزون", plan_id=1, duration_minutes=480,
        speed_down_kbps=2048, speed_up_kbps=512, price="5.00", sale_mode="inventory")
    PKG_INSTANT = svc.create_package(
        name="ساعة واحدة — توليد فوري", plan_id=1, duration_minutes=60,
        speed_down_kbps=2048, speed_up_kbps=512, price="1.50", sale_mode="instant")
    # مخزون للعرض المخزون (يَظهر في «المخزون المتبقّي» داخل ملف العرض).
    svc.add_inventory_stock(package_id=PKG_INV["id"], count=12, actor="owner",
                            password_length=8)


def _rewrite(html: str) -> str:
    html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
    html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
    html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")
    return html


PAGES = [
    ("card_marketplace", "/admin/radius/card-marketplace"),
    ("cards_offers", "/admin/radius/cards/offers"),
    ("offer_file_inventory",
     f"/admin/radius/card-marketplace/packages/{PKG_INV['id']}/file"),
]

with flask_app.test_client() as client:
    lr = client.post("/admin/radius/login",
                     data={"username": "owner", "password": "x12345678"},
                     follow_redirects=False)
    assert lr.status_code in {302, 303}, f"login -> {lr.status_code}"
    for name, url in PAGES:
        res = client.get(url, follow_redirects=False)
        assert res.status_code == 200, f"{name} {url} -> {res.status_code}"
        out = os.path.join(OUT_DIR, f"{name}.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(_rewrite(res.get_data(as_text=True)))
        print(f"wrote {out}  status={res.status_code}")
