"""Stress-fix (misc stream, A11 F-4/F-7/F-14): malformed input → JSON 4xx, never HTML 500.

Each case below returned a Flask HTML 500 on client20 (2026-09-28): list/scalar
JSON bodies reaching ``body.get``/``**body``, unknown FK ids, NaN/Infinity,
dicts where text was expected. Every one must now be a JSON error envelope with
an Arabic message and a 4xx status. Also pins the IntegrityError mislabel
(«distributor name already exists» for NaN / missing admin) and Arabic texts.
"""
from __future__ import annotations

import re
import secrets
import sys

import pytest

for _m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
    sys.modules.pop(_m, None)

AUTH = {"Authorization": "Bearer dev-token-please-change"}
_ARABIC = re.compile(r"[؀-ۿ]")


@pytest.fixture(scope="module")
def client():
    from app import create_app
    return create_app().test_client()


@pytest.fixture(scope="module")
def subscriber_id(client):
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "bi_" + secrets.token_hex(3), "password": "p1234"})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["id"]


@pytest.fixture(scope="module")
def ticket_id(client, subscriber_id):
    res = client.post("/api/v1/tickets", headers=AUTH, json={
        "subscriber_id": subscriber_id, "subject": "bad-input probe"})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["id"]


@pytest.fixture(scope="module")
def distributor_id(client):
    res = client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "bi_d_" + secrets.token_hex(3), "credit_limit": 0})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]["distributor"]["id"]


def _json_error(res, *statuses):
    assert res.status_code in statuses, (res.status_code, res.get_data(as_text=True)[:300])
    body = res.get_json()
    assert body is not None, "error must be the JSON envelope, not HTML"
    assert body["ok"] is False
    assert _ARABIC.search(body["error"]["message"]), body["error"]["message"]
    return body


def test_tickets_bad_inputs(client, subscriber_id, ticket_id):
    cases = [
        ("post", "/api/v1/tickets", {"subscriber_id": 99999999, "subject": "x"}, (404,)),
        ("post", "/api/v1/tickets", {"subscriber_id": subscriber_id, "subject": "x",
                                     "assignee_admin_id": 987654}, (422,)),
        ("post", "/api/v1/tickets", [1, 2, 3], (422,)),
        ("post", "/api/v1/tickets", {"subscriber_id": 7939.9, "subject": "x"}, (422,)),
        ("post", "/api/v1/tickets", {"subscriber_id": subscriber_id, "subject": "x" * 10005}, (422,)),
        ("post", "/api/v1/tickets", {"subscriber_id": subscriber_id, "subject": "x",
                                     "attachments": "abc"}, (422,)),
        ("patch", f"/api/v1/tickets/{ticket_id}", [1, 2], (422,)),
        ("patch", f"/api/v1/tickets/{ticket_id}", {"subject": None}, (422,)),
        ("patch", f"/api/v1/tickets/{ticket_id}", {"subject": ""}, (422,)),
        ("patch", f"/api/v1/tickets/{ticket_id}", {"subject": {"a": 1}}, (422,)),
        ("patch", f"/api/v1/tickets/{ticket_id}", {"assignee_admin_id": "abc"}, (422,)),
        ("patch", f"/api/v1/tickets/{ticket_id}", {"body": [1, 2]}, (422,)),
        ("post", f"/api/v1/tickets/{ticket_id}/replies", ["a"], (422,)),
    ]
    for method, url, body, statuses in cases:
        res = getattr(client, method)(url, headers=AUTH, json=body)
        _json_error(res, *statuses)
    # unchanged after all the rejected patches
    got = client.get(f"/api/v1/tickets/{ticket_id}", headers=AUTH).get_json()["data"]["ticket"]
    assert got["subject"] == "bad-input probe"
    assert got["created_at"].endswith("Z")
    # unknown status filter is a 422, not an empty 200
    _json_error(client.get("/api/v1/tickets?status=bogus", headers=AUTH), 422)


def test_tickets_assignee_can_be_cleared(client, ticket_id):
    res = client.patch(f"/api/v1/tickets/{ticket_id}", headers=AUTH,
                       json={"assignee_admin_id": None})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["assignee_admin_id"] is None


def test_service_request_list_body(client):
    _json_error(client.post("/api/v1/service-requests", headers=AUTH, json=[1]), 422)


