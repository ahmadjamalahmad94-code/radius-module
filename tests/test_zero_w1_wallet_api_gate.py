"""Zero-w1 #1 — /api/v1 wallet debit/credit apply the web SafetyGate + perms
and the Idempotency-Key replay of the other money endpoints.

Before: only the endpoint RBAC ran — an owner's 6,000 debit (web max 5,000)
returned 201, a manager holding «reports.finance» but not ``wallet.debit``
debited, and a double tap with one Idempotency-Key debited twice.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_zw1_wallet_")
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


def _bearer_for(app, admin_id):
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        _rec, plain = api_tokens_repo.create_token(
            tenant_id=1, name=f"login:t{uuid4().hex[:6]}", scopes=["admin:full"],
            created_by=int(admin_id))
    return {"Authorization": f"Bearer {plain}"}


def _owner(app):
    from app.radius.db.repos import admins_repo
    with app.app_context():
        pid = admins_repo.primary_admin_id()
        if pid is None:
            admins_repo.create_admin(username="owner_x", password="owner-pass",
                                     full_name="Owner", is_super_admin=True)
            pid = admins_repo.primary_admin_id()
    return _bearer_for(app, pid)


def _manager(app, perms):
    from app.radius.db.repos import admins_repo
    _owner(app)
    with app.app_context():
        role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}",
                                       display_name="r", permissions=tuple(perms))
        a = admins_repo.create_admin(username=f"m_{uuid4().hex[:8]}",
                                     password="pw-123456", full_name="M",
                                     role_id=role.id)
    return _bearer_for(app, a.id)


def _wallet(client, hdr, credit="7000.00"):
    r = client.post("/api/v1/finance/wallets", headers=hdr,
                    json={"owner_type": "company", "owner_id": 1})
    assert r.status_code == 201, r.get_json()
    wid = r.get_json()["data"]["wallet"]["id"]
    if credit:
        c = client.post(f"/api/v1/finance/wallets/{wid}/credit", headers=hdr,
                        json={"amount": credit, "reference_type": "manual"})
        assert c.status_code == 201, c.get_json()
    return wid


def _balance(client, hdr, wid):
    return client.get(f"/api/v1/finance/wallets/{wid}", headers=hdr
                      ).get_json()["data"]["wallet"]["balance"]


def test_debit_over_web_safety_limit_is_refused(app):
    c = app.test_client()
    hdr = _owner(app)
    wid = _wallet(c, hdr)
    r = c.post(f"/api/v1/finance/wallets/{wid}/debit", headers=hdr,
               json={"amount": "6000.00", "reference_type": "manual"})
    assert r.status_code == 422, r.get_json()
    assert r.get_json()["error"]["code"] == "limit_exceeded"
    assert _balance(c, hdr, wid) == "7000.00"
    ok = c.post(f"/api/v1/finance/wallets/{wid}/debit", headers=hdr,
                json={"amount": "5000.00", "reference_type": "manual"})
    assert ok.status_code == 201
    assert _balance(c, hdr, wid) == "2000.00"


def test_manager_without_wallet_perms_is_refused_like_the_web(app):
    c = app.test_client()
    wid = _wallet(c, _owner(app))
    mgr = _manager(app, ("reports.finance",))
    for op in ("debit", "credit"):
        r = c.post(f"/api/v1/finance/wallets/{wid}/{op}", headers=mgr,
                   json={"amount": "5.00", "reference_type": "manual"})
        assert r.status_code == 403, (op, r.get_json())
    assert _balance(c, _owner(app), wid) == "7000.00"
    full = _manager(app, ("reports.finance", "wallet.debit", "wallet.credit"))
    for op in ("debit", "credit"):
        r = c.post(f"/api/v1/finance/wallets/{wid}/{op}", headers=full,
                   json={"amount": "5.00", "reference_type": "manual"})
        assert r.status_code == 201, (op, r.get_json())


def test_double_tap_with_one_key_debits_once(app):
    c = app.test_client()
    hdr = _owner(app)
    wid = _wallet(c, hdr, credit="100.00")
    h = {**hdr, "Idempotency-Key": "tap-" + uuid4().hex}
    body = {"amount": "10.00", "reference_type": "manual"}
    a = c.post(f"/api/v1/finance/wallets/{wid}/debit", headers=h, json=body)
    b = c.post(f"/api/v1/finance/wallets/{wid}/debit", headers=h, json=body)
    assert a.status_code == b.status_code == 201
    assert b.headers.get("Idempotent-Replay") == "true"
    assert _balance(c, hdr, wid) == "90.00"
    h2 = {**hdr, "Idempotency-Key": "tapc-" + uuid4().hex}
    for _ in range(2):
        c.post(f"/api/v1/finance/wallets/{wid}/credit", headers=h2, json=body)
    assert _balance(c, hdr, wid) == "100.00"


def test_non_numeric_amount_is_422(app):
    c = app.test_client()
    hdr = _owner(app)
    wid = _wallet(c, hdr)
    r = c.post(f"/api/v1/finance/wallets/{wid}/debit", headers=hdr,
               json={"amount": "abc", "reference_type": "manual"})
    assert r.status_code == 422
