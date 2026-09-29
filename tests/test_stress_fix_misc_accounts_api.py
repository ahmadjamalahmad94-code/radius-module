"""Stress-fix (misc stream, A01 API items + app H4 server-side search).

* custom_price honoured on create + PATCH (web form field; app sends it);
* create validates the username like rename (no spaces/Arabic/emoji/«/»),
  on the API and the web form (shared UsersService.create);
* trial accounts are listed; PATCH user_type=card is refused (was hiding
  the subscriber); unknown user_type message is Arabic;
* DELETE / reset_password of an unknown user → 404 (was 200);
* limit=-1 no longer removes the cap;
* GET /accounts: q (alias search) + page/per_page or limit/offset with total
  and has_more; status=active alias; literal LIKE (``%``/``_``); fast on 2.5k.
"""
from __future__ import annotations

import secrets
import sys
import time

import pytest

for _m in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
    sys.modules.pop(_m, None)

AUTH = {"Authorization": "Bearer dev-token-please-change"}
TAG = "sa" + secrets.token_hex(2)


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


def _create(client, username, **extra):
    return client.post("/api/v1/accounts", headers=AUTH,
                       json={"username": username, "password": "p1234", **extra})


def test_custom_price_create_and_patch(client):
    u = f"{TAG}_cp"
    res = _create(client, u, custom_price=12.5)
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["data"]["custom_price"] == 12.5
    res = client.patch(f"/api/v1/accounts/{u}", headers=AUTH, json={"custom_price": "7.25"})
    assert res.status_code == 200 and res.get_json()["data"]["custom_price"] == 7.25
    for bad in ("inf", "NaN", -1, "abc", True):
        res = client.patch(f"/api/v1/accounts/{u}", headers=AUTH, json={"custom_price": bad})
        assert res.status_code == 422, (bad, res.get_json())
    assert client.get(f"/api/v1/accounts/{u}", headers=AUTH).get_json()["data"]["custom_price"] == 7.25


@pytest.mark.parametrize("bad", ["a b", "علي", "u😀", "a/b", "x" * 65, " "])
def test_create_rejects_bad_usernames(client, bad):
    res = _create(client, bad)
    assert res.status_code == 422, (bad, res.status_code, res.get_json())
    assert res.get_json()["ok"] is False


def test_create_accepts_rename_charset(client):
    for name in (f"{TAG}.a-b_c@d", f"{TAG}123"):
        assert _create(client, name).status_code == 201


def test_web_create_rejects_bad_username(app):
    web = app.test_client()
    with web.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "acc_admin"
        sess["admin_name"] = "Acc Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "a-csrf"
    res = web.post("/admin/radius/users", data={
        "username": "bad name/x", "password": "p1234", "_csrf_token": "a-csrf"})
    # fix wave 2: web validation errors are 422, the same status as the API
    assert res.status_code == 422
    assert "اسم الدخول يسمح" in res.get_data(as_text=True)
    from app.radius.db.connection import db
    with app.app_context():
        assert db().execute("SELECT 1 FROM subscribers WHERE username = 'bad name/x'").fetchone() is None


def test_trial_accounts_are_listed_and_card_type_refused(client):
    u = f"{TAG}_trial"
    assert _create(client, u, user_type="trial").status_code == 201
    items = client.get(f"/api/v1/accounts?q={u}", headers=AUTH).get_json()["data"]["items"]
    assert [i["username"] for i in items] == [u]
    only_trial = client.get("/api/v1/accounts?user_type=trial", headers=AUTH).get_json()["data"]["items"]
    assert u in [i["username"] for i in only_trial]
    assert all(i["user_type"] == "trial" for i in only_trial)
    res = client.patch(f"/api/v1/accounts/{u}", headers=AUTH, json={"user_type": "card"})
    assert res.status_code == 422
    res = _create(client, f"{TAG}_adm", user_type="admin")
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert "unknown" not in msg and "نوع الحساب" in msg
    assert client.get("/api/v1/accounts?user_type=card", headers=AUTH).status_code == 422


