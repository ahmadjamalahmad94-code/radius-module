"""Stress-fix (misc stream, A11 F-8 + A10 F12): events.

* events-center date filter: from=to=<local today> returns today's events
  (the ``to`` day was dropped by a lexical compare); ``to`` is inclusive;
* events-center paging: limit/offset + total/has_more (was capped at 200);
* /events: actor comes from the token (``actor_type: system`` can't be forged),
  keyset paging with before_id + exact has_more, parsed ``metadata``;
* money actions (every accounting ledger row) emit a ``financial`` event
  ``ledger.<entry_type>`` targeted at the subscriber (web labels exist);
* web events center: same-day filter + pager.
"""
from __future__ import annotations

import secrets
import sys
from datetime import timedelta
from uuid import uuid4

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


def _today_local(app) -> str:
    from app.radius.core.system_config import local_today
    with app.app_context():
        return local_today().isoformat()


def _record(client, key: str, n: int) -> None:
    for i in range(n):
        res = client.post("/api/v1/events", headers=AUTH, json={
            "category": "system", "event_key": key, "message": f"m{i}",
            "target_type": key, "target_id": i + 1, "metadata": {"i": i}})
        assert res.status_code == 201, res.get_json()


def test_same_day_filter_and_inclusive_to(app, client):
    key = "ev" + secrets.token_hex(3)
    _record(client, key, 3)
    today = _today_local(app)
    res = client.get(f"/api/v1/events-center?target_type={key}&from={today}&to={today}", headers=AUTH)
    data = res.get_json()["data"]
    assert res.status_code == 200 and data["count"] == 3 and data["total"] == 3
    # to=yesterday excludes today; from=tomorrow excludes today
    from datetime import date
    d = date.fromisoformat(today)
    y, t = (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()
    assert client.get(f"/api/v1/events-center?target_type={key}&to={y}", headers=AUTH).get_json()["data"]["count"] == 0
    assert client.get(f"/api/v1/events-center?target_type={key}&from={t}", headers=AUTH).get_json()["data"]["count"] == 0
    assert client.get(f"/api/v1/events-center?target_type={key}&from={y}&to={today}", headers=AUTH).get_json()["data"]["count"] == 3
    # bad date → 422 (not a silent full list / 500)
    bad = client.get(f"/api/v1/events-center?target_type={key}&from=2026-13-45", headers=AUTH)
    assert bad.status_code == 422


def test_date_bound_is_local_day(app):
    """A ``YYYY-MM-DD`` bound is the panel's LOCAL day expressed in UTC."""
    from app.radius.core.system_config import local_period_utc_range
    from app.radius.services.events_risk_center import _date_bound
    with app.app_context():
        start, stop = local_period_utc_range("daily", "2026-09-28")
        assert _date_bound("2026-09-28", end=False) == start
        assert _date_bound("2026-09-28", end=True) == stop
        assert _date_bound("2026-09-28T10:00:00Z", end=True) == "2026-09-28 10:00:00"
        assert _date_bound("2026-09-28T13:00:00+03:00", end=False) == "2026-09-28 10:00:00"


def test_events_center_paging(client):
    key = "pg" + secrets.token_hex(3)
    _record(client, key, 7)
    seen: list[int] = []
    offset = 0
    while True:
        data = client.get(f"/api/v1/events-center?target_type={key}&limit=3&offset={offset}",
                          headers=AUTH).get_json()["data"]
        assert data["total"] == 7
        seen += [e["id"] for e in data["events"]]
        if not data["has_more"]:
            break
        offset += 3
    assert len(seen) == 7 == len(set(seen))


def test_actor_cannot_be_spoofed(client):
    res = client.post("/api/v1/events", headers=AUTH, json={
        "category": "security", "event_key": "spoof", "actor_type": "system", "actor_id": 1})
    assert res.status_code == 201
    ev = res.get_json()["data"]["event"]
    assert ev["actor_type"] != "system"
    assert ev["actor_type"] in ("admin", "api_token")
    assert ev["metadata"] == {}


def test_business_events_keyset_paging(client):
    _record(client, "ks" + secrets.token_hex(2), 5)
    first = client.get("/api/v1/events?limit=2", headers=AUTH).get_json()["data"]
    assert first["has_more"] is True and first["next_before_id"] == first["items"][-1]["id"]
    assert isinstance(first["items"][0]["metadata"], dict)
    # a new event arriving between pages must not shift page 2 (no duplicates)
    _record(client, "ks-late", 1)
    second = client.get(f"/api/v1/events?limit=2&before_id={first['next_before_id']}",
                        headers=AUTH).get_json()["data"]
    ids1 = {e["id"] for e in first["items"]}
    assert not ids1 & {e["id"] for e in second["items"]}
    assert all(e["id"] < first["next_before_id"] for e in second["items"])
    # has_more is exact at the end of the list
    total = len(client.get("/api/v1/events?limit=500", headers=AUTH).get_json()["data"]["items"])
    tail = client.get(f"/api/v1/events?limit={total}", headers=AUTH).get_json()["data"]
    assert tail["has_more"] is False and tail["next_before_id"] is None


def test_money_actions_emit_financial_events(app, client):
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "evm_" + secrets.token_hex(3), "password": "p1234"})
    sub = res.get_json()["data"]
    from app.radius.db.connection import transaction
    from app.radius.db.repos import accounting_repo
    with app.app_context():
        with transaction() as conn:
            entry_id = accounting_repo.create_ledger_entry(
                conn, tenant_id=1, entry_type="payment", amount=30.0,
                currency="ILS", subscriber_id=sub["id"], username=sub["username"],
                operator="api-token:env", source_type="test")
        accounting_repo.void_ledger_entry(tenant_id=1, entry_id=entry_id, actor="tester")
    data = client.get(
        f"/api/v1/events-center?category=financial&target_type=subscriber&target_id={sub['id']}",
        headers=AUTH).get_json()["data"]
    keys = sorted(e["event_key"] for e in data["events"])
    assert keys == ["ledger.payment", "ledger.void"], keys
    pay = next(e for e in data["events"] if e["event_key"] == "ledger.payment")
    assert pay["metadata"]["ledger_entry_id"] == entry_id
    assert pay["metadata"]["amount"] == 30.0
    assert pay["event_key_label"] == "قيد دفعة"
    # the subscriber 360 timeline already lists ledger rows — no duplicate mirror
    from app.radius.services.subscriber_360 import Subscriber360Service
    with app.app_context():
        evs = Subscriber360Service(tenant_id=1)._business_events(sub["id"])
    assert not [e for e in evs if str(e.get("event_key", "")).startswith("ledger.")]


def test_web_events_center_same_day_and_pager(app, client):
    from app.radius.db.repos import admins_repo
    key = "wb" + secrets.token_hex(3)
    _record(client, key, 3)
    web = app.test_client()
    username = f"ev_web_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=username, password="ev-web-pass",
                             full_name="Events Web", is_super_admin=True, role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
    assert web.post("/admin/radius/login", data={"username": username,
                                                 "password": "ev-web-pass"}).status_code in (302, 303)
    today = _today_local(app)
    page = web.get(f"/admin/radius/events?target_type={key}&from={today}&to={today}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert html.count('href="/admin/radius/events/') >= 3
    # invalid date → friendly page, not 500
    assert web.get("/admin/radius/events?from=2026-99-99").status_code == 200
