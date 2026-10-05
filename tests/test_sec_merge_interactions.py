"""Security merge simulation — cross-fix interactions (docs/security/SEC_MERGE_SIMULATION.md).

F-5 (one-time bootstrap password + must_change_password) × B-06 (login tenant
pick for owner-level only) × F-3 (strong FLASK_SECRET in production):
a fresh PRODUCTION install must still let its only admin in — B-06 must not
lock the bootstrap admin out of every tenant — and the web must force the
password change before anything else.

The API side (``POST /api/admin/login``) does NOT enforce must_change_password
(pre-existing; identity-sync admins have the same gap). Recorded as a strict
xfail so the gap is visible and the test flips when an owner decision lands.
All values below are TEST values.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest

STRONG = "merge-sim-strong-flask-secret-7f3a9c1e5b2d4f60a8b7"
BASE = "https://localhost"


@pytest.fixture()
def prod_app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_sec_merge_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ENV", "production")
    monkeypatch.setenv("FLASK_SECRET", STRONG)
    for k in ("FLASK_ENV", "HOBERADIUS_DEMO_SEED", "HOBERADIUS_BOOTSTRAP_ADMIN_USER",
              "HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "HOBERADIUS_INITIAL_CREDENTIALS_FILE"):
        monkeypatch.delenv(k, raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    creds = {}
    with open(os.path.join(tmp, "initial_admin_credentials.txt"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\S+\s+username=(\S+)\s+password=(\S+)", line.strip())
            if m:
                creds[m.group(1)] = m.group(2)
    app.config["_creds"] = creds
    yield app
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_production_boot_refuses_example_secret(monkeypatch):
    """F-3: the deploy/.env.example placeholder cannot boot production."""
    monkeypatch.setenv("HOBERADIUS_ENV", "production")
    monkeypatch.setenv("FLASK_SECRET", "change-me-to-32-random-bytes-please")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tempfile.mkdtemp(prefix="hr_sec_merge_"), "t.db"))
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    with pytest.raises(RuntimeError, match="FLASK_SECRET"):
        create_app()


def test_bootstrap_admin_web_login_lands_in_tenant_then_forced_change(prod_app):
    """B-06 × F-5: the one-time-password bootstrap admin (owner by the min-id
    fallback) gets a tenant on the web login and is sent to the account page."""
    pw = prod_app.config["_creds"]["admin"]
    c = prod_app.test_client()
    r = c.post("/admin/radius/login", data={"username": "admin", "password": pw},
               base_url=BASE)
    assert r.status_code in (302, 303), r.status_code
    with c.session_transaction(base_url=BASE) as s:
        assert int(s.get("tenant_id") or 0) == 1
        assert int(s.get("admin_id") or 0) > 0
    r = c.get("/admin/radius/", base_url=BASE)
    assert r.status_code in (302, 303)
    assert "/account" in r.headers.get("Location", ""), r.headers.get("Location")


@pytest.mark.xfail(strict=True, reason=(
    "pre-existing gap made sharper by F-5: /api/admin/login ignores "
    "must_change_password, so the one-time bootstrap password works on the "
    "API (mobile app / HTTP Basic) without ever being changed — owner decision"))
def test_api_login_refuses_or_flags_must_change_password(prod_app):
    pw = prod_app.config["_creds"]["admin"]
    r = prod_app.test_client().post("/api/admin/login",
                                    json={"username": "admin", "password": pw},
                                    base_url=BASE)
    body = r.get_json() or {}
    flagged = bool(((body.get("data") or {}).get("admin") or {}).get("must_change_password"))
    assert r.status_code != 200 or flagged
