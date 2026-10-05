"""SEC F-5 — production-mode seeding must never create a known default password.

Before the fix:
  * the demo seed (NO_SEED unset; in production only with HOBERADIUS_DEMO_SEED=1)
    created admin/admin (super) and operator/operator;
  * the bootstrap admin (every boot with no admin, production included) was
    admin/123456789, the value printed in deploy/.env.example.
A fresh production VPS was therefore reachable with a public password until
the owner logged in.

After the fix, in production (HOBERADIUS_ENV / FLASK_ENV = prod|production):
  * every seeded/bootstrap admin gets a random one-time password,
    must_change_password=1, and the credentials go to a 0600 file next to the
    DB (``initial_admin_credentials.txt``) — never to the log;
  * an explicit strong HOBERADIUS_BOOTSTRAP_ADMIN_PASS is still honoured;
    an explicit KNOWN default (123456789, admin…) is not.
Dev/test keep the old defaults.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import sys
import tempfile

import pytest

STRONG = "9f1c2e7b4a5d6c3e8f0a1b2c3d4e5f60718293a4b5c6d7e8"


def _boot(monkeypatch, *, prod: bool, no_seed: bool, demo_seed: bool = False, **env):
    tmp = tempfile.mkdtemp(prefix="hr_f5_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    for k in ("HOBERADIUS_ENV", "FLASK_ENV", "HOBERADIUS_NO_SEED", "HOBERADIUS_DEMO_SEED",
              "HOBERADIUS_BOOTSTRAP_ADMIN_USER", "HOBERADIUS_BOOTSTRAP_ADMIN_PASS",
              "HOBERADIUS_INITIAL_CREDENTIALS_FILE", "HOBERADIUS_DEMO_MONEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FLASK_SECRET", STRONG)
    if prod:
        monkeypatch.setenv("HOBERADIUS_ENV", "production")
    if no_seed:
        monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    if demo_seed:
        monkeypatch.setenv("HOBERADIUS_DEMO_SEED", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    app = create_app()
    return app, tmp


def _verify(app, username, password) -> bool:
    with app.app_context():
        from app.radius.db.repos import admins_repo
        a = admins_repo.get_by_username(username)
        return bool(a) and admins_repo.verify_password(password, a.password_hash)


def _must_change(app, username) -> bool:
    with app.app_context():
        from app.radius.db.repos import admins_repo
        return bool(admins_repo.get_by_username(username).must_change_password)


def _creds_file(tmp):
    return os.path.join(tmp, "initial_admin_credentials.txt")


def _creds(tmp) -> dict:
    out = {}
    with open(_creds_file(tmp), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\S+\s+username=(\S+)\s+password=(\S+)", line.strip())
            if m:
                out[m.group(1)] = m.group(2)
    return out


# ─────────────────────── production: demo seed opt-in ───────────────────────
def test_production_demo_seed_creates_no_known_passwords(monkeypatch):
    app, tmp = _boot(monkeypatch, prod=True, no_seed=False, demo_seed=True)
    assert not _verify(app, "admin", "admin")
    assert not _verify(app, "operator", "operator")
    creds = _creds(tmp)
    assert set(creds) >= {"admin", "operator"}
    for user, pw in creds.items():
        assert len(pw) >= 16
        assert _verify(app, user, pw)
        assert _must_change(app, user)


# ─────────────────────── production: bootstrap admin ───────────────────────
def test_production_bootstrap_admin_has_no_known_password(monkeypatch):
    app, tmp = _boot(monkeypatch, prod=True, no_seed=True)
    assert not _verify(app, "admin", "123456789")
    pw = _creds(tmp)["admin"]
    assert _verify(app, "admin", pw)
    assert _must_change(app, "admin")


def test_production_explicit_known_default_bootstrap_pass_is_replaced(monkeypatch):
    app, tmp = _boot(monkeypatch, prod=True, no_seed=True,
                     HOBERADIUS_BOOTSTRAP_ADMIN_PASS="123456789")
    assert not _verify(app, "admin", "123456789")
    assert _verify(app, "admin", _creds(tmp)["admin"])


def test_production_explicit_strong_bootstrap_pass_is_honoured(monkeypatch):
    app, tmp = _boot(monkeypatch, prod=True, no_seed=True,
                     HOBERADIUS_BOOTSTRAP_ADMIN_USER="owner",
                     HOBERADIUS_BOOTSTRAP_ADMIN_PASS="Owner-Chosen-Pass-2026!")
    assert _verify(app, "owner", "Owner-Chosen-Pass-2026!")
    assert not os.path.exists(_creds_file(tmp))


def test_production_credentials_file_is_owner_only_and_password_not_logged(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG):
        app, tmp = _boot(monkeypatch, prod=True, no_seed=True)
    pw = _creds(tmp)["admin"]
    assert pw not in caplog.text
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(_creds_file(tmp)).st_mode)
        assert mode == 0o600


def test_production_second_boot_does_not_rotate(monkeypatch):
    app, tmp = _boot(monkeypatch, prod=True, no_seed=True)
    pw = _creds(tmp)["admin"]
    with app.app_context():
        from app.radius.db.repos import admins_repo
        admins_repo.ensure_bootstrap_admin()
    assert _verify(app, "admin", pw)
    assert list(_creds(tmp)) == ["admin"]


# ─────────────────────── dev/test: unchanged ───────────────────────
def test_dev_seed_keeps_demo_defaults(monkeypatch):
    app, tmp = _boot(monkeypatch, prod=False, no_seed=False)
    assert _verify(app, "admin", "admin")
    assert _verify(app, "operator", "operator")
    assert not os.path.exists(_creds_file(tmp))


def test_dev_bootstrap_keeps_default(monkeypatch):
    app, tmp = _boot(monkeypatch, prod=False, no_seed=True)
    assert _verify(app, "admin", "123456789")
    assert not os.path.exists(_creds_file(tmp))
