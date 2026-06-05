from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash


AUTH = {"Authorization": "Bearer dev-token-please-change"}


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "subscriber_portal_api.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file

    @flask_app.before_request
    def _bind_subscriber_portal_test_db():
        os.environ["HOBERADIUS_DB_PATH"] = db_file
        from app.radius.db.connection import reset_for_tests

        reset_for_tests(db_file)

    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _plan(name: str = "Subscriber Portal Plan", *, loan_enabled: bool = True, max_loan_minutes: int = 2880) -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            loan_enabled, max_loan_minutes, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 24 * 60, 30, 10.0, "ILS", 1 if loan_enabled else 0, max_loan_minutes),
    )
    return int(cur.lastrowid)


def _subscriber(
    username: str = "subscriber-portal-user",
    password: str = "portal-pass",
    *,
    expired: bool = False,
    hashed: bool = False,
    plan_id: int | None = None,
) -> int:
    plan_id = plan_id or _plan(f"Plan {username}")
    expire = (datetime.utcnow() - timedelta(days=1) if expired else datetime.utcnow() + timedelta(days=30)).isoformat() + "Z"
    stored_password = generate_password_hash(password) if hashed else password
    cur = db().execute(
        """
        INSERT INTO subscribers(
            tenant_id, username, password, user_type, service_type, full_name,
            mobile, email, plan_id, status, expire_at, balance, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (
            1,
            username,
            stored_password,
            "subscriber",
            "Hotspot",
            "Portal Subscriber",
            "0590002222",
            "portal@example.com",
            plan_id,
            "enabled",
            expire,
            -2.5,
        ),
    )
    return int(cur.lastrowid)


def _login(client, username: str = "subscriber-portal-user", password: str = "portal-pass") -> str:
    res = client.post("/api/v1/subscriber-portal/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()
    assert data["ok"] is True
    return data["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_subscriber_portal_routes_registered(client):
    res = client.get("/api/v1/_routes", headers=AUTH)
    assert res.status_code == 200, res.get_json()
    routes = {item["rule"] for item in res.get_json()["data"]["routes"]}
    assert "/api/v1/subscriber-portal/login" in routes
    assert "/api/v1/subscriber-portal/dashboard" in routes
    assert "/api/v1/subscriber-portal/loan-request" in routes


def test_login_returns_token_without_admin_session_and_sanitizes_passwords(app, client):
    with app.app_context():
        _subscriber(hashed=True)

    res = client.post("/api/v1/subscriber-portal/login", json={"username": "subscriber-portal-user", "password": "portal-pass"})

    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["token"]
    assert data["expires_in"] == 15 * 60
    assert data["subscriber"]["username"] == "subscriber-portal-user"
    assert "password" not in data["subscriber"]
    assert "pppoe_password" not in data["subscriber"]


def test_login_rejects_bad_password(app, client):
    with app.app_context():
        _subscriber()

    res = client.post("/api/v1/subscriber-portal/login", json={"username": "subscriber-portal-user", "password": "bad"})

    assert res.status_code == 401
    assert res.get_json()["error"] == "invalid_credentials"


def test_missing_invalid_expired_and_revoked_tokens_are_rejected(app, client):
    with app.app_context():
        _subscriber()
    token = _login(client)

    missing = client.get("/api/v1/subscriber-portal/me")
    invalid = client.get("/api/v1/subscriber-portal/me", headers=_auth("bad-token"))
    with app.app_context():
        db().execute(
            "UPDATE customer_portal_tokens SET expires_at=?",
            ((datetime.utcnow() - timedelta(seconds=1)).isoformat() + "Z",),
        )
    expired = client.get("/api/v1/subscriber-portal/me", headers=_auth(token))

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert expired.status_code == 401
    assert expired.get_json()["error"] == "token_expired"

    with app.app_context():
        _subscriber("fresh-user")
    fresh_token = _login(client, "fresh-user", "portal-pass")
    logout = client.post("/api/v1/subscriber-portal/logout", headers=_auth(fresh_token))
    revoked = client.get("/api/v1/subscriber-portal/me", headers=_auth(fresh_token))
    assert logout.status_code == 200
    assert revoked.status_code == 401


def test_dashboard_is_self_scoped_and_expired_subscriber_can_view(app, client):
    with app.app_context():
        _subscriber(expired=True)
        _subscriber("other-subscriber")
    token = _login(client)

    res = client.get("/api/v1/subscriber-portal/dashboard", headers=_auth(token))

    assert res.status_code == 200, res.get_json()
    dashboard = res.get_json()["dashboard"]
    assert dashboard["subscriber"]["username"] == "subscriber-portal-user"
    assert dashboard["subscription"]["status"] == "expired"
    assert dashboard["subscription"]["expired_view_allowed"] is True
    assert "password" not in dashboard["subscriber"]
    assert "other-subscriber" not in str(dashboard)


def test_loan_request_creates_current_subscriber_ticket_inbox_and_event(app, client):
    with app.app_context():
        subscriber_id = _subscriber()
    token = _login(client)

    res = client.post(
        "/api/v1/subscriber-portal/loan-request",
        json={"requested_minutes": 1440, "reason": "احتاج يوم إضافي"},
        headers=_auth(token),
    )

    assert res.status_code == 201, res.get_json()
    data = res.get_json()["request"]
    assert data["requester_id"] == subscriber_id
    assert data["request_type"] == "loan"
    assert data["result"]["ticket_id"]
    with app.app_context():
        ticket = db().execute("SELECT * FROM tickets WHERE tenant_id=1 AND subscriber_id=?", (subscriber_id,)).fetchone()
        inbox = db().execute("SELECT * FROM inbox_messages WHERE tenant_id=1 AND subscriber_id=?", (subscriber_id,)).fetchone()
        event = db().execute("SELECT * FROM business_events WHERE tenant_id=1 AND target_id=?", (subscriber_id,)).fetchone()
    assert ticket is not None
    assert inbox is not None
    assert event is not None


def test_invalid_loan_value_returns_arabic_json_error(app, client):
    with app.app_context():
        _subscriber()
    token = _login(client)

    res = client.post(
        "/api/v1/subscriber-portal/loan-request",
        json={"requested_minutes": 0, "reason": ""},
        headers=_auth(token),
    )

    assert res.status_code == 400
    assert res.get_json()["error"] == "validation_error"


def test_requests_are_self_scoped(app, client):
    with app.app_context():
        mine_id = _subscriber()
        other_id = _subscriber("portal-other")
    mine_token = _login(client)
    other_token = _login(client, "portal-other", "portal-pass")

    mine_req = client.post(
        "/api/v1/subscriber-portal/renewal-request",
        json={"reason": "أريد تجديد الاشتراك"},
        headers=_auth(mine_token),
    ).get_json()["request"]
    other_req = client.post(
        "/api/v1/subscriber-portal/renewal-request",
        json={"reason": "طلب آخر"},
        headers=_auth(other_token),
    ).get_json()["request"]

    mine_list = client.get("/api/v1/subscriber-portal/requests", headers=_auth(mine_token))
    cross_detail = client.get(f"/api/v1/subscriber-portal/requests/{other_req['id']}", headers=_auth(mine_token))
    own_detail = client.get(f"/api/v1/subscriber-portal/requests/{mine_req['id']}", headers=_auth(mine_token))

    assert mine_req["requester_id"] == mine_id
    assert other_req["requester_id"] == other_id
    assert [item["id"] for item in mine_list.get_json()["items"]] == [mine_req["id"]]
    assert cross_detail.status_code == 404
    assert own_detail.status_code == 200
