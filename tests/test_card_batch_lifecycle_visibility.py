"""R1 foundation tests for card-batch lifecycle, visibility, and checker UI."""
from __future__ import annotations

import secrets

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
    assert res.status_code == 200, res.get_json()
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


def _prefix() -> str:
    return "r1" + secrets.token_hex(4)


def _generate(client, auth_headers, *, count: int = 1, **overrides):
    body = {
        "plan_id": 1,
        "count": count,
        "username_prefix": _prefix(),
        "password_length": 12,
    }
    body.update(overrides)
    res = client.post("/api/v1/cards/generate", json=body, headers=auth_headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["data"]


def _web_login(client) -> None:
    res = client.post(
        "/admin/radius/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def test_card_batch_lifecycle_columns_are_additive(app):
    from app.radius.db.connection import db

    columns = {
        row["name"]
        for row in db().execute("PRAGMA table_info(card_batches)").fetchall()
    }
    assert {"deleted_at", "deleted_by", "delete_reason"}.issubset(columns)


def test_archiving_batch_does_not_hide_or_delete_cards(client, auth_headers):
    from app.radius.db.repos import cards_repo

    data = _generate(client, auth_headers, count=2)
    batch_id = data["batch"]["id"]

    assert cards_repo.archive_batch(1, batch_id, actor="qa", reason="operator cleanup")
    assert cards_repo.archive_batch(1, batch_id, actor="qa", reason="again") is False

    batch = cards_repo.get_batch(1, batch_id)
    cards = cards_repo.list_cards(1, batch_id=batch_id)
    assert batch is not None
    assert batch.deleted_at is not None
    assert batch.deleted_by == "qa"
    assert batch.delete_reason == "operator cleanup"
    assert batch.status == "deleted"
    assert len(cards) == 2


def test_batch_summary_counts_available_active_expired_and_revoked(client, auth_headers):
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso
    from app.radius.db.repos import cards_repo

    data = _generate(client, auth_headers, count=4)
    batch_id = data["batch"]["id"]
    cards = cards_repo.list_cards(1, batch_id=batch_id, limit=10)
    assert len(cards) == 4

    with transaction() as conn:
        conn.execute(
            "UPDATE cards SET used = 1, first_used_at = ? WHERE id = ?",
            (now_iso(), cards[0].id),
        )
        conn.execute("UPDATE cards SET expire_at = ? WHERE id = ?", ("2000-01-01T00:00:00Z", cards[1].id))
        conn.execute("UPDATE cards SET revoked = 1 WHERE id = ?", (cards[2].id,))

    res = client.get(f"/api/v1/cards/batches/{batch_id}/summary", headers=auth_headers)
    assert res.status_code == 200, res.get_json()
    summary = res.get_json()["data"]["summary"]
    assert summary["total_cards"] == 4
    assert summary["active_count"] == 1
    assert summary["expired_count"] == 1
    assert summary["revoked_count"] == 1
    assert summary["available_count"] == 1
    assert summary["remaining_count"] == 1
    assert "password" not in summary


def test_card_checker_ui_route_and_result_never_expose_password(client, auth_headers):
    data = _generate(client, auth_headers, count=1)
    card = data["cards"][0]

    _web_login(client)
    res = client.get(
        "/admin/radius/cards/checker",
        query_string={"query": card["username"]},
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert card["username"] in html
    assert "فحص بطاقة" in html
    assert "الدفعة" in html
    assert "الباقة" in html
    assert "كلمة مرور" in html
    assert card["password"] not in html


def test_card_checker_ui_empty_and_long_query_are_safe(client):
    _web_login(client)
    empty = client.get("/admin/radius/cards/checker")
    assert empty.status_code == 200
    assert "أدخل رقم بطاقة" in empty.get_data(as_text=True)

    long_query = client.get(
        "/admin/radius/cards/checker",
        query_string={"query": "x" * 129},
    )
    assert long_query.status_code == 200
    assert "لا يتجاوز 128" in long_query.get_data(as_text=True)


def test_roadmap_audit_payload_shape_is_stable():
    from app.radius.core.constants import AUDIT_ACTION_LOAN_GRANT
    from app.radius.services.audit_events import roadmap_audit_payload

    payload = roadmap_audit_payload(
        domain="loans",
        action=AUDIT_ACTION_LOAN_GRANT,
        reason="temporary activation",
        metadata={"hours": 3},
    )
    assert payload == {
        "schema": "customer-roadmap.audit.v1",
        "domain": "loans",
        "action": "loan_grant",
        "reason": "temporary activation",
        "metadata": {"hours": 3},
    }
