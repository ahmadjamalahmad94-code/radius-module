"""Render recycle-bin + lifecycle pages exactly as the panel serves them
(owner/super session), with /static rewritten to file:// for an offline
headless screenshot. Seeds a few archived items + a retention policy so the
pages render populated (not just empty states).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
# same bypass the test conftest uses so the license lifecycle gate doesn't
# redirect an un-activated dev DB to the activation page.
os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

OUT_DIR = os.path.abspath("_rclc_shots")
os.makedirs(OUT_DIR, exist_ok=True)
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")

db_file = os.path.join(tempfile.mkdtemp(), "render_rclc.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402


def _iso(days: int = 0) -> str:
    return (datetime.utcnow() + timedelta(days=days)).replace(
        microsecond=0).isoformat() + "Z"


flask_app = create_app()
with flask_app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.db.connection import transaction
    from app.radius.services import lifecycle

    run_pending_migrations()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    owner = admins_repo.create_admin(
        username="owner", password="x12345678", full_name="Owner",
        is_super_admin=True,
    )
    owner_id = int(owner.id)

    now = _iso()
    with transaction() as conn:
        conn.execute("INSERT INTO access_plans(tenant_id,name,enabled,created_at,"
                     "deleted_at,deleted_by,delete_reason,retention_expires_at,"
                     "archive_source) VALUES(1,'باقة قديمة',1,?,?,?,?,?,'manual')",
                     (now, _iso(-3), "owner", "تنظيف الباقات", _iso(60)))
        # an archived subscriber, still restorable
        conn.execute("INSERT INTO access_plans(tenant_id,name,enabled,created_at) "
                     "VALUES(1,'باقة حية',1,?)", (now,))
        plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute("INSERT INTO subscribers(tenant_id,username,password,plan_id,"
                     "status,expire_at,created_at,deleted_at,deleted_by,"
                     "delete_reason,archive_source,retention_expires_at) "
                     "VALUES(1,'ahmad_old','pw',?, 'disabled', ?, ?, ?, 'owner',"
                     "'انتهى الاشتراك','auto',?)",
                     (plan_id, _iso(-30), now, _iso(-2), _iso(45)))
        # an archived subscriber whose retention already expired (restore locked)
        conn.execute("INSERT INTO subscribers(tenant_id,username,password,plan_id,"
                     "status,expire_at,created_at,deleted_at,deleted_by,"
                     "delete_reason,archive_source,retention_expires_at) "
                     "VALUES(1,'sara_expired','pw',?, 'disabled', ?, ?, ?, 'owner',"
                     "'احتفاظ منتهٍ','auto',?)",
                     (plan_id, _iso(-120), now, _iso(-100), _iso(-1)))

    lifecycle.create_policy(1, {
        "entity_type": "card", "trigger_type": "expired_at",
        "delay_value": 7, "delay_unit": "days",
        "retention_value": 90, "retention_unit": "days", "enabled": True,
    }, actor="owner")
    lifecycle.create_policy(1, {
        "entity_type": "subscriber", "trigger_type": "expired_at",
        "delay_value": 3, "delay_unit": "months",
        "retention_value": 180, "retention_unit": "days", "enabled": True,
    }, actor="owner")

PAGES = {
    "recycle_bin": "/admin/radius/recycle-bin",
    "lifecycle": "/admin/radius/lifecycle",
}

with flask_app.test_client() as client:
    lr = client.post("/admin/radius/login",
                     data={"username": "owner", "password": "x12345678"},
                     follow_redirects=False)
    assert lr.status_code in {302, 303}, f"login -> {lr.status_code}"
    for name, url in PAGES.items():
        res = client.get(url, follow_redirects=False)
        assert res.status_code == 200, f"{url} -> {res.status_code}"
        html = res.get_data(as_text=True)
        html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
        html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
        html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")
        out = os.path.join(OUT_DIR, f"{name}.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"wrote {out}  bytes={len(html)}  status={res.status_code}")
