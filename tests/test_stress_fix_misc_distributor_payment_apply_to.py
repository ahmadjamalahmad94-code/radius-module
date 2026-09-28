"""Owner rule 2026-09-28 — a distributor payment has ONE effect, chosen at payment
time: «إضافة للرصيد» (apply_to=balance) or «خصم من الدين» (apply_to=debt).

It used to raise the balance AND lower the debt (money counted twice). Pinned:
* balance → balance += amount, debt untouched, ledger entry payment_to_balance;
* debt    → debt -= amount, balance untouched, ledger entry payment_to_debt;
  more than the outstanding debt → 422 naming the remaining debt, nothing written;
* missing apply_to (old app builds) → debt if the distributor owes, else balance;
* exactly one ledger row per payment; summary + balance-movements show which one;
* web form: two radios with the default preselected + current balance/debt shown,
  and the posted choice reaches the same service.
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


def _dist(client, debt: float = 0.0) -> int:
    res = client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "ap_" + secrets.token_hex(4), "balance": 10, "credit_limit": 0})
    assert res.status_code == 201, res.get_json()
    did = res.get_json()["data"]["distributor"]["id"]
    if debt:
        r = client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                        json={"amount": debt, "direction": "debit"})
        assert r.status_code == 201, r.get_json()
    return did


def _state(client, did):
    s = client.get(f"/api/v1/distributors/{did}/summary", headers=AUTH).get_json()["data"]["summary"]
    return s["balance"], s["debt_balance"], s


def _entries(app, did):
    from app.radius.db.connection import db
    with app.app_context():
        return [dict(r) for r in db().execute(
            "SELECT entry_type, direction, amount FROM distributor_ledger_entries "
            "WHERE distributor_id = ? ORDER BY id", (did,)).fetchall()]


def _pay(client, did, **body):
    return client.post(f"/api/v1/distributors/{did}/settle", headers=AUTH,
                       json={"direction": "credit", **body})


def test_apply_to_balance_only_raises_balance(app, client):
    did = _dist(client, debt=50)
    res = _pay(client, did, amount=20, apply_to="balance")
    assert res.status_code == 201, res.get_json()
    entry = res.get_json()["data"]["entry"]
    assert entry["apply_to"] == "balance" and entry["entry_type"] == "payment_to_balance"
    bal, debt, s = _state(client, did)
    assert (bal, debt) == (30.0, 50.0)
    assert s["ledger"]["paid_to_balance"] == 20.0 and s["ledger"]["paid_to_debt"] == 0.0
    credits = [e for e in _entries(app, did) if e["direction"] == "credit"]
    assert credits == [{"entry_type": "payment_to_balance", "direction": "credit", "amount": 20.0}]


def test_apply_to_debt_only_lowers_debt(app, client):
    did = _dist(client, debt=50)
    res = _pay(client, did, amount=20, apply_to="debt")
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["data"]["entry"]["entry_type"] == "payment_to_debt"
    bal, debt, s = _state(client, did)
    assert (bal, debt) == (10.0, 30.0)
    assert s["ledger"]["paid_to_debt"] == 20.0 and s["ledger"]["paid_to_balance"] == 0.0
    # paying exactly the rest clears the debt
    assert _pay(client, did, amount=30, apply_to="debt").status_code == 201
    assert _state(client, did)[:2] == (10.0, 0.0)
    assert len([e for e in _entries(app, did) if e["direction"] == "credit"]) == 2


def test_debt_payment_over_outstanding_is_422(app, client):
    did = _dist(client, debt=15)
    before = _entries(app, did)
    res = _pay(client, did, amount=40, apply_to="debt")
    assert res.status_code == 422, res.get_json()
    msg = res.get_json()["error"]["message"]
    assert "الدين المتبقّي 15" in msg, msg
    assert _state(client, did)[:2] == (10.0, 15.0)
    assert _entries(app, did) == before
    # no debt at all → debt payment refused too
    zero = _dist(client)
    assert _pay(client, zero, amount=1, apply_to="debt").status_code == 422


def test_default_when_apply_to_missing(app, client):
    owes = _dist(client, debt=25)
    res = _pay(client, owes, amount=5)
    assert res.status_code == 201
    assert res.get_json()["data"]["entry"]["apply_to"] == "debt"
    assert _state(client, owes)[:2] == (10.0, 20.0)
    clean = _dist(client)
    res = _pay(client, clean, amount=5)
    assert res.get_json()["data"]["entry"]["apply_to"] == "balance"
    assert _state(client, clean)[:2] == (15.0, 0.0)
    # bad value → 422
    assert _pay(client, clean, amount=5, apply_to="both").status_code == 422
    assert _pay(client, clean, amount=5, apply_to=["debt"]).status_code == 422


def test_repo_debt_guard_is_atomic(app, client):
    """The repo re-checks the debt inside the transaction (a parallel payment
    that already cleared it cannot drive it below zero / double-apply)."""
    from app.radius.db.repos import operations_repo
    did = _dist(client, debt=10)
    with app.app_context():
        operations_repo.post_distributor_ledger(
            1, did, entry_type="settlement", direction="credit", amount=10,
            currency="ILS", actor="t", apply_to="debt")
        with pytest.raises(operations_repo.DistributorDebtExceeded) as exc:
            operations_repo.post_distributor_ledger(
                1, did, entry_type="settlement", direction="credit", amount=10,
                currency="ILS", actor="t", apply_to="debt")
        assert exc.value.remaining == 0.0
    assert len([e for e in _entries(app, did) if e["direction"] == "credit"]) == 1


def test_balance_movements_label_the_effect(client):
    did = _dist(client, debt=30)
    name = client.get(f"/api/v1/distributors/{did}/summary", headers=AUTH).get_json()[
        "data"]["summary"]["distributor"]["name"]
    _pay(client, did, amount=4, apply_to="balance")
    _pay(client, did, amount=6, apply_to="debt")
    rows = client.get(f"/api/v1/operational-reports/balance-movements?q={name}&limit=50",
                      headers=AUTH).get_json()["data"]["items"]
    labels = {r["entry_type"]: r["entry_label"] for r in rows if r["scope"] == "distributor"}
    assert labels["payment_to_balance"] == "دفعة — إضافة للرصيد"
    assert labels["payment_to_debt"] == "دفعة — خصم من الدين"


def _web(app):
    web = app.test_client()
    with web.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "ap_admin"
        sess["admin_name"] = "AP Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "ap-csrf"
    return web


def test_web_form_radios_default_and_parity(app, client):
    web = _web(app)
    owes = _dist(client, debt=12)
    html = web.get(f"/admin/radius/distributors/{owes}").get_data(as_text=True)
    assert 'data-testid="dd-apply-to"' in html
    assert "إضافة للرصيد" in html and "خصم من الدين" in html
    assert 'value="debt" checked' in html and 'value="balance" checked' not in html
    assert 'data-testid="dd-cur-debt"' in html and 'data-testid="dd-cur-balance"' in html
    clean = _dist(client)
    html = web.get(f"/admin/radius/distributors/{clean}").get_data(as_text=True)
    assert 'value="balance" checked' in html and 'value="debt" checked' not in html

    # web post → same service: balance
    res = web.post(f"/admin/radius/distributors/{owes}/settle", data={
        "amount": "3", "direction": "credit", "apply_to": "balance", "_csrf_token": "ap-csrf"})
    assert res.status_code in (302, 303)
    assert _state(client, owes)[:2] == (13.0, 12.0)
    # debt
    web.post(f"/admin/radius/distributors/{owes}/settle", data={
        "amount": "2", "direction": "credit", "apply_to": "debt", "_csrf_token": "ap-csrf"})
    assert _state(client, owes)[:2] == (13.0, 10.0)
    # over-debt on the web → refused, nothing changes
    web.post(f"/admin/radius/distributors/{owes}/settle", data={
        "amount": "99", "direction": "credit", "apply_to": "debt", "_csrf_token": "ap-csrf"})
    assert _state(client, owes)[:2] == (13.0, 10.0)
    with web.session_transaction() as sess:
        flashes = [m for _c, m in sess.get("_flashes", [])]
    assert any("الدين المتبقّي 10" in m for m in flashes), flashes
