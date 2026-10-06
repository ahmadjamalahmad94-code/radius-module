"""SEC-360: subscriber detail payloads must never carry credentials.

``GET /api/v1/accounts/<u>/360`` returned the raw ``subscribers`` row, whose
``pppoe_password`` was NOT in the 360 deny-list (only ``password`` was) — so
every 360 call (even for a manager without «رؤية كلمة مرور المشترك») leaked
the PPPoE password in plain text. The mobile app never displays it.
"""
from __future__ import annotations

import os

import pytest

from app.radius.core.types import Subscriber
from app.radius.db.connection import db, reset_for_tests
from app.radius.db.repos import subscribers_repo

TOKEN = "dev-token-please-change"
LOGIN_PW = "LoginPw-7f3c"
PPPOE_PW = "PppoeSecret-91ab"
RADCHECK_PW = "RadcheckVal-55e1"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sec_360.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _seed(app) -> Subscriber:
    with app.app_context():
        sub = subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="sec360-user", password=LOGIN_PW,
            pppoe_username="sec360-ppp", pppoe_password=PPPOE_PW,
            full_name="SEC 360",
        ))
        db().execute(
            "INSERT INTO radcheck(tenant_id, username, attribute, op, value) "
            "VALUES (1, ?, 'Cleartext-Password', ':=', ?)",
            (sub.username, RADCHECK_PW),
        )
        db().execute(
            "INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas) "
            "VALUES (1, ?, ?, 'Access-Accept', '2026-01-01', '', 'nas-1')",
            (sub.username, LOGIN_PW),
        )
        return sub


def _get(app, path):
    with app.test_client() as client:
        return client.get(path, headers={"Authorization": f"Bearer {TOKEN}"})


def test_360_never_returns_pppoe_or_login_password(app):
    sub = _seed(app)
    res = _get(app, f"/api/v1/accounts/{sub.username}/360")
    assert res.status_code == 200, res.get_json()
    raw = res.get_data(as_text=True)
    for secret in (LOGIN_PW, PPPOE_PW, RADCHECK_PW):
        assert secret not in raw, f"secret leaked in 360: {secret}"
    row = res.get_json()["data"]["subscriber"]
    assert "password" not in row
    # The key stays (old app builds parse it) but only as the mask + a flag.
    assert row.get("pppoe_password") in ("", "••••••")
    assert row["has_pppoe_password"] is True
    assert row["pppoe_username"] == "sec360-ppp"


def test_360_empty_pppoe_password_reports_flag_false(app):
    with app.app_context():
        sub = subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="sec360-nopw", password=LOGIN_PW))
    res = _get(app, f"/api/v1/accounts/{sub.username}/360")
    assert res.status_code == 200
    row = res.get_json()["data"]["subscriber"]
    assert row["has_pppoe_password"] is False
    assert row.get("pppoe_password") == ""


def test_account_detail_and_list_never_return_login_password_or_radcheck(app):
    sub = _seed(app)
    for path in (f"/api/v1/accounts/{sub.username}", "/api/v1/accounts?limit=50"):
        res = _get(app, path)
        assert res.status_code == 200, (path, res.get_json())
        raw = res.get_data(as_text=True)
        assert LOGIN_PW not in raw, path
        assert RADCHECK_PW not in raw, path
        assert '"password":' not in raw, path
