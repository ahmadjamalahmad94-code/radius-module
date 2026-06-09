"""Web UI smoke tests for recycle-bin restore flow."""
from __future__ import annotations

import secrets
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO access_plans(
                    id, tenant_id, name, code, plan_type, service_type,
                    duration_minutes, validity_days, speed_down_kbps,
                    speed_up_kbps, price, currency, enabled, created_at
                )
                VALUES(1,1,'Recycle Web UI Test Plan','RECYCLEWEB','time','Hotspot',
                       1440,1,4000,2000,1,'JOD',1,?)
                """,
                (now_iso(),),
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"recycle_web_{uuid4().hex[:10]}"
    password = "recycle-web-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Recycle Web Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str) -> str:
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _auth_headers(client) -> dict:
    from app.radius.db.repos import admins_repo

    username = f"recycle_api_{uuid4().hex[:10]}"
    password = "recycle-api-pass"
    admins_repo.create_admin(
        username=username,
        password=password,
        full_name="Recycle API Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/api/admin/login",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _subscriber(client) -> dict:
    username = "recycleui_" + secrets.token_hex(5)
    res = client.post(
        "/api/v1/accounts",
        json={"username": username, "password": "pw1234", "plan_id": 1},
        headers=_auth_headers(client),
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def test_recycle_bin_web_route_is_login_guarded(client):
    res = client.get("/admin/radius/recycle-bin", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_recycle_bin_lists_archived_subscriber_and_restores(client):
    _web_login(client)
    sub = _subscriber(client)
    token = _csrf(client, "/admin/radius/users")

    deleted = client.post(
        f"/admin/radius/users/{sub['username']}/delete",
        data={"_csrf_token": token},
        follow_redirects=True,
    )
    assert deleted.status_code == 200

    recycle = client.get("/admin/radius/recycle-bin?entity_type=subscribers")
    assert recycle.status_code == 200
    html = recycle.get_data(as_text=True)
    assert sub["username"] in html
    assert "استعادة" in html

    restored = client.post(
        f"/admin/radius/recycle-bin/subscribers/{sub['id']}/restore",
        data={"_csrf_token": token},
        follow_redirects=True,
    )
    assert restored.status_code == 200
    assert sub["username"] not in restored.get_data(as_text=True)

    users = client.get("/admin/radius/users?q=" + sub["username"])
    assert users.status_code == 200
    assert sub["username"] in users.get_data(as_text=True)
