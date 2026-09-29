"""p01 re-probe — a manager holding ONLY «لوحة التحكّم» (dashboard.view) hits
EVERY web and API endpoint; everything must refuse him (403/404, or a
redirect to the dashboard / license / provider page) except the explicit
allow-lists (self-service, public, in-handler-guarded).

This is the audit's method turned into a regression test: before the fix the
same manager purged card batches, created subscriber groups, ran wizard steps,
credited wallets, voided the ledger and read voucher codes.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest

_DENY_REDIRECT_MARKERS = ("/admin/radius/", "/admin/radius/dashboard",
                          "/_provider/", "/_license/")


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_probe_")
    keys = ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED",
            "HOBERADIUS_API_TOKENS")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    os.environ.pop("HOBERADIUS_API_TOKENS", None)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    a.testing = True
    yield a
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="module")
def viewer(app):
    from app.radius.db.repos import admins_repo, api_tokens_repo
    with app.app_context():
        if admins_repo.primary_admin_id() is None:
            admins_repo.create_admin(username=f"own_{uuid4().hex[:6]}",
                                     password="owner-pass", full_name="Owner",
                                     is_super_admin=True)
        role = admins_repo.create_role(name=f"dash_{uuid4().hex[:6]}",
                                       display_name="dashboard only",
                                       permissions=("dashboard.view",))
        u = f"viewer_{uuid4().hex[:6]}"
        a = admins_repo.create_admin(username=u, password="viewer-pass",
                                     full_name="Viewer", is_super_admin=False,
                                     role_id=role.id)
        _rec, plain = api_tokens_repo.create_token(
            tenant_id=1, name="login:probe", scopes=["admin:full"],
            created_by=a.id)
    return u, plain


def _values(rule):
    out = {}
    for arg in rule.arguments:
        conv = type(rule._converters.get(arg)).__name__ if hasattr(rule, "_converters") else ""
        out[arg] = 1 if "Integer" in conv else "x"
    return out


def _urls(app, prefix):
    adapter = app.url_map.bind("localhost")
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith(prefix):
            continue
        for m in sorted(set(rule.methods) - {"HEAD", "OPTIONS"}):
            try:
                url = adapter.build(rule.endpoint, _values(rule), method=m)
            except Exception:  # noqa: BLE001
                continue
            yield rule, m, url


def _denied(resp) -> bool:
    if resp.status_code in (401, 403, 404):
        return True
    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("Location", "")
        path = loc.split("://", 1)[-1]
        path = path[path.find("/"):] if "/" in path else path
        path = path.split("?", 1)[0]
        return (path in ("/admin/radius/", "/admin/radius/dashboard")
                or "/_provider/" in path or "/_license/" in path)
    return False


def test_dashboard_only_manager_is_refused_on_every_web_endpoint(app, viewer):
    from app.radius.routes.blueprint import _GUARD_ALLOWLIST, _PUBLIC_ENDPOINTS
    user, _tok = viewer
    client = app.test_client()
    r = client.post("/admin/radius/login",
                    data={"username": user, "password": "viewer-pass"})
    assert r.status_code in (302, 303)
    client.get("/admin/radius/")
    with client.session_transaction() as s:
        csrf = s.get("_csrf_token", "")
    leaks = []
    for rule, method, url in _urls(app, "radius."):
        name = rule.endpoint.split(".", 1)[1]
        if rule.endpoint in _PUBLIC_ENDPOINTS or name in _GUARD_ALLOWLIST:
            continue
        if name in ("auth_logout",):
            continue
        headers = {"X-CSRFToken": csrf, "Accept": "application/json",
                   "X-Requested-With": "XMLHttpRequest"}
        data = {"_csrf_token": csrf} if method != "GET" else None
        resp = client.open(url, method=method, data=data, headers=headers)
        if not _denied(resp):
            leaks.append(f"{method} {url} → {resp.status_code}")
    assert not leaks, "dashboard-only manager reached:\n" + "\n".join(leaks)


def test_dashboard_only_manager_is_refused_on_every_api_endpoint(app, viewer):
    from app.api.permission_guard import API_AUTH_ONLY, API_PUBLIC
    _user, token = viewer
    client = app.test_client()
    h = {"Authorization": f"Bearer {token}"}
    leaks = []
    for rule, method, url in _urls(app, "api."):
        name = rule.endpoint[4:]
        if name in API_PUBLIC or name in API_AUTH_ONLY:
            continue
        resp = client.open(url, method=method, json={}, headers=h)
        if resp.status_code != 403:
            leaks.append(f"{method} {url} → {resp.status_code}")
    assert not leaks, "dashboard-only manager reached:\n" + "\n".join(leaks)
