"""Render /admin/radius/cards/offers exactly as the panel serves it, seeded with
a couple of offers over ready-made plans + a manager the offers are shared with,
then rewrite /static → file:// for an offline headless screenshot.

Usage: python scripts/_render_card_offers.py OUT.html [owner|manager]
Mirrors scripts/_render_403.py.
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

OUT_HTML = sys.argv[1]
ROLE = (sys.argv[2] if len(sys.argv) > 2 else "owner").lower()
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")

db_file = os.path.join(tempfile.mkdtemp(), "render_card_offers.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import db, reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402

flask_app = create_app()
with flask_app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.services.card_offers import CardOffersService

    run_pending_migrations()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    mgr = admins_repo.create_admin(
        username="reseller", password="x12345678", full_name="موزّع الشمال",
        is_super_admin=False,
    )
    mgr_id = int(mgr.id)

    cur = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
            price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,
            created_at, updated_at)
        VALUES(1,'باقة منزلية 4/2',480,1,5.0,'JOD',4096,2048,1024,datetime('now'),datetime('now'))
        """,
    )
    plan_fast = int(cur.lastrowid)
    cur = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
            price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,
            created_at, updated_at)
        VALUES(1,'باقة يوميّة 2/1',1440,1,8.0,'JOD',2048,1024,0,datetime('now'),datetime('now'))
        """,
    )
    plan_day = int(cur.lastrowid)

    svc = CardOffersService(tenant_id=1)
    svc.create_offer(
        name="بطاقة 8 ساعات", duration_minutes=8 * 60, wholesale="2.00",
        selling="5.00", plan_id=plan_fast, notes="الأكثر مبيعًا", created_by="owner",
        visible_admin_ids=[mgr_id],
    )
    svc.create_offer(
        name="بطاقة يوم كامل", duration_minutes=1440, wholesale="3.50",
        selling="8.00", plan_id=plan_day, created_by="owner",
        visible_admin_ids=[mgr_id],
    )

with flask_app.test_client() as client:
    with client.session_transaction() as sess:
        if ROLE == "manager":
            sess["admin_id"] = mgr_id
            sess["admin_user"] = "reseller"
            sess["admin_name"] = "موزّع الشمال"
            sess["is_super_admin"] = False
        else:
            sess["admin_id"] = 1
            sess["admin_user"] = "owner"
            sess["admin_name"] = "المالك"
            sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
    res = client.get("/admin/radius/cards/offers")

assert res.status_code == 200, f"expected 200, got {res.status_code}"
html = res.get_data(as_text=True)
assert "عروض البطاقات" in html
if ROLE == "manager":
    assert "data-offer-create" not in html, "manager must not see the create form"
    assert "عروض جاهزة من الإدارة" in html
else:
    assert "عرض جديد" in html

html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")

with open(OUT_HTML, "w", encoding="utf-8") as fh:
    fh.write(html)

print("status=200 bytes=", len(html), "wrote", OUT_HTML)
