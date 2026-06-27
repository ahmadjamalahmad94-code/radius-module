"""Regression: the «تجهيز ملف البطاقات» print modal must carry a CSRF token.

The shared print modal (`_card_print_modal.html`) is submitted by
`cards_batches_view.js` via `fetch(... method:"POST" ...)` with a plain
`FormData` body and no `X-CSRFToken` header. The form therefore has to
ship the `_csrf_token` hidden field itself.

Historically the form tag had no ``method="post"``, so the global
after-request CSRF injector (which only rewrites ``<form method=post>``)
never added the token. The export-job POST then hit the global CSRF guard
and came back **HTTP 400** — the owner saw it as «فشل تجهيز الملف».

These tests pin the fix: the rendered form contains a token, the POST
succeeds (202) with it, and is rejected (400) without it.
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # Dual-key license-gate bypass (combines with the conftest's
    # HOBERADIUS_LICENSE_GATE_TEST_BYPASS) so the admin panel renders
    # instead of redirecting to /_license/activate.
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"print_csrf_{uuid4().hex[:10]}"
    password = "print-csrf-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Print CSRF Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _session_csrf(client) -> str:
    # The session token is minted lazily on the first rendered page; hit one
    # so `_csrf_token` exists before we read it back.
    client.get("/admin/radius/print-templates")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _create_template(client) -> int:
    token = _session_csrf(client)
    res = client.post(
        "/admin/radius/print-templates",
        data={"_csrf_token": token, "name": f"qa-{uuid4().hex[:6]}"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}, res.status_code
    from app.radius.services.operations import get_operations_service

    templates = get_operations_service().list_print_templates(tenant_id=1, limit=10)
    assert templates, "template was not created"
    return int(templates[0]["id"])


# The form's opening tag through to its closing tag — we assert the CSRF
# token lives INSIDE this specific form, not merely somewhere on the page.
_PRINT_FORM_RE = re.compile(
    r"<form[^>]*data-batch-print-form[^>]*>(.*?)</form>",
    re.IGNORECASE | re.DOTALL,
)


def test_print_modal_form_carries_csrf_token(client):
    """The print modal form must embed a `_csrf_token` hidden input."""
    _web_login(client)
    page = client.get("/admin/radius/cards/print")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    match = _PRINT_FORM_RE.search(html)
    assert match, "data-batch-print-form not found on the print list page"
    form_html = match.group(0)
    assert "_csrf_token" in form_html, (
        "print modal form is missing its CSRF token — the export POST "
        "would be rejected with HTTP 400"
    )


def test_export_job_start_rejected_without_csrf(client):
    """Sanity: the export-job endpoint is genuinely CSRF-guarded."""
    _web_login(client)
    template_id = _create_template(client)
    res = client.post(
        f"/admin/radius/print-templates/{template_id}/export-jobs",
        data={"print_page_size": "A4"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 400


def test_export_job_start_succeeds_with_csrf(client):
    """With the token the modal ships, the export job is accepted (202)."""
    _web_login(client)
    template_id = _create_template(client)
    token = _session_csrf(client)
    res = client.post(
        f"/admin/radius/print-templates/{template_id}/export-jobs",
        data={"_csrf_token": token, "print_page_size": "A4"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 202, res.get_data(as_text=True)
    payload = res.get_json()
    assert payload and payload.get("ok") is True
    assert payload.get("status_url") and payload.get("download_url")
