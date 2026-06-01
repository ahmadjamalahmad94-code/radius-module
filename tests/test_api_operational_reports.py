from __future__ import annotations

from core_stabilization_helpers import AUTH, app, client, username  # noqa: F401


def test_operational_reports_require_auth(client):
    res = client.get("/api/v1/operational-reports/sessions")
    assert res.status_code == 401


def test_sessions_report_returns_radacct_rows(client):
    from app.radius.db.connection import transaction

    user = username("op_user")
    session = username("op_session")
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO radacct(
                tenant_id, acctsessionid, username, nasipaddress, acctstarttime,
                acctstoptime, acctsessiontime, acctinputoctets, acctoutputoctets,
                callingstationid, framedipaddress
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, session, user, "10.0.0.1", "2026-05-20 08:00:00",
                None, 120, 1024, 2048, "AA:BB:CC:DD:EE:FF", "10.20.30.40",
            ),
        )

    res = client.get(
        f"/api/v1/operational-reports/sessions?q={user}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["slug"] == "sessions"
    assert data["count"] == 1
    assert data["items"][0]["username"] == user
    assert data["items"][0]["acctsessionid"] == session


def test_failed_login_report_does_not_expose_password(client):
    from app.radius.db.connection import transaction

    user = username("bad_user")
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas)
            VALUES(?,?,?,?,?,?,?)
            """,
            (1, user, "super-secret", "Access-Reject", "2026-05-20", "", "nas-a"),
        )

    res = client.get(
        f"/api/v1/operational-reports/failed-logins?q={user}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    item = res.get_json()["data"]["items"][0]
    assert item["username"] == user
    assert "pass" not in item
    assert "super-secret" not in str(item)


def test_audit_reports_redact_sensitive_payload(client):
    from app.radius.db.connection import transaction

    user = username("op_user")
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO audit_log(
                tenant_id, actor, action, target_type, target_id, payload_json,
                ip_address, user_agent, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                1, "admin", "update", "user", user,
                '{"password":"secret","safe":"shown"}', "127.0.0.1", "pytest",
                "2026-05-20",
            ),
        )

    res = client.get(
        f"/api/v1/operational-reports/profile-changes?q={user}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    payload = res.get_json()["data"]["items"][0]["payload"]
    assert payload["password"] == "[redacted]"
    assert payload["safe"] == "shown"


def test_unknown_operational_report_returns_404(client):
    res = client.get("/api/v1/operational-reports/nope", headers=AUTH)
    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "not_found"


def test_operational_report_rejects_long_query_in_arabic(client):
    res = client.get("/api/v1/operational-reports/sessions?q=" + ("x" * 121), headers=AUTH)
    assert res.status_code == 422
    assert res.get_json()["error"]["message"] == "عبارة البحث طويلة جدًا."
