"""QA: PDF export endpoints must return a real PDF (200, %PDF-), not empty.

Guards the cards-batches and finance-reports PDF exports. (On the live VPS
these returned 204/empty due to a missing reportlab dependency / stale
deploy; the code itself produces a valid PDF, which this test locks in.)
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client):
    client.post("/admin/radius/login", data={"username": "admin", "password": "admin"})


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
