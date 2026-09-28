"""Stress-fix (misc stream, A11 F-10/F-11): message templates + notifications paging.

* creating a template whose key already exists (case-folded) → 409, the
  existing template is untouched; ``overwrite: true`` is the explicit update;
  the web form refuses the duplicate unless «استبدال» is ticked;
* notifications: keyset paging via ``before_id`` has no duplicates when new
  rows arrive between pages; ``has_more`` is exact at the end.
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


def test_duplicate_template_key_is_409(client):
    key = "st_tpl_" + secrets.token_hex(3)
    first = client.post("/api/v1/communications/templates", headers=AUTH, json={
        "template_key": key, "title": "first", "body": "b1"})
    assert first.status_code == 201, first.get_json()
    tpl_id = first.get_json()["data"]["template"]["id"]
    dup = client.post("/api/v1/communications/templates", headers=AUTH, json={
        "template_key": key.upper(), "title": "second", "body": "b2"})
    assert dup.status_code == 409, dup.get_json()
    assert dup.get_json()["error"]["code"] == "conflict"
    items = client.get("/api/v1/communications/templates", headers=AUTH).get_json()["data"]["items"]
    row = next(t for t in items if t["id"] == tpl_id)
    assert row["title"] == "first"
    # explicit update
    upd = client.post("/api/v1/communications/templates", headers=AUTH, json={
        "template_key": key, "title": "second", "body": "b2", "overwrite": True})
    assert upd.status_code == 201
    assert upd.get_json()["data"]["template"]["id"] == tpl_id
    assert upd.get_json()["data"]["template"]["title"] == "second"


def test_web_template_duplicate_needs_overwrite(app):
    from app.radius.services.notification_campaigns import NotificationCampaignService
    web = app.test_client()
    with web.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "tpl_admin"
        sess["admin_name"] = "Tpl Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "tpl-csrf"
    key = "web_tpl_" + secrets.token_hex(3)
    form = {"template_key": key, "title": "one", "body": "x", "channel": "internal",
            "_csrf_token": "tpl-csrf"}
    assert web.post("/admin/radius/communications/templates", data=form).status_code in (302, 303)
    web.post("/admin/radius/communications/templates", data={**form, "title": "two"})
    with app.app_context():
        assert NotificationCampaignService(tenant_id=1).get_template(key)["title"] == "one"
    web.post("/admin/radius/communications/templates",
             data={**form, "title": "three", "overwrite": "1"})
    with app.app_context():
        assert NotificationCampaignService(tenant_id=1).get_template(key)["title"] == "three"


def _seed(app, n: int, tag: str) -> list[int]:
    from app.radius.db.repos import notifications_repo
    with app.app_context():
        return [notifications_repo.create(1, type="system", severity="info",
                                          title=f"{tag} {i}", body="b", link="/",
                                          dedup_key=f"{tag}-{i}-{secrets.token_hex(2)}")
                for i in range(n)]


def test_notifications_keyset_no_duplicates(app, client):
    _seed(app, 10, "a")
    p1 = client.get("/api/v1/notifications?limit=4", headers=AUTH).get_json()["data"]
    assert p1["has_more"] is True
    cursor = p1["next_before_id"]
    assert cursor == p1["items"][-1]["id"]
    _seed(app, 5, "late")  # arrive between pages
    p2 = client.get(f"/api/v1/notifications?limit=4&before_id={cursor}", headers=AUTH).get_json()["data"]
    ids1 = {i["id"] for i in p1["items"]}
    ids2 = {i["id"] for i in p2["items"]}
    assert not ids1 & ids2
    assert all(i < cursor for i in ids2)
    # walk to the end: has_more flips exactly on the last non-empty page
    seen = ids1 | ids2
    cur = p2["next_before_id"]
    while cur:
        page = client.get(f"/api/v1/notifications?limit=4&before_id={cur}", headers=AUTH).get_json()["data"]
        assert page["items"], "has_more promised a page that came back empty"
        assert not seen & {i["id"] for i in page["items"]}
        seen |= {i["id"] for i in page["items"]}
        cur = page["next_before_id"]
    assert client.get("/api/v1/notifications?before_id=abc", headers=AUTH).status_code == 422


def test_notifications_offset_has_more_exact(app, client):
    _seed(app, 5, "ofs")
    total = len(client.get("/api/v1/notifications?limit=100", headers=AUTH).get_json()["data"]["items"])
    assert total < 100
    page = client.get(f"/api/v1/notifications?limit={total}", headers=AUTH).get_json()["data"]
    assert page["has_more"] is False
    page = client.get(f"/api/v1/notifications?limit={total - 1}", headers=AUTH).get_json()["data"]
    assert page["has_more"] is True
