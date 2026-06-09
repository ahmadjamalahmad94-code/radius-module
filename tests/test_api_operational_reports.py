from __future__ import annotations

from core_stabilization_helpers import AUTH, app, client, subscriber, username  # noqa: F401


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


def test_login_states_report_returns_unified_login_events(client):
    from app.radius.db.connection import transaction

    user = username("login_state")
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO audit_log(
                tenant_id, actor, action, target_type, target_id, result_status,
                error_message, payload_json, ip_address, user_agent, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, user, "auth_login_failed", "admin", user, "failed",
                "bad_password", '{"reason":"bad_password"}', "127.0.0.2",
                "pytest", "2026-05-20 09:00:00",
            ),
        )

    res = client.get(
        f"/api/v1/operational-reports/login-states?q={user}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["slug"] == "login-states"
    assert data["items"][0]["username"] == user
    assert data["items"][0]["success"] is False


def test_speed_failures_report_filters_failed_speed_actions(client):
    from app.radius.db.connection import transaction

    user = username("speed_fail")
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO audit_log(
                tenant_id, actor, action, target_type, target_id, result_status,
                error_message, payload_json, ip_address, user_agent, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, "admin", "bulk_set_speeds", "user", user, "failed",
                "router offline", '{"secret":"hidden","speed":"50m"}',
                "127.0.0.1", "pytest", "2026-05-20",
            ),
        )

    res = client.get(
        f"/api/v1/operational-reports/speed-failures?q={user}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    item = res.get_json()["data"]["items"][0]
    assert item["target_id"] == user
    assert item["payload"]["secret"] == "[redacted]"
    assert item["error_message"] == "router offline"


def test_used_cards_report_returns_used_cards_without_password(client):
    from app.radius.db.connection import transaction

    card_user = username("used_card")
    with transaction() as conn:
        batch_id = conn.execute(
            """
            INSERT INTO card_batches(
                tenant_id, batch_code, package_name, plan_id, count, generated, used, created_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (1, username("batch"), "Test Cards", 1, 1, 1, 1, "2026-05-20"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO cards(
                tenant_id, batch_id, username, password, plan_id, used,
                first_used_at, used_by_mac, expire_at, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, batch_id, card_user, "card-password", 1, 1,
                "2026-05-20 10:00:00", "AA:BB:CC", "2026-06-20",
                "2026-05-20",
            ),
        )

    res = client.get(
        f"/api/v1/operational-reports/used-cards?q={card_user}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    item = res.get_json()["data"]["items"][0]
    assert item["username"] == card_user
    assert item["used_by_mac"] == "AA:BB:CC"
    assert "password" not in item
    assert "card-password" not in str(item)


def test_balance_movements_report_reads_accounting_ledger(client):
    from app.radius.db.connection import transaction

    user = username("balance_move")
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO accounting_ledger_entries(
                tenant_id, entry_type, direction, amount, currency, username,
                operator, source_type, status, notes, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, "payment", "credit", 12.5, "JOD", user, "admin",
                "manual", "posted", "test movement", "{}", "2026-05-20",
            ),
        )

    res = client.get(
        f"/api/v1/operational-reports/balance-movements?q={user}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    item = res.get_json()["data"]["items"][0]
    assert item["username"] == user
    assert item["scope"] == "general"
    assert item["amount"] == 12.5


def test_cash_transactions_report_reads_payments(client):
    from app.radius.db.connection import transaction

    sub = subscriber(client, name=username("cash_tx"))
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO payment_transactions(
                tenant_id, subscriber_id, username, amount, currency, method,
                status, plan_price, discount_amount, effective_price,
                earned_minutes, created_by, notes, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                1, sub["id"], sub["username"], 20, "JOD", "cash",
                "posted", 20, 0, 20, 60, "admin", "test payment",
                "{}", "2026-05-20",
            ),
        )

    res = client.get(
        f"/api/v1/operational-reports/cash-transactions?q={sub['username']}",
        headers=AUTH,
    )
    assert res.status_code == 200, res.get_json()
    item = res.get_json()["data"]["items"][0]
    assert item["username"] == sub["username"]
    assert item["amount"] == 20
    assert item["method"] == "cash"


def test_unknown_operational_report_returns_404(client):
    res = client.get("/api/v1/operational-reports/nope", headers=AUTH)
    assert res.status_code == 404
    assert res.get_json()["error"]["code"] == "not_found"


def test_operational_report_rejects_long_query_in_arabic(client):
    res = client.get("/api/v1/operational-reports/sessions?q=" + ("x" * 121), headers=AUTH)
    assert res.status_code == 422
    assert res.get_json()["error"]["message"] == "عبارة البحث طويلة جدًا."
