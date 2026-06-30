"""BUG1 regression — subscriber KPI stat cards must count the WHOLE table.

The «المشتركون» page (/admin/radius/subscribers) showed نشطون/منتهون/معطلون/
«في النتائج» computed from the *loaded page* (limit=1000 list) rather than a
DB-level aggregate. With more subscribers than would surface in the loaded set
the cards under-counted. This test seeds a mixed-status population and proves:

  • subscribers_status_counts() returns a true GROUP-BY-status aggregate.
  • the rendered page shows the real totals (not the page length).
"""
from __future__ import annotations

import secrets
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "dev-token-please-change")
    from app import create_app

    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _tenant(app):
    from app.radius.db.connection import transaction

    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tenants(id, name, slug, created_at) "
            "VALUES (1, 'Default Tenant', 'default', '2026-01-01T00:00:00Z')"
        )


def _seed(status: str, *, user_type: str = "subscriber") -> None:
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo

    subscribers_repo.upsert_subscriber(
        Subscriber(
            id=None,
            tenant_id=1,
            username="kpi_" + secrets.token_hex(6),
            password="pw1234",
            status=status,
            user_type=user_type,
        )
    )


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"kpi_web_{uuid4().hex[:10]}"
    password = "kpi-web-pass"
    admins_repo.create_admin(
        username=username, password=password,
        full_name="KPI Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def test_status_counts_is_full_table_aggregate(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        base = subscribers_repo.subscribers_status_counts(1, user_type="subscriber")
        b_enabled = base["by_status"].get("enabled", 0)
        b_expired = base["by_status"].get("expired", 0)
        b_disabled = base["by_status"].get("disabled", 0)

        for _ in range(12):
            _seed("enabled")
        for _ in range(5):
            _seed("expired")
        for _ in range(3):
            _seed("disabled")
        # card-mirror rows must NOT be counted on the subscribers page.
        for _ in range(7):
            _seed("enabled", user_type="card")

        counts = subscribers_repo.subscribers_status_counts(1, user_type="subscriber")
        assert counts["by_status"].get("enabled", 0) == b_enabled + 12
        assert counts["by_status"].get("expired", 0) == b_expired + 5
        assert counts["by_status"].get("disabled", 0) == b_disabled + 3
        # total == sum of all buckets, and excludes the 7 card rows.
        assert counts["total"] == sum(counts["by_status"].values())
        assert counts["total"] == base["total"] + 20


def test_subscribers_page_cards_reflect_real_totals(client, app):
    _web_login(client)
    with app.app_context():
        for _ in range(15):
            _seed("enabled")
        for _ in range(4):
            _seed("expired")
        for _ in range(2):
            _seed("disabled")
        from app.radius.db.repos import subscribers_repo

        counts = subscribers_repo.subscribers_status_counts(1, user_type="subscriber")

    res = client.get("/admin/radius/subscribers")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # The «في النتائج» total card must show the real aggregate total.
    assert str(counts["total"]) in html
    assert counts["by_status"].get("enabled", 0) >= 15
