"""Render the friendly 403 page exactly as the panel serves it, then write
HTML with /static rewritten to local file:// URLs so panel CSS/fonts resolve
for an offline headless screenshot. Mirrors tests/test_card_gen_validity_and_403.py.
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

db_file = os.path.join(tempfile.mkdtemp(), "render_403.db")
os.environ["HOBERADIUS_DB_PATH"] = db_file

from app.radius.db.connection import reset_for_tests  # noqa: E402

reset_for_tests(db_file)
from app import create_app  # noqa: E402

flask_app = create_app()
with flask_app.app_context():
    from app.radius.db.migrations_runner import run_pending_migrations
    from app.radius.db.repos import admins_repo, tenants_repo

    run_pending_migrations()
    tenants_repo.ensure_default_tenant()
    admins_repo.ensure_default_roles()
    mgr = admins_repo.create_admin(
        username="limited", password="x12345678", full_name="Limited Manager",
        is_super_admin=False,
    )
    mgr_id = int(mgr.id)

with flask_app.test_client() as client:
    with client.session_transaction() as sess:
        sess["admin_id"] = mgr_id
        sess["admin_user"] = "limited"
        sess["admin_name"] = "Limited Manager"
        sess["is_super_admin"] = False
        sess["tenant_id"] = 1
        sess["permissions"] = []
        sess["_csrf_token"] = "off-csrf"
    # Super-only write by a non-super admin → real @bp.errorhandler(403) fires.
    res = client.post(
        f"/admin/radius/admins/{mgr_id}",
        data={"_csrf_token": "off-csrf", "username": "x"},
        follow_redirects=False,
    )

assert res.status_code == 403, f"expected 403, got {res.status_code}"
html = res.get_data(as_text=True)
assert "data-mt-forbidden-page" in html, "not the friendly forbidden page"
assert "ليس لديك صلاحية الوصول إلى هذه الصفحة" in html
assert "إذا كنت تتوقع أن هذا خلل، راجع الإدارة." in html
assert "f403-art" in html, "animated SVG illustration missing from served HTML"

# Rewrite /static/... → file://<abs static root>/... so CSS/fonts resolve offline.
html = html.replace('"/static/', f'"file:///{STATIC_ROOT}/')
html = html.replace("'/static/", f"'file:///{STATIC_ROOT}/")
html = html.replace("url(/static/", f"url(file:///{STATIC_ROOT}/")

with open(OUT_HTML, "w", encoding="utf-8") as fh:
    fh.write(html)

print("status=403  bytes=", len(html))
print("svg_present=", "f403-art" in html)
print("wrote", OUT_HTML)
