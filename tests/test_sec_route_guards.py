"""SEC M3/H6 — state-changing endpoints that were fail-open are now guarded.

A non-super admin (lacking the required permission) must get 403 on these POST
endpoints instead of silently executing them. Covers the credential/channel
sinks (sms_save/wh_settings), the sensitive super-only ones (VPN/WG/tools/
store-key rotation), and representative CRUD (share groups, subscriber-group
bulk actions).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_secguard_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
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


def _login_limited(client):
    """Log in a NON-super admin bound to a role with NO permissions — so the
    perm-guard blocks (403) before any handler/CSRF runs."""
    from app.radius.db.repos import admins_repo
    if admins_repo.primary_admin_id() is None:
        admins_repo.create_admin(username=f"owner_{uuid4().hex[:8]}",
                                 password="owner-pass", full_name="Owner",
                                 is_super_admin=True)
    role = admins_repo.create_role(name=f"empty_{uuid4().hex[:6]}",
                                   display_name="No perms", permissions=())
    u = f"g_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="p", full_name="T",
                             is_super_admin=False, role_id=role.id)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "p"})
    assert res.status_code in {302, 303}


def _csrf(client):
    client.get("/admin/radius/")
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


# A representative sample across the newly-guarded clusters. Each is a POST
# whose guard fires BEFORE the handler body, so a bad/missing id still 403s.
_ENDPOINTS = [
    "/admin/radius/sms/save",
    "/admin/radius/sms/test",
    "/admin/radius/webhooks",                       # wh_settings
    "/admin/radius/share_groups",                   # sgrp_create
    "/admin/radius/share_groups/1/delete",          # sgrp_delete
    "/admin/radius/vpn-accounts/create",            # vpn_accounts_create
    "/admin/radius/tools/set_speeds",               # tool_set_speeds
    "/admin/radius/settings/store-key/rotate",       # settings_rotate_store_key
    "/admin/radius/subscriber-groups/1/disconnect-online",
]


@pytest.mark.parametrize("path", _ENDPOINTS)
def test_limited_admin_is_forbidden(client, path):
    # With a valid CSRF token, the perm-guard returns 403 for an admin whose
    # role lacks the required permission (empty-perm role → every key missing;
    # super-gated ones always 403 a non-super).
    _login_limited(client)
    token = _csrf(client)
    resp = client.post(path, data={"_csrf_token": token},
                       headers={"X-CSRFToken": token,
                                "Accept": "application/json",
                                "X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 403, (path, resp.status_code)


def test_guard_map_registered():
    # The blueprint's guard map carries the new entries with sane keys.
    from app.radius.routes.blueprint import _PERM_GUARDED, _PERM_SUPER
    assert _PERM_GUARDED["sms_save"] == "settings.edit"
    assert _PERM_GUARDED["wh_settings"] == "settings.edit"
    assert _PERM_GUARDED["settings_rotate_store_key"] == _PERM_SUPER
    assert _PERM_GUARDED["vpn_accounts_create"] == _PERM_SUPER
    assert _PERM_GUARDED["subscriber_groups_disconnect_online"] == "online.disconnect"
