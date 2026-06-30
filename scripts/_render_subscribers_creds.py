"""Render the subscribers list page (with the new «إرسال بيانات المشترك» action
in the per-row «إدارية» menu), /static rewritten to file:// for an offline
headless screenshot. Seeds a few subscribers + a connected TweetSMS account.
"""
from __future__ import annotations

import os
import tempfile

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
os.environ["FLASK_SECRET"] = "render-creds-secret"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

OUT_DIR = os.path.abspath("_creds_shots")
os.makedirs(OUT_DIR, exist_ok=True)
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")

db_file = os.path.join(tempfile.mkdtemp(), "render_creds.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402

flask_app = create_app()
with flask_app.app_context():
    from app.radius.db.connection import transaction
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo, tenant_sms_settings_repo

    run_pending_migrations()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    admins_repo.create_admin(username="owner", password="x12345678",
                             full_name="Owner", is_super_admin=True)
    tenant_sms_settings_repo.upsert(
        tenant_id=1, provider="tweetsms",
        api_key="tw_live_9f3b2a7c41d8", sender="HOBE-NET", enabled=True,
    )
    rows = [
        ("ahmad.shop", "Pa55wd", "أحمد التاجر", "0599123456"),
        ("sara.net", "S3cret9", "سارة العلي", "0598765432"),
        ("kareem.hs", "Kx7yz!", "كريم حسن", "0569112233"),
    ]
    with transaction() as c:
        for u, p, name, mob in rows:
            c.execute(
                "INSERT INTO subscribers(tenant_id,username,password,full_name,"
                "mobile,status,created_at) VALUES(1,?,?,?,?,'enabled','2026-01-01')",
                (u, p, name, mob),
            )

with flask_app.test_client() as client:
    lr = client.post("/admin/radius/login",
                     data={"username": "owner", "password": "x12345678"},
                     follow_redirects=False)
    assert lr.status_code in {302, 303}, f"login -> {lr.status_code}"
    res = client.get("/admin/radius/users", follow_redirects=False)
    assert res.status_code == 200, f"page -> {res.status_code}"
    html = res.get_data(as_text=True)
    html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
    html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
    html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")
    out = os.path.join(OUT_DIR, "subscribers.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    assert "إرسال بيانات المشترك" in html, "new action button missing from rendered page"
    print(f"wrote {out}  bytes={len(html)}  status={res.status_code}")
