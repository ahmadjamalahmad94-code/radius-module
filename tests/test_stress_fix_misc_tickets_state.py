"""Stress-fix (misc stream, A11 F-9): ticket / service-request state machine.

* reopening a closed ticket clears ``closed_at`` (API + web);
* a rejected (closed) service request cannot be approved afterwards → 409;
* of 8 parallel decisions on one request exactly one wins, the rest get 409
  and only one «قرار الإدارة» reply is appended;
* web ticket status rejects unknown statuses; web create rejects an unknown
  subscriber instead of an FK 500.
"""
from __future__ import annotations

import secrets
import sys
import threading
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


def _subscriber(client) -> int:
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "tk_" + secrets.token_hex(4), "password": "p1234"})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["id"]


def _service_request(client) -> int:
    res = client.post("/api/v1/service-requests", headers=AUTH, json={
        "subscriber_id": _subscriber(client), "service_key": "customer_portal",
        "request_type": "activation"})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["ticket"]["id"]


def test_reopen_clears_closed_at(client):
    res = client.post("/api/v1/tickets", headers=AUTH, json={
        "subscriber_id": _subscriber(client), "subject": "reopen me"})
    tid = res.get_json()["data"]["id"]
    closed = client.patch(f"/api/v1/tickets/{tid}", headers=AUTH, json={"status": "closed"})
    first_closed_at = closed.get_json()["data"]["closed_at"]
    assert first_closed_at
    # closing again keeps the original close stamp
    again = client.patch(f"/api/v1/tickets/{tid}", headers=AUTH, json={"status": "closed"})
    assert again.get_json()["data"]["closed_at"] == first_closed_at
    reopened = client.patch(f"/api/v1/tickets/{tid}", headers=AUTH, json={"status": "open"})
    data = reopened.get_json()["data"]
    assert data["status"] == "open"
    assert data["closed_at"] is None


def test_rejected_request_cannot_be_approved(client):
    tid = _service_request(client)
    res = client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                      json={"decision": "reject"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["ticket"]["status"] == "closed"
    for decision in ("approve", "trial", "reject"):
        res = client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                          json={"decision": decision})
        assert res.status_code == 409, (decision, res.get_json())
        assert res.get_json()["error"]["code"] == "conflict"
    ticket = client.get(f"/api/v1/tickets/{tid}", headers=AUTH).get_json()["data"]["ticket"]
    assert ticket["status"] == "closed" and ticket["closed_at"]


def test_approve_once_then_reject_allowed(client):
    tid = _service_request(client)
    ok1 = client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                      json={"decision": "approve"})
    assert ok1.status_code == 200 and ok1.get_json()["data"]["ticket"]["status"] == "in_progress"
    again = client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                        json={"decision": "approve"})
    assert again.status_code == 409
    # a client that saw the request as «open» cannot decide on stale state
    stale = client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                        json={"decision": "reject", "expected_status": "open"})
    assert stale.status_code == 409
    # preliminary approval → reject is the documented legitimate sequence
    rej = client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                      json={"decision": "reject"})
    assert rej.status_code == 200
    again = client.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH,
                        json={"decision": "reject"})
    assert again.status_code == 409


def test_compare_and_set_single_winner(app):
    from app.radius.db.repos import tickets_repo
    from app.radius.core.types_saas import Ticket

    with app.app_context():
        client = app.test_client()
        sid = _subscriber(client)
        t = tickets_repo.create_ticket(Ticket(id=None, tenant_id=1, subscriber_id=sid, subject="cas"))
        status, version = tickets_repo.ticket_version(1, t.id)
        assert tickets_repo.compare_and_set_status(
            1, t.id, expected_status=status, expected_version=version, new_status="in_progress")
        # same (stale) version again → loses
        assert not tickets_repo.compare_and_set_status(
            1, t.id, expected_status=status, expected_version=version, new_status="closed")
        assert tickets_repo.get_ticket(1, t.id).status == "in_progress"


@pytest.mark.parametrize("mode", ["same", "mixed_pinned"])
def test_parallel_decisions_only_one_wins(app, client, mode):
    """8 parallel decisions on one request: exactly one wins. «same» = 8×approve;
    «mixed_pinned» = approve/reject alternating, each pinned to the state the
    operator saw (expected_status=open) — the stress repro's 8×200 is gone."""
    tid = _service_request(client)
    results: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(i: int) -> None:
        c = app.test_client()
        barrier.wait()
        if mode == "same":
            body = {"decision": "approve"}
        else:
            body = {"decision": "approve" if i % 2 else "reject", "expected_status": "open"}
        res = c.post(f"/api/v1/service-requests/{tid}/decision", headers=AUTH, json=body)
        with lock:
            results.append(res.status_code)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results.count(200) == 1, results
    assert results.count(409) == 7, results
    detail = client.get(f"/api/v1/tickets/{tid}", headers=AUTH).get_json()["data"]
    decisions = [r for r in detail["replies"] if r["body"].startswith("قرار الإدارة")]
    assert len(decisions) == 1


# ── web parity ──────────────────────────────────────────────────────────

def _web_login(client) -> str:
    from app.radius.db.repos import admins_repo

    username = f"tk_web_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=username, password="tk-web-pass",
                             full_name="Tickets Web", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": "tk-web-pass"})
    assert res.status_code in (302, 303), res.status_code
    assert "/login" not in (res.headers.get("Location") or ""), res.headers.get("Location")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tk-csrf"
    return "tk-csrf"


def test_web_ticket_status_and_create_validation(app):
    web = app.test_client()
    csrf = _web_login(web)
    api = app.test_client()
    sid = _subscriber(api)
    tid = api.post("/api/v1/tickets", headers=AUTH, json={
        "subscriber_id": sid, "subject": "web"}).get_json()["data"]["id"]

    res = web.post(f"/admin/radius/tickets/{tid}/status",
                   data={"status": "bogus", "_csrf_token": csrf})
    assert res.status_code in (302, 303), res.status_code
    assert api.get(f"/api/v1/tickets/{tid}", headers=AUTH).get_json()["data"]["ticket"]["status"] == "open"

    web.post(f"/admin/radius/tickets/{tid}/status", data={"status": "closed", "_csrf_token": csrf})
    web.post(f"/admin/radius/tickets/{tid}/status", data={"status": "open", "_csrf_token": csrf})
    t = api.get(f"/api/v1/tickets/{tid}", headers=AUTH).get_json()["data"]["ticket"]
    assert t["status"] == "open" and t["closed_at"] is None

    # unknown subscriber → friendly redirect (was an FK 500)
    res = web.post("/admin/radius/tickets", data={
        "subscriber_id": "99999999", "subject": "x", "_csrf_token": csrf})
    assert res.status_code in (302, 303)
    # empty subject rejected too
    before = len(api.get("/api/v1/tickets?limit=500", headers=AUTH).get_json()["data"]["items"])
    web.post("/admin/radius/tickets", data={"subscriber_id": str(sid), "subject": "",
                                            "_csrf_token": csrf})
    after = len(api.get("/api/v1/tickets?limit=500", headers=AUTH).get_json()["data"]["items"])
    assert after == before
