"""Regression (stress re-test R02, 2026-09-29): «حفظ التعديلات» on the web
subscriber form wiped the balance (−888.61 → 0.00, no ledger row) and the
usage/first-login columns, because _form_dto builds a fresh Subscriber whose
unmanaged columns default to 0/None and upsert writes every column.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_edit_bal_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed(app):
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO access_plans(id, tenant_id, name, code, "
                "speed_up_kbps, speed_down_kbps, created_at) "
                "VALUES (702, 1, 'P2', 'p2', 5000, 10000, ?)", (now,))
            c.execute(
                "INSERT INTO subscribers(tenant_id, username, password, plan_id, "
                "status, user_type, service_type, balance, used_seconds, "
                "first_login_at, created_at) "
                "VALUES (1, 'balsub', 'pw1234', 702, 'enabled', 'subscriber', "
                "'hotspot', -888.61, 3600, '2026-09-01T10:00:00Z', ?)", (now,))


def _row(app):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute(
            "SELECT balance, used_seconds, first_login_at, full_name "
            "FROM subscribers WHERE username='balsub'").fetchone()


def test_web_edit_save_keeps_balance_and_usage(app):
    _seed(app)
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        s["is_super_admin"] = True
    client.get("/admin/radius/users/balsub/edit")
    with client.session_transaction() as s:
        token = s.get("_csrf_token")
    resp = client.post("/admin/radius/users/balsub", data={
        "_csrf_token": token,
        "username": "balsub",
        "password": "pw1234",
        "status": "enabled",
        "service_type": "hotspot",
        "plan_id": "702",
        "full_name": "Edited Name",
    })
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:600]
    row = _row(app)
    assert row["full_name"] == "Edited Name"          # the edit landed
    assert abs(float(row["balance"]) - (-888.61)) < 1e-9
    assert int(row["used_seconds"]) == 3600
    assert str(row["first_login_at"]).startswith("2026-09-01")
