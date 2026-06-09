"""Security regression: RBAC guard on sensitive admin routes.

Before the fix the app applied NO per-permission check to any web route
(only a login guard), so any authenticated admin — including a low-priv
operator — could POST to admin/role management, tenants, backups, settings
and ledger-void endpoints (privilege escalation to super-admin).

These tests prove:
  * a non-super-admin (seeded `operator`) is blocked (403) from the
    sensitive endpoints, but can still reach allowed pages, and
  * a super-admin (`admin`) is NOT blocked.
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


def _login(client, user, pw):
    return client.post(
        "/admin/radius/login",
        data={"username": user, "password": pw},
        follow_redirects=False,
    )


def _csrf(client, url="/admin/radius/"):
    client.get(url)
    with client.session_transaction() as sess:
        return sess.get("_csrf_token")


# (url, extra form data) — all should be blocked for a non-super-admin
SENSITIVE_POSTS = [
    ("/admin/radius/admins", {"username": "qa_x", "password": "qa_y"}),
    ("/admin/radius/settings", {"currency": "ILS"}),
    ("/admin/radius/backups/restore", {"name": "x"}),
    ("/admin/radius/finance/ledger/void", {"entry_id": "1", "reason": "x"}),
    ("/admin/radius/tenants/1", {"name": "x"}),
]


def test_operator_is_blocked_from_sensitive_routes(client):
    r = _login(client, "operator", "operator")
    assert r.status_code in {302, 303}, "operator login should succeed"
    tok = _csrf(client)
    for url, data in SENSITIVE_POSTS:
        payload = dict(data)
        payload["_csrf_token"] = tok
        res = client.post(url, data=payload, follow_redirects=False)
        assert res.status_code == 403, f"{url} expected 403, got {res.status_code}"
    # not over-blocked: operator can still reach allowed pages
    assert client.get("/admin/radius/").status_code == 200
    assert client.get("/admin/radius/users").status_code == 200


def test_super_admin_is_not_blocked(client):
    r = _login(client, "admin", "admin")
    assert r.status_code in {302, 303}
    # super-admin can view settings and is not 403'd on a guarded write
    assert client.get("/admin/radius/settings").status_code == 200
    tok = _csrf(client)
    res = client.post(
        "/admin/radius/finance/ledger/void",
        data={"entry_id": "999999", "reason": "qa", "_csrf_token": tok},
        follow_redirects=False,
    )
    assert res.status_code != 403, "super-admin must not be blocked by the RBAC guard"
