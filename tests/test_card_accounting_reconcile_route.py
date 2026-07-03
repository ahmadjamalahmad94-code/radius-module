"""FIX 3 route guard: the reconcile tool is OWNER-ONLY and dry-run-first.

  • non-owner (even a super-role admin) → 403 on page/plan/apply.
  • primary owner → 200 on the page + a working dry-run (plan) endpoint.
  • apply without the confirm word → refused (no backup, no mutation).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_fix3route_")
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


def _make_owner():
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(
        username=f"owner_{uuid4().hex[:8]}", password="owner-pass",
        full_name="Primary Owner", is_super_admin=True)


def _make_super_role_admin():
    from app.radius.core.constants import ALL_PERMISSIONS
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(
        name=f"superrole_{uuid4().hex[:6]}", display_name="Super-like",
        permissions=tuple(ALL_PERMISSIONS))
    return admins_repo.create_admin(
        username=f"mgr_{uuid4().hex[:8]}", password="mgr-pass",
        full_name="Super-role", role_id=role.id, is_super_admin=True)


def _login(client, username, password):
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": password},
                      follow_redirects=False)
    assert res.status_code in {302, 303}, res.status_code


def _csrf(client) -> str:
    # The session CSRF token is minted lazily on the first rendered page.
    # Hit the dashboard (accessible to any logged-in admin) so a non-owner
    # still gets a valid token — proving the 403 is the OWNER gate, not CSRF.
    client.get("/admin/radius/")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token", "")


def _post(client, url, body):
    token = _csrf(client)
    return client.post(url, json=body, headers={"X-CSRFToken": token})


PAGE = "/admin/radius/cards/reconcile-accounting"
PLAN = "/admin/radius/cards/reconcile-accounting/plan"
APPLY = "/admin/radius/cards/reconcile-accounting/apply"


def test_non_owner_super_role_is_forbidden(app):
    with app.app_context():
        _make_owner()                      # id #1 = the real owner
        nonowner = _make_super_role_admin()  # id #2
    client = app.test_client()
    _login(client, nonowner.username, "mgr-pass")
    assert client.get(PAGE).status_code == 403
    assert _post(client, PLAN, {}).status_code == 403
    assert _post(client, APPLY, {"confirm": "مطابقة"}).status_code == 403


def test_owner_can_open_page_and_run_dry_run(app):
    with app.app_context():
        owner = _make_owner()
    client = app.test_client()
    _login(client, owner.username, "owner-pass")
    assert client.get(PAGE).status_code == 200
    resp = _post(client, PLAN, {})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "plan" in body and "card_updates" in body["plan"]


def test_apply_without_confirm_is_refused(app):
    with app.app_context():
        owner = _make_owner()
    client = app.test_client()
    _login(client, owner.username, "owner-pass")
    resp = _post(client, APPLY, {"confirm": "wrong"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False
    assert body["code"] == "confirm"
