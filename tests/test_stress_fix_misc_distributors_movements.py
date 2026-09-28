"""Stress-fix (misc stream): distributors + balance-movements paging.

* A10 F13: walking /operational-reports/balance-movements page by page now
  reaches the distributor ledger rows (offset was applied per sub-query, so
  233 distributor rows never appeared on any page);
* A11 F-5: a debit beyond a set credit limit (> 0) is refused; a non-active
  distributor cannot receive batches or new debt (payments still accepted);
* web parity: the web distributor form rejects «inf» with an Arabic message.
"""
from __future__ import annotations

import secrets
import sys

import pytest

for _m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
    sys.modules.pop(_m, None)

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture(scope="module")
def app():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOBERADIUS_NO_WORKER", "1")
        mp.setenv("HOBERADIUS_NO_SEED", "1")
        from app import create_app
        yield create_app()


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


def _distributor(client, **extra) -> int:
    res = client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "mv_" + secrets.token_hex(3), **extra})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["distributor"]["id"]


def test_balance_movements_paging_reaches_distributor_rows(app, client):
    tag = "mvq" + secrets.token_hex(2)
    did = _distributor(client, name=tag + "_dist")
    for i in range(6):
        res = client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                          json={"amount": 1 + i, "direction": "credit"})
        assert res.status_code == 201, res.get_json()
    from app.radius.db.connection import transaction
    from app.radius.db.repos import accounting_repo
    with app.app_context():
        with transaction() as conn:
            for i in range(9):
                accounting_repo.create_ledger_entry(
                    conn, tenant_id=1, entry_type="payment", amount=10 + i,
                    currency="ILS", username=f"{tag}_sub", operator="t",
                    source_type="test")
    # query matches both sources: username LIKE tag / distributor name LIKE tag
    full = client.get(f"/api/v1/operational-reports/balance-movements?q={tag}&limit=100",
                      headers=AUTH).get_json()["data"]["items"]
    assert len(full) == 15
    assert sum(1 for r in full if r["scope"] == "distributor") == 6
    walked: list[tuple] = []
    offset = 0
    while True:
        page = client.get(
            f"/api/v1/operational-reports/balance-movements?q={tag}&limit=4&offset={offset}",
            headers=AUTH).get_json()["data"]["items"]
        if not page:
            break
        walked += [(r["scope"], r["entry_id"]) for r in page]
        offset += 4
    assert len(walked) == 15 == len(set(walked)), walked
    assert sum(1 for s, _ in walked if s == "distributor") == 6


def test_credit_limit_enforced_on_debit(client):
    did = _distributor(client, credit_limit=100)
    ok = client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                     json={"amount": 60, "direction": "debit"})
    assert ok.status_code == 201
    over = client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                       json={"amount": 50, "direction": "debit"})
    assert over.status_code == 422
    assert "حدّ ائتمان" in over.get_json()["error"]["message"]
    # a payment is always accepted
    pay = client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                      json={"amount": 50, "direction": "credit"})
    assert pay.status_code == 201
    # credit_limit 0 = no limit configured → debit accepted
    free = _distributor(client, credit_limit=0)
    assert client.post(f"/api/v1/distributors/{free}/settle", headers=AUTH,
                       json={"amount": 5000, "direction": "debit"}).status_code == 201


def test_inactive_distributor_cannot_take_debt_or_batches(app, client):
    did = _distributor(client, status="inactive")
    res = client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                      json={"amount": 1, "direction": "debit"})
    assert res.status_code == 422 and "غير مفعّل" in res.get_json()["error"]["message"]
    # paying down is still fine
    assert client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                       json={"amount": 1, "direction": "credit"}).status_code == 201
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso
    plan = client.post("/api/v1/profiles", headers=AUTH, json={
        "name": "mvp_" + secrets.token_hex(3), "plan_type": "time", "duration_minutes": 60,
        "speed_down_kbps": 4000, "speed_up_kbps": 2000})
    assert plan.status_code == 201, plan.get_json()
    with app.app_context():
        with transaction() as conn:
            batch_id = conn.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, created_at) "
                "VALUES(1, ?, ?, ?)",
                ("MVB" + secrets.token_hex(3), plan.get_json()["data"]["id"], now_iso())).lastrowid
    res = client.post(f"/api/v1/distributors/{did}/assign-batch", headers=AUTH,
                      json={"batch_id": batch_id})
    assert res.status_code == 422, res.get_json()
    assert "غير مفعّل" in res.get_json()["error"]["message"]
    active = _distributor(client)
    ok = client.post(f"/api/v1/distributors/{active}/assign-batch", headers=AUTH,
                     json={"batch_id": batch_id})
    assert ok.status_code == 200, ok.get_json()


def test_web_form_rejects_infinity(app):
    web = app.test_client()
    with web.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "dist_admin"
        sess["admin_name"] = "Dist Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "d-csrf"
    name = "mvw_" + secrets.token_hex(3)
    res = web.post("/admin/radius/distributors", data={
        "name": name, "credit_limit": "inf", "_csrf_token": "d-csrf"})
    assert res.status_code == 400
    assert "خارج النطاق" in res.get_data(as_text=True)
    from app.radius.db.connection import db
    with app.app_context():
        assert db().execute("SELECT 1 FROM distributors WHERE name = ?", (name,)).fetchone() is None
