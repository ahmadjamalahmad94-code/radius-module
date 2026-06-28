"""Render the hotspot login-designer (owner session) with /static rewritten to
file:// for an offline headless screenshot — to verify the 3 under-image chips
are editable from the «المحتوى» (Content) tab. Seeds one hotspot router.
"""
from __future__ import annotations

import os
import tempfile

os.environ["HOBERADIUS_NO_WORKER"] = "1"
os.environ["HOBERADIUS_NO_SEED"] = "1"
os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

OUT_DIR = os.path.abspath("_designer_shots")
os.makedirs(OUT_DIR, exist_ok=True)
STATIC_ROOT = os.path.abspath("app/static").replace("\\", "/")

db_file = os.path.join(tempfile.mkdtemp(), "render_designer.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402

flask_app = create_app()

with flask_app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.core.types import NasDevice
    from app.radius.services.devices import get_nas_devices_service

    run_pending_migrations()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    admins_repo.create_admin(username="owner", password="x12345678",
                             full_name="Owner", is_super_admin=True)

    dev = NasDevice(
        id=None, name="hAP-Cafe", address="10.10.0.21", secret="s3cr3t-radius",
        vendor="mikrotik", nas_type="hotspot", api_port=8728,
        api_user="hobe-api", api_password="x", api_use_tls=False,
        enabled=True, monitoring_enabled=True,
    )
    saved = get_nas_devices_service().create(actor="render", device=dev)
    nas_id = int(saved.id)

with flask_app.test_client() as client:
    lr = client.post("/admin/radius/login",
                     data={"username": "owner", "password": "x12345678"},
                     follow_redirects=False)
    assert lr.status_code in {302, 303}, f"login -> {lr.status_code}"
    url = f"/admin/radius/mt/{nas_id}/login-designer"
    res = client.get(url, follow_redirects=False)
    assert res.status_code == 200, f"page -> {res.status_code}"
    html = res.get_data(as_text=True)
    html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
    html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
    html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")
    out = os.path.join(OUT_DIR, "login_designer.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    # quick assertions: the 6 chip editors + content tab are present
    for needle in ("data-mtld-tab=\"content\"", "CHIP1_TITLE", "CHIP1_SUB",
                   "CHIP2_TITLE", "CHIP3_SUB", "CHIPS_MANAGED",
                   "نصوص الرقائق تحت الصورة"):
        assert needle in html, f"missing: {needle}"
    print(f"wrote {out}  bytes={len(html)}  status={res.status_code}  nas_id={nas_id}")