def test_distributors_bad_inputs(client, distributor_id):
    cases = [
        ("/api/v1/distributors", {"name": 12345}),
        ("/api/v1/distributors", [1]),
        ("/api/v1/distributors", {"name": "x" + secrets.token_hex(2), "admin_id": "abc"}),
        ("/api/v1/distributors", {"name": "x" + secrets.token_hex(2), "credit_limit": "inf"}),
        ("/api/v1/distributors", {"name": "x" + secrets.token_hex(2), "balance": "NaN"}),
        ("/api/v1/distributors", {"name": "x" + secrets.token_hex(2), "status": "Bogus"}),
        ("/api/v1/distributors", {"name": "x" + secrets.token_hex(2), "permissions": "not-a-list"}),
        ("/api/v1/distributors", {"name": "x" * 10005}),
        (f"/api/v1/distributors/{distributor_id}/assign-batch", [1]),
        (f"/api/v1/distributors/{distributor_id}/settle", [1]),
        (f"/api/v1/distributors/{distributor_id}/settle", {"amount": "NaN", "direction": "debit"}),
        (f"/api/v1/distributors/{distributor_id}/settle", {"amount": "Infinity", "direction": "credit"}),
        (f"/api/v1/distributors/{distributor_id}/settle", {"amount": 1e300, "direction": "debit"}),
        (f"/api/v1/distributors/{distributor_id}/settle", {"amount": 5, "direction": "sideways"}),
    ]
    for url, body in cases:
        _json_error(client.post(url, headers=AUTH, json=body), 422)
    # the list is still strict JSON (no Infinity token anywhere)
    raw = client.get("/api/v1/distributors", headers=AUTH).get_data(as_text=True)
    assert "Infinity" not in raw and "NaN" not in raw


def test_distributor_integrity_errors_are_not_mislabelled(client):
    # NaN used to surface as «distributor name already exists» (English + wrong).
    body = _json_error(client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "bi_nan_" + secrets.token_hex(3), "balance": "NaN"}), 422)
    assert "مستخدم" not in body["error"]["message"]
    body = _json_error(client.post("/api/v1/distributors", headers=AUTH, json={
        "name": "bi_adm_" + secrets.token_hex(3), "admin_id": 987654}), 422)
    assert "المدير" in body["error"]["message"]
    name = "bi_dup_" + secrets.token_hex(3)
    assert client.post("/api/v1/distributors", headers=AUTH, json={"name": name}).status_code == 201
    body = _json_error(client.post("/api/v1/distributors", headers=AUTH, json={"name": name}), 422)
    assert "مستخدم مسبقًا" in body["error"]["message"]


def test_distributor_not_found_is_arabic_404(client):
    body = _json_error(client.get("/api/v1/distributors/99999999/summary", headers=AUTH), 404)
    assert "غير موجود" in body["error"]["message"]


def test_distributors_negative_limit_is_clamped(client):
    for i in range(3):
        client.post("/api/v1/distributors", headers=AUTH, json={"name": f"bi_l{i}_" + secrets.token_hex(2)})
    res = client.get("/api/v1/distributors?limit=-1", headers=AUTH)
    assert res.status_code == 200
    assert res.get_json()["data"]["count"] == 1
    assert res.get_json()["data"]["has_more"] is True


def test_tickets_list_has_more(client, subscriber_id):
    for i in range(3):
        client.post("/api/v1/tickets", headers=AUTH, json={
            "subscriber_id": subscriber_id, "subject": f"page {i}"})
    total = len(client.get("/api/v1/tickets?limit=500", headers=AUTH).get_json()["data"]["items"])
    exact = client.get(f"/api/v1/tickets?limit={total}", headers=AUTH).get_json()["data"]
    assert exact["has_more"] is False
    short = client.get(f"/api/v1/tickets?limit={total - 1}", headers=AUTH).get_json()["data"]
    assert short["has_more"] is True and short["count"] == total - 1


def test_events_bad_inputs(client):
    _json_error(client.post("/api/v1/events", headers=AUTH, json={
        "category": "system", "event_key": "k", "target_id": [1, 2]}), 422)
    _json_error(client.post("/api/v1/events", headers=AUTH, json=[1]), 422)
    _json_error(client.post("/api/v1/events", headers=AUTH, json={
        "category": {"a": 1}, "event_key": "k"}), 422)
    _json_error(client.get("/api/v1/events?category=bogus", headers=AUTH), 422)
    # actor_id dict no longer reaches SQLite (actor comes from the token)
    res = client.post("/api/v1/events", headers=AUTH, json={
        "category": "system", "event_key": "k", "actor_id": {"a": 1}})
    assert res.status_code == 201, res.get_json()


def test_investigation_bad_inputs(client):
    _json_error(client.post("/api/v1/events-center/investigations", headers=AUTH, json=[1]), 422)
    _json_error(client.post("/api/v1/events-center/investigations", headers=AUTH,
                            json={"title": {"x": 1}}), 422)
    _json_error(client.post("/api/v1/events-center/investigations", headers=AUTH,
                            json={"title": "t", "entity_id": "abc"}), 422)


def test_admin_password_list_body(client):
    res = client.post("/api/admin/password", headers=AUTH, json=[1])
    # 401 when the dev env-token is not an admin login token, 422 otherwise —
    # either way a JSON envelope, never the HTML 500 page.
    assert res.status_code in (401, 422), res.status_code
    assert res.get_json()["ok"] is False
    res = client.post("/api/admin/login", json=[1])
    assert res.status_code == 422 and res.get_json()["ok"] is False


def test_card_user_360_unknown_is_404(client):
    res = client.get("/api/v1/card-users/99999999/360", headers=AUTH)
    assert res.status_code == 404, res.get_json()
