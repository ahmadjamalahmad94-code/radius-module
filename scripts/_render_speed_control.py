"""Render the redesigned speed-control page (with the new time-schedule panel)
as the panel serves it, /static rewritten to file:// for offline screenshots.
Seeds a plan + two schedules so the schedule table + controls render populated.
"""
from __future__ import annotations

import os
import tempfile

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

OUT_DIR = os.path.abspath("_spdctl_shots")
os.makedirs(OUT_DIR, exist_ok=True)
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")

db_file = os.path.join(tempfile.mkdtemp(), "render_spdctl.db")
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
    from app.radius.services.operations import get_operations_service

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
        conn.execute(
            "INSERT INTO access_plans(id,tenant_id,name,code,plan_type,service_type,"
            "duration_minutes,validity_days,speed_down_kbps,speed_up_kbps,price,"
            "currency,enabled,created_at) VALUES"
            "(2,1,'باقة منزلية 20','HOME20','time','PPPoE',1440,30,20000,10000,6,'JOD',1,?)",
            (now_iso(),),
        )

    svc = get_operations_service()
    svc.create_bandwidth_schedule(tenant_id=1, actor="owner", data={
        "name": "سرعة الليل", "target_type": "plan", "plan_id": 1, "priority": 3,
        "starts_at_time": "00:00", "ends_at_time": "06:00",
        "speed_down_kbps": 80000, "speed_up_kbps": 40000,
        "restore_mode": "profile_default", "enabled": True,
        "notes": "مضاعفة السرعة في ساعات الذروة المنخفضة"})
    svc.create_bandwidth_schedule(tenant_id=1, actor="owner", data={
        "name": "تخفيف وقت الذروة", "target_type": "plan", "plan_id": 2, "priority": 5,
        "starts_at_time": "19:00", "ends_at_time": "23:00",
        "speed_down_kbps": 12000, "speed_up_kbps": 6000,
        "cir_down_kbps": 4000, "cir_up_kbps": 2000,
        "restore_mode": "keep_current", "enabled": False,
        "notes": "تقليل الحمل وقت الذروة المسائية"})

with flask_app.test_client() as client:
    lr = client.post("/admin/radius/login",
                     data={"username": "owner", "password": "x12345678"},
                     follow_redirects=False)
    assert lr.status_code in {302, 303}, f"login -> {lr.status_code}"
    res = client.get("/admin/radius/operations/speed-control", follow_redirects=False)
    assert res.status_code == 200, f"page -> {res.status_code}"
    html = res.get_data(as_text=True)
    html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
    html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
    html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")
    out = os.path.join(OUT_DIR, "speed_control.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {out}  bytes={len(html)}  status={res.status_code}")
