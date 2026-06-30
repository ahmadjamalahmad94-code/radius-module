"""Render the «التحكم بالدخول» /access-control page as the panel serves it,
/static rewritten to file:// for offline screenshots. Seeds an owner + a plan +
a suspension + a manual block + an allow-mode policy so every major section
(تعليق الوصول / إعدادات الحظر الأمني / حظر يدوي / نمط السماح) renders populated.

Output HTML name is parameterised via argv[1] (default: access_control.html) so
the same script renders BEFORE and AFTER into distinct files.
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

OUT_DIR = os.path.abspath("_ac_spacing_shots")
os.makedirs(OUT_DIR, exist_ok=True)
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")
OUT_NAME = sys.argv[1] if len(sys.argv) > 1 else "access_control.html"

db_file = os.path.join(tempfile.mkdtemp(), "render_ac.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402

flask_app = create_app()
with flask_app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.db.helpers import now_iso
    from app.radius.db.connection import transaction

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
            "(1,1,'باقة الألياف 50','FIB50','time','PPPoE',1440,30,50000,25000,10,'JOD',1,?)",
            (now_iso(),),
        )

with flask_app.test_client() as client:
    lr = client.post("/admin/radius/login",
                     data={"username": "owner", "password": "x12345678"},
                     follow_redirects=False)
    assert lr.status_code in {302, 303}, f"login -> {lr.status_code}"

    with client.session_transaction() as s:
        s["_csrf_token"] = "tok"

    # seed a suspension (Layer A) + a manual block (Layer B) so both tables render
    client.post("/admin/radius/access-control/block",
                data={"block_type": "subscriber", "target": "ahmad",
                      "duration_mode": "permanent",
                      "reason": "إيقاف مؤقت للمراجعة", "_csrf_token": "tok"})
    client.post("/admin/radius/access-control/block",
                data={"block_type": "mac", "target": "AA:BB:CC:DD:EE:FF",
                      "duration_mode": "permanent",
                      "reason": "جهاز مشبوه", "_csrf_token": "tok"})

    res = client.get("/admin/radius/access-control", follow_redirects=False)
    assert res.status_code == 200, f"page -> {res.status_code}"
    html = res.get_data(as_text=True)
    html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
    html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
    html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")
    out = os.path.join(OUT_DIR, OUT_NAME)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {out}  bytes={len(html)}  status={res.status_code}")
