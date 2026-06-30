"""BUG2 regression — the «الباقات» plans table must be horizontally scrollable.

The plans table (/admin/radius/plans) has 14 columns and overflows narrow
viewports. The unified-data-table wrapper ships `overflow-x:hidden` ("columns
fit or are hidden"), so on a laptop/phone the right/left columns were cut off
with no way to scroll. This test asserts the rendered page carries a
plans-scoped `overflow-x:auto` rule on the table wrapper so all columns are
reachable, without altering the global uds tables.
"""
from __future__ import annotations

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


def _web_login(client) -> None:
    from app.radius.db.repos import admins_repo

    username = f"plans_web_{uuid4().hex[:10]}"
    password = "plans-web-pass"
    admins_repo.create_admin(
        username=username, password=password,
        full_name="Plans Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def test_plans_table_wrapper_is_horizontally_scrollable(client, app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.db.repos import plans_repo

        plans_repo.upsert_plan(
            AccessPlan(
                id=None, tenant_id=1, name="Scroll Plan", code="scroll1",
                duration_minutes=43200, validity_days=30, price=100, enabled=True,
            )
        )

    _web_login(client)
    res = client.get("/admin/radius/plans")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # The table view + uds wrapper are present.
    assert 'class="pl-table-view"' in html
    assert "uds-table-wrap" in html
    # Plans-scoped horizontal-scroll rule is wired into the page.
    assert ".pl-table-view .uds-table-wrap" in html
    assert "overflow-x: auto" in html
    assert "min-width: max-content" in html
