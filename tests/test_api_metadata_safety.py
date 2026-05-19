"""
Regression test for the metadata-wipe bug (Slice A stabilization).

Before the fix, sending `{"metadata": "{bad-json"}` on PATCH would:
  - return 200 OK
  - silently normalise metadata to "{}"
  - wipe any previously-saved metadata on the row

After the fix both /api/v1/accounts and /api/v1/profiles must:
  - return 422 with code "validation_error"
  - leave the stored metadata untouched
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(client):
    res = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "admin"},
    )
    token = res.get_json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}


# ─────────────── accounts ───────────────

def test_account_invalid_metadata_string_does_not_wipe(client, auth_headers):
    username = f"qa_meta_{int(time.time() * 1000)}"
    seed_meta = {
        "mikrotik": {"profile": "KEEP_ME"},
        "general": {"notes": "must survive"},
    }
    create = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234", "metadata": seed_meta},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.get_json()
    try:
        before = client.get(
            f"/api/v1/accounts/{username}", headers=auth_headers
        ).get_json()["data"]["metadata"]

        bad = client.patch(
            f"/api/v1/accounts/{username}",
            json={"metadata": "{bad-json"},
            headers=auth_headers,
        )
        assert bad.status_code == 422
        body = bad.get_json()
        assert body["ok"] is False
        assert body["error"]["code"] == "validation_error"

        after = client.get(
            f"/api/v1/accounts/{username}", headers=auth_headers
        ).get_json()["data"]["metadata"]
        assert before == after, (
            "metadata must not be wiped by an invalid patch"
        )
    finally:
        client.delete(f"/api/v1/accounts/{username}", headers=auth_headers)


def test_account_invalid_metadata_type_returns_422(client, auth_headers):
    username = f"qa_meta_t_{int(time.time() * 1000)}"
    client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234"},
        headers=auth_headers,
    )
    try:
        for bad in (42, True):
            res = client.patch(
                f"/api/v1/accounts/{username}",
                json={"metadata": bad},
                headers=auth_headers,
            )
            assert res.status_code == 422, (bad, res.get_json())
    finally:
        client.delete(f"/api/v1/accounts/{username}", headers=auth_headers)


def test_account_valid_metadata_paths_still_work(client, auth_headers):
    username = f"qa_meta_ok_{int(time.time() * 1000)}"
    client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234"},
        headers=auth_headers,
    )
    try:
        # dict
        res = client.patch(
            f"/api/v1/accounts/{username}",
            json={"metadata": {"general": {"notes": "from dict"}}},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert (
            res.get_json()["data"]["metadata"]["general"]["notes"]
            == "from dict"
        )

        # JSON string
        res = client.patch(
            f"/api/v1/accounts/{username}",
            json={"metadata": '{"general": {"notes": "from string"}}'},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert (
            res.get_json()["data"]["metadata"]["general"]["notes"]
            == "from string"
        )
    finally:
        client.delete(f"/api/v1/accounts/{username}", headers=auth_headers)


# ─────────────── profiles ───────────────

def test_profile_invalid_metadata_does_not_wipe(client, auth_headers):
    name = f"qa_meta_plan_{int(time.time() * 1000)}"
    seed_meta = {"mikrotik": {"profile": "KEEP"}}
    create = client.post(
        "/api/v1/profiles",
        json={"name": name, "plan_type": "time", "metadata": seed_meta},
        headers=auth_headers,
    )
    assert create.status_code == 201, create.get_json()
    pid = create.get_json()["data"]["id"]
    try:
        before = client.get(
            f"/api/v1/profiles/{pid}", headers=auth_headers
        ).get_json()["data"]["metadata"]

        bad = client.patch(
            f"/api/v1/profiles/{pid}",
            json={"metadata": "{bad"},
            headers=auth_headers,
        )
        assert bad.status_code == 422
        assert bad.get_json()["error"]["code"] == "validation_error"

        after = client.get(
            f"/api/v1/profiles/{pid}", headers=auth_headers
        ).get_json()["data"]["metadata"]
        assert before == after
    finally:
        client.delete(f"/api/v1/profiles/{pid}", headers=auth_headers)
