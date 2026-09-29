"""p01/D06 — central /api/v1 permission guard.

* CI: every endpoint behind the admin API credential is mapped in
  ``API_PERMISSIONS`` or allow-listed in ``API_AUTH_ONLY``; every endpoint that
  is NOT behind it is listed in ``API_PUBLIC`` (a new unauthenticated endpoint
  is noticed too).
* Every ``web:`` mapping names a real web endpoint, and every mapped endpoint
  refuses a manager who holds no permission.
* Behaviour: owner token passes, a manager with the right key passes, a
  dashboard-only manager is refused on reads (voucher codes, ledger, payments,
  audit, tenants, license file) and money writes; unbound tokens unchanged.
* D13: the bare ``is_super_admin`` flag no longer manages admins; owner
  accounts are protected.
* D16: POST /distributors needs the «إدارة الموزعين» grant, like the web.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_apiguard_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    a.testing = True
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _uses_token(view) -> bool:
    x = view
    while x is not None:
        q = getattr(getattr(x, "__code__", None), "co_qualname", "")
        if q.startswith("require_api_token."):
            return True
        x = getattr(x, "__wrapped__", None)
    return False


def _owner(app):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        pid = admins_repo.primary_admin_id()
        if pid is None:
            admins_repo.create_admin(username="owner_x", password="owner-pass",
                                     full_name="Owner", is_super_admin=True)
            pid = admins_repo.primary_admin_id()
        return admins_repo.get_admin(pid)


def _manager(app, perms, *, is_super_admin=False):
    from app.radius.db.repos import admins_repo
    _owner(app)
    with app.app_context():
        role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}",
                                       display_name="r", permissions=tuple(perms))
        u = f"m_{uuid4().hex[:8]}"
        a = admins_repo.create_admin(username=u, password="pw-123456",
                                     full_name="M", is_super_admin=is_super_admin,
                                     role_id=role.id)
    return a, _bearer_for(app, a.id)


def _bearer_for(app, admin_id):
    """An app-login style token bound to the admin (``created_by``)."""
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        _rec, plain = api_tokens_repo.create_token(
            tenant_id=1, name=f"login:t{uuid4().hex[:6]}", scopes=["admin:full"],
            created_by=int(admin_id))
    return {"Authorization": f"Bearer {plain}"}


def _basic(username, password="pw-123456"):
    raw = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


# ───────────────────────── CI coverage ─────────────────────────

def test_every_authenticated_api_endpoint_is_mapped(app):
    from app.api.permission_guard import API_AUTH_ONLY, API_PERMISSIONS, API_PUBLIC
    unmapped, public_unlisted, mapped_public = [], [], []
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("api."):
            continue
        name = rule.endpoint[4:]
        if _uses_token(app.view_functions[rule.endpoint]):
            if name not in API_PERMISSIONS and name not in API_AUTH_ONLY:
                unmapped.append(f"{name} {rule.rule}")
        else:
            if name not in API_PUBLIC:
                public_unlisted.append(f"{name} {rule.rule}")
            if name in API_PERMISSIONS or name in API_AUTH_ONLY:
                mapped_public.append(name)
    assert not unmapped, ("API endpoints without a permission mapping — add them "
                          "to app/api/permission_guard.API_PERMISSIONS:\n"
                          + "\n".join(unmapped))
    assert not public_unlisted, ("API endpoints NOT behind the admin credential "
                                 "— add them to API_PUBLIC only if that is "
                                 "intended:\n" + "\n".join(public_unlisted))
    assert not mapped_public, mapped_public


def test_mapping_is_valid_and_denies_a_manager_without_permissions(app):
    from app.api.permission_guard import (API_AUTH_ONLY, API_PERMISSIONS,
                                          _resolve, decide)
    web = {r.endpoint[7:] for r in app.url_map.iter_rules()
           if r.endpoint.startswith("radius.")}
    rules = {r.endpoint[4:]: r for r in app.url_map.iter_rules()
             if r.endpoint.startswith("api.")}
    assert not sorted(set(API_PERMISSIONS) - set(rules)), "stale API map entries"
    admin, _ = _manager(app, ("dashboard.view",))
    bad, allowed = [], []
    with app.test_request_context("/"):
        for name, rule in rules.items():
            if name not in API_PERMISSIONS or name in API_AUTH_ONLY:
                continue
            for m in sorted(set(rule.methods) - {"HEAD", "OPTIONS"}):
                spec = _resolve(API_PERMISSIONS[name], m)
                if spec and spec.startswith("web:") and spec[4:] not in web:
                    bad.append(f"{name}: {spec}")
                if decide(name, m, admin, tenant_id=1) is None:
                    allowed.append(f"{m} {name} ({spec})")
    assert not bad, bad
    assert not allowed, "\n".join(allowed)


# ───────────────────────── behaviour ─────────────────────────

_VIEWER_READS = [
    "/api/v1/vouchers", "/api/v1/ledger", "/api/v1/payments", "/api/v1/audit",
    "/api/v1/tenants", "/api/v1/system/license-file", "/api/v1/accounts",
    "/api/v1/cards/batches", "/api/v1/finance/wallets", "/api/v1/settings",
    "/api/v1/tools/radius-log",
]


@pytest.mark.parametrize("path", _VIEWER_READS)
def test_dashboard_only_manager_cannot_read(client, app, path):
    _, u = _manager(app, ("dashboard.view",))
    r = client.get(path, headers=u)
    assert r.status_code == 403, (path, r.status_code, r.get_data(as_text=True)[:200])
    assert r.get_json()["error"]["code"] == "forbidden"


def test_dashboard_only_manager_cannot_move_money_or_change_settings(client, app):
    _, u = _manager(app, ("dashboard.view",))
    h = u
    for method, path, body in [
        ("POST", "/api/v1/finance/wallets/1/credit", {"amount": 7}),
        ("POST", "/api/v1/ledger/void", {"entry_id": 1, "reason": "x"}),
        ("PATCH", "/api/v1/settings", {"system.name": "hacked"}),
        ("PATCH", "/api/v1/payments/settings", {"enabled": True}),
        ("POST", "/api/v1/cards/generate", {"plan_id": 1, "count": 1}),
        ("POST", "/api/v1/distributors/1/settle", {"amount": 1}),
        ("POST", "/api/v1/lifecycle/policies", {"name": "x"}),
        ("POST", "/api/v1/lifecycle/run", {}),
        ("DELETE", "/api/v1/mikrotik/1", None),
        ("DELETE", "/api/v1/subscriber-groups/1", None),
        ("POST", "/api/v1/tools/set-speeds", {}),
        ("POST", "/api/v1/finance/ledger/corrections", {}),
    ]:
        r = client.open(path, method=method, json=body, headers=h)
        assert r.status_code == 403, (method, path, r.status_code)


def test_auth_only_endpoints_stay_open(client, app):
    _, u = _manager(app, ("dashboard.view",))
    h = u
    assert client.get("/api/admin/me", headers=h).status_code == 200
    assert client.get("/api/v1/notifications", headers=h).status_code == 200
    assert client.get("/api/v1/dashboard", headers=h).status_code == 200


def test_manager_with_the_right_key_passes(client, app):
    _, u = _manager(app, ("dashboard.view", "users.view", "cards.view",
                          "reports.finance", "audit.view"))
    h = u
    for path in ("/api/v1/accounts", "/api/v1/cards/batches", "/api/v1/ledger",
                 "/api/v1/audit", "/api/v1/vouchers"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True)[:200])


def test_owner_bypasses_the_guard(client, app):
    _owner(app)
    from app.radius.db.repos import admins_repo
    with app.app_context():
        owner = admins_repo.get_admin(admins_repo.primary_admin_id())
    # owner-only endpoints answer for the owner (password known only for the
    # seeded one → use a DB token bound to the owner)
    h = _bearer_for(app, owner.id)
    for path in ("/api/v1/tenants", "/api/v1/ledger", "/api/v1/audit",
                 "/api/v1/backups/status", "/api/v1/admins"):
        r = client.get(path, headers=h)
        assert r.status_code != 403, (path, r.status_code)


def test_unbound_credential_keeps_behaviour(client, app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "unbound-int-token")
    h = {"Authorization": "Bearer unbound-int-token"}
    assert client.get("/api/v1/tenants", headers=h).status_code == 200
    assert client.get("/api/v1/audit", headers=h).status_code == 200


def test_card_passwords_masked_without_view_passwords(app):
    """API read serializer masks card passwords like the web batch page."""
    from types import SimpleNamespace

    from flask import g

    from app.api.v1.cards import _serialize_card_read
    card = SimpleNamespace(id=1, batch_id=1, plan_id=1, username="c1",
                           password="secret1", used=0, revoked=0, expire_at=None,
                           first_used_at=None, created_at=None, locked_mac="",
                           used_by_mac="")
    viewer, _ = _manager(app, ("dashboard.view", "cards.view"))
    sup, _ = _manager(app, ("dashboard.view", "cards.view", "scope.view_passwords"))
    for aid, expected in ((viewer.id, "••••••"), (sup.id, "secret1"), (0, "secret1")):
        with app.test_request_context("/"):
            g.admin_id, g.tenant_id = aid, 1
            assert _serialize_card_read(card)["password"] == expected, aid


# ───────────────────────── D13 / D16 ─────────────────────────

def test_flag_super_cannot_take_over_the_owner(client, app):
    """A non-owner with the bare is_super_admin flag used to rename the owner
    and reset his password over the API (p01/D13)."""
    owner = _owner(app)
    _, u = _manager(app, ("dashboard.view", "admins.view"), is_super_admin=True)
    h = u
    r = client.patch(f"/api/v1/admins/{owner.id}", json={"password": "owned-123"},
                     headers=h)
    assert r.status_code == 403
    r = client.patch(f"/api/v1/admins/{owner.id}", json={"full_name": "x"},
                     headers=h)
    assert r.status_code == 403
    r = client.post("/api/v1/admins", json={"username": "evil", "password": "p"},
                    headers=h)
    assert r.status_code == 403
    victim, _v = _manager(app, ("dashboard.view",))
    r = client.patch(f"/api/v1/admins/{victim.id}", json={"password": "new-pw-1"},
                     headers=h)
    assert r.status_code == 403
    r = client.delete(f"/api/v1/admins/{owner.id}", headers=h)
    assert r.status_code == 403
    from app.radius.db.repos import admins_repo
    with app.app_context():
        again = admins_repo.get_admin(owner.id)
    assert again.full_name == owner.full_name
    # admins.view lets him READ the roster (web parity), nothing more
    assert client.get("/api/v1/admins", headers=h).status_code == 200


def test_owner_account_protected_even_from_owner_like_delete(client, app):
    from app.api.v1 import admins as api_admins  # noqa: F401
    from app.radius.auth.owner import can_modify_admin, is_owner_like
    owner = _owner(app)
    mgr, _ = _manager(app, ("dashboard.view",), is_super_admin=True)
    with app.app_context():
        assert is_owner_like(owner)
        assert not is_owner_like(mgr)          # the bare flag is not ownership
        assert not can_modify_admin(mgr, owner)
        assert can_modify_admin(owner, mgr)


def test_distributor_create_needs_the_manage_grant(client, app):
    mgr, u = _manager(app, ("dashboard.view", "reports.finance"))
    _mid = mgr.id
    r = client.post("/api/v1/distributors", json={"name": "d1"}, headers=u)
    # refused either by the web decision (the distributor action gate) or by
    # the «إدارة الموزعين» grant check in the handler — never 201 any more.
    assert r.status_code == 403
    from app.api.v1 import distributors as api_dist
    from flask import g
    with app.test_request_context("/"):
        g.admin_id, g.tenant_id = _mid, 1
        assert api_dist._can_manage_distributors() is False
