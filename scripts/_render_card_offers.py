"""Render /admin/radius/cards/offers exactly as the panel serves it (super-admin),
seeded with a couple of offers (one with a direct speed, one without) + a manager
to show the sharing UI, then rewrite /static → file:// for an offline headless
screenshot. Mirrors scripts/_render_403.py.
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
            price, currency, speed_down_kbps, speed_up_kbps, created_at, updated_at)
        VALUES(1,'خطة منزلية',480,1,5.0,'JOD',4096,2048,datetime('now'),datetime('now'))
        """,
    )
    plan_id = int(cur.lastrowid)

    svc = CardOffersService(tenant_id=1)
    svc.create_offer(
        name="بطاقة 8 ساعات — سريعة", duration_minutes=8 * 60, wholesale="2.00",
        selling="5.00", plan_id=plan_id, speed_down_kbps=4096, speed_up_kbps=2048,
        notes="عرض مميّز بسرعة عالية", created_by="owner",
        visible_admin_ids=[mgr_id],
    )
    svc.create_offer(
        name="بطاقة يوم كامل", duration_minutes=1440, wholesale="3.50",
        selling="8.00", plan_id=plan_id, speed_down_kbps=0, speed_up_kbps=0,
        created_by="owner",
    )

with flask_app.test_client() as client:
    with client.session_transaction() as sess:
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
assert "عرض جديد" in html

html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")

with open(OUT_HTML, "w", encoding="utf-8") as fh:
    fh.write(html)

print("status=200 bytes=", len(html), "wrote", OUT_HTML)
