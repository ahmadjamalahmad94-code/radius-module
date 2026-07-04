# -*- coding: utf-8 -*-
"""Fresh-install admin bootstrap + the wrong-password 500 regression.

Owner reports on a freshly-provisioned VPS:
  1. He wants a guaranteed default login: admin / 123456789.
  2. Logging in with a WRONG password returned «Internal Server Error» (500)
     instead of a friendly «wrong credentials» — while still recording the
     failed attempt. Root cause: auth.py passes attempted_password= to
     record_login_event, whose signature had lost that parameter → TypeError
     (raised at call binding, before the function's own try/except) → 500.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_boot_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # NO_SEED on: prove the bootstrap admin is created independently of the
    # demo seed (i.e. the production path).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_bootstrap_admin_created_on_fresh_install(app):
    """A fresh install (NO_SEED) still gets a default super-admin login."""
    with app.app_context():
        from app.radius.db.repos import admins_repo
        a = admins_repo.get_by_username("admin")
        assert a is not None, "default admin was not bootstrapped"
        assert a.is_super_admin
        svc = _svc(app)
        assert svc.authenticate("admin", "123456789") is not None
        assert svc.authenticate("admin", "wrong") is None


def _svc(app):
    from app.radius.services.admins import get_admins_service
    return get_admins_service()


def test_wrong_password_login_returns_401_not_500(app):
    """The reported bug: wrong admin password must NOT 500."""
    client = app.test_client()
    res = client.post("/admin/radius/login",
                      data={"username": "admin", "password": "definitely-wrong"})
    assert res.status_code == 401, res.status_code   # friendly, not 500
    body = res.get_data(as_text=True)
    assert "بيانات الدخول غير صحيحة" in body


def test_wrong_password_records_failed_attempt_and_password(app):
    """The failed attempt is logged AND the attempted password captured (for
    the super-admin «حالات الدخول» diagnostic) — without a 500."""
    client = app.test_client()
    res = client.post("/admin/radius/login",
                      data={"username": "admin", "password": "OopsTypo9"})
    assert res.status_code == 401
    with app.app_context():
        from app.radius.db.connection import db
        # audit failed-login row exists
        arow = db().execute(
            "SELECT id FROM audit_log WHERE action='auth_login_failed' "
            "AND actor='admin' ORDER BY id DESC LIMIT 1").fetchone()
        assert arow is not None
        # attempted password captured in the dedicated table (not audit payload)
        prow = db().execute(
            "SELECT attempted_password FROM login_attempt_passwords "
            "WHERE username='admin' ORDER BY id DESC LIMIT 1").fetchone()
        assert prow is not None and prow["attempted_password"] == "OopsTypo9"


def test_correct_password_login_succeeds(app):
    client = app.test_client()
    res = client.post("/admin/radius/login",
                      data={"username": "admin", "password": "123456789"})
    assert res.status_code in {302, 303}   # redirect to dashboard