def test_delete_and_reset_unknown_user_is_404(client):
    ghost = f"{TAG}_ghost"
    assert client.delete(f"/api/v1/accounts/{ghost}", headers=AUTH).status_code == 404
    res = client.post(f"/api/v1/accounts/{ghost}/reset_password", headers=AUTH,
                      json={"new_password": "x1234"})
    assert res.status_code == 404
    real = f"{TAG}_del"
    assert _create(client, real).status_code == 201
    assert client.delete(f"/api/v1/accounts/{real}", headers=AUTH).status_code == 200
    # already archived → 404 the second time
    assert client.delete(f"/api/v1/accounts/{real}", headers=AUTH).status_code == 404


def test_negative_limit_is_clamped(client):
    for i in range(3):
        _create(client, f"{TAG}_lim{i}")
    data = client.get("/api/v1/accounts?limit=-1", headers=AUTH).get_json()["data"]
    assert data["count"] == 1 and data["limit"] == 1


def test_server_side_search_paging_contract(app, client):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    base = f"{TAG}srch"
    with app.app_context():
        for i in range(2500):
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, tenant_id=1, username=f"{base}{i:05d}", password="p",
                full_name=f"اسم {i}", mobile=f"059{i:07d}", status="enabled"))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username=f"{base}_under", password="p",
            full_name="with_underscore", status="disabled"))
    # an OLD subscriber (not among the newest 100) is found by q
    t0 = time.perf_counter()
    res = client.get(f"/api/v1/accounts?q={base}00005&per_page=20&page=1", headers=AUTH)
    elapsed = time.perf_counter() - t0
    data = res.get_json()["data"]
    assert [i["username"] for i in data["items"]] == [f"{base}00005"]
    assert data["total"] == 1 and data["has_more"] is False
    assert elapsed < 2.0, elapsed
    # search by phone and by name
    assert client.get("/api/v1/accounts?q=0590000007", headers=AUTH).get_json()["data"]["total"] == 1
    # page walk with totals
    p1 = client.get(f"/api/v1/accounts?q={base}&per_page=500&page=1", headers=AUTH).get_json()["data"]
    assert p1["total"] == 2501 and p1["count"] == 500 and p1["has_more"] is True
    assert p1["page"] == 1 and p1["per_page"] == 500
    p6 = client.get(f"/api/v1/accounts?q={base}&per_page=500&page=6", headers=AUTH).get_json()["data"]
    assert p6["count"] == 1 and p6["has_more"] is False and p6["offset"] == 2500
    # limit/offset form gives the same totals
    lo = client.get(f"/api/v1/accounts?search={base}&limit=10&offset=2495", headers=AUTH).get_json()["data"]
    assert lo["total"] == 2501 and lo["count"] == 6
    # status filters + active alias
    en = client.get(f"/api/v1/accounts?q={base}&status=active&limit=1", headers=AUTH).get_json()["data"]
    assert en["total"] == 2500
    dis = client.get(f"/api/v1/accounts?q={base}&status=disabled", headers=AUTH).get_json()["data"]
    assert dis["total"] == 1
    assert client.get("/api/v1/accounts?status=weird", headers=AUTH).status_code == 422
    # LIKE wildcards are literal
    assert client.get("/api/v1/accounts?q=%25", headers=AUTH).get_json()["data"]["total"] == 0
    und = client.get("/api/v1/accounts?q=with_underscore", headers=AUTH).get_json()["data"]
    assert und["total"] == 1
    assert client.get("/api/v1/accounts?q=with%5Funderscore", headers=AUTH).get_json()["data"]["total"] == 1
    assert client.get("/api/v1/accounts?q=withXunderscore", headers=AUTH).get_json()["data"]["total"] == 0
    assert client.get("/api/v1/accounts?page=abc", headers=AUTH).status_code == 422
