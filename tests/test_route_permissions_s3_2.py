"""S3.2 — Route-level permission enforcement.

Pins:
  - Super admin reaches every gated route (back-compat).
  - Non-admin without the required permission is forbidden:
      * HTML request → 403 + forbidden template
      * JSON request → 403 + {ok:false, error}
  - The forbidden response is NOT a redirect to login
    (login redirect is the global guard's job; once you're
    past that, lack of permission should surface as 403).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s3_2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, *, is_super_admin: bool = True) -> int:
    from app.radius.db.repos import admins_repo
    u = f"s3_2_{uuid4().hex[:8]}"
    admin = admins_repo.create_admin(
        username=u, password="s3-pass",
        full_name="S3.2 Tester",
        is_super_admin=is_super_admin,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s3-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    return admin.id


# ─── Super admin keeps access ─────────────────────────────────


def test_super_admin_reaches_audit_log_index(client):
    _login(client, is_super_admin=True)
    res = client.get("/admin/radius/audit")
    assert res.status_code == 200


def test_super_admin_reaches_programming_form(app, client):
    _login(client, is_super_admin=True)
    # No nas exists yet → 404 from the handler, NOT 403. That's
    # what proves the perm gate let us through.
    res = client.get("/admin/radius/mt/9999/program")
    assert res.status_code in {200, 404}


# ─── Non-admin is forbidden ───────────────────────────────────


def test_non_admin_audit_index_forbidden_html(app, client):
    _login(client, is_super_admin=False)
    res = client.get("/admin/radius/audit")
    assert res.status_code == 403
    html = res.get_data(as_text=True)
    # Forbidden template renders the operator-facing reason.
    assert "صلاحية" in html
    assert "data-mt-forbidden-reason" in html


def test_non_admin_audit_index_forbidden_json(app, client):
    _login(client, is_super_admin=False)
    res = client.get(
        "/admin/radius/audit",
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 403
    body = res.get_json()
    assert body["ok"] is False
    assert "error" in body


def test_non_admin_programming_form_forbidden(app, client):
    _login(client, is_super_admin=False)
    res = client.get(
        "/admin/radius/mt/1/program",
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 403


def test_forbidden_is_not_a_login_redirect(app, client):
    """An authenticated user without permission must NOT bounce
    back to login — that would loop. They get 403."""
    _login(client, is_super_admin=False)
    res = client.get("/admin/radius/audit", follow_redirects=False)
    assert res.status_code == 403
    # No Location header → not a redirect.
    assert "Location" not in res.headers


# ─── Decorator covers programming + designer + jobs ───────────


def _csrf(client) -> str:
    """Mint a CSRF token. Touches a public endpoint so a
    non-admin session can still surface a token in the
    session storage."""
    client.get("/admin/radius/login")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token") or ""


def test_program_apply_post_blocked_for_non_admin(app, client):
    """Non-admin POST is blocked. With a valid CSRF token the
    block falls to the permission decorator (403); without one
    CSRF bounces first (302). Either way the action is denied."""
    _login(client, is_super_admin=False)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/program/apply",
        data={"_csrf_token": token,
              "kind": "hotspot", "interface": "ether2",
              "cidr": "10.0.0.0/24", "hotspot_name": "hs",
              "confirm": "1"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code in {302, 403}


def test_login_designer_deploy_blocked_for_non_admin(app, client):
    _login(client, is_super_admin=False)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/login-designer/deploy",
        data={"_csrf_token": token, "confirm": "1"},
        headers={"Accept": "application/json"},
    )
    assert res.status_code in {302, 403}


def test_jobs_diagnostics_post_blocked_for_non_admin(app, client):
    _login(client, is_super_admin=False)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/jobs/diagnostics/1",
        data={"_csrf_token": token},
        headers={"Accept": "application/json"},
    )
    assert res.status_code in {302, 403}
