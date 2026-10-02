"""QA: PDF export endpoints must return a real PDF (200, %PDF-), not empty.

Guards the cards-batches and finance-reports PDF exports. (On the live VPS
these returned 204/empty due to a missing reportlab dependency / stale
deploy; the code itself produces a valid PDF, which this test locks in.)
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app():
    # Test-safe app: no demo seed + no workers. Without HOBERADIUS_NO_SEED the
    # license-lifecycle gate's test bypass is not armed (dual key, see
    # conftest) and every page 302s to /_license/activate; and the demo
    # «admin/admin» login this test used to rely on is gone — a fresh install
    # boots admin #1 with a real password (commit d13fb302).
    mp = pytest.MonkeyPatch()
    mp.setenv("HOBERADIUS_NO_SEED", "1")
    mp.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.core.constants import ROLE_SUPER_ADMIN
        from app.radius.db.repos import admins_repo
        admins_repo.ensure_default_roles()
        role = admins_repo.get_role_by_name(ROLE_SUPER_ADMIN)
        # «مدير عام» role = every non-owner-only permission (exports included).
        admins_repo.create_admin(username="qa_pdf_exporter", password="qa-pdf-pass-1",
                                 full_name="QA PDF", role_id=role.id,
                                 is_super_admin=True)
    yield flask_app
    mp.undo()


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "qa_pdf_exporter", "password": "qa-pdf-pass-1"})
    assert res.status_code in (302, 303)


@pytest.mark.parametrize("url", [
    "/admin/radius/cards/batches/export.pdf",
    "/admin/radius/finance/reports/export.pdf",
])
def test_pdf_export_returns_valid_pdf(client, url):
    _login(client)
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    body = r.get_data()
    assert body[:5] == b"%PDF-", f"{url} did not return a PDF (first bytes: {body[:8]!r})"
    assert len(body) > 200, f"{url} PDF suspiciously small ({len(body)} bytes)"
