"""Lifecycle retention engine tests.

The feature archives expired cards/subscribers into the recycle bin. It must
never hard-delete records and must not reduce the original card-batch count.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_lifecycle_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]
    from app import create_app
    yield create_app()
    for name in list(sys.modules):
        if name.startswith("app."):
            del sys.modules[name]


@pytest.fixture
def client(app):
    return app.test_client()


def _iso(delta_days: int = 0) -> str:
    return (datetime.utcnow() + timedelta(days=delta_days)).replace(microsecond=0).isoformat() + "Z"


def _seed_plan_batch_cards():
    from app.radius.db.connection import transaction

    now = _iso()
    with transaction() as conn:
        conn.execute(
            "INSERT INTO access_plans(tenant_id, name, enabled, created_at) VALUES(1, 'Lifecycle', 1, ?)",
            (now,),
        )
        plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO card_batches(
                tenant_id, batch_code, package_name, plan_id, count, generated, used,
                created_by, status, created_at, metadata, original_count, settlement_count
            )
            VALUES(1, 'LC-B1', 'Lifecycle batch', ?, 2, 2, 0, 'test', 'active', ?, '{}', 2, 2)
            """,
            (plan_id, now),
        )
        batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, expire_at, created_at)
            VALUES(1, ?, 'lc-expired', 'secret', ?, ?, ?)
            """,
            (batch_id, plan_id, _iso(-5), now),
        )
        expired_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, expire_at, created_at)
            VALUES(1, ?, 'lc-future', 'secret', ?, ?, ?)
            """,
            (batch_id, plan_id, _iso(5), now),
        )
    return plan_id, batch_id, expired_id


def test_preview_is_dry_run_and_archive_preserves_batch_original_count(client):
    _, batch_id, expired_id = _seed_plan_batch_cards()

    policy = client.post(
        "/api/v1/lifecycle/policies",
        json={
            "entity_type": "card",
            "trigger_type": "expired_at",
            "delay_value": 2,
            "delay_unit": "days",
            "retention_value": 90,
            "retention_unit": "days",
            "enabled": True,
        },
        headers=AUTH,
    )
    assert policy.status_code == 201, policy.get_json()

    preview = client.post("/api/v1/lifecycle/preview", json={}, headers=AUTH)
    assert preview.status_code == 200, preview.get_json()
    totals = preview.get_json()["data"]["totals"]
    assert totals["cards"] == 1
    assert totals["pending_archive"] == 1

    from app.radius.db.connection import db
    before = db().execute("SELECT deleted_at FROM cards WHERE id = ?", (expired_id,)).fetchone()
    assert before["deleted_at"] is None

    run = client.post("/api/v1/lifecycle/run", json={}, headers=AUTH)
    assert run.status_code == 200, run.get_json()
    assert run.get_json()["data"]["changed"] == 1

    row = db().execute(
        "SELECT deleted_at, archive_source, archive_policy_id, retention_expires_at FROM cards WHERE id = ?",
        (expired_id,),
    ).fetchone()
    assert row["deleted_at"]
    assert row["archive_source"] == "auto"
    assert row["archive_policy_id"]
    assert row["retention_expires_at"]

    from app.radius.db.repos import cards_repo
    summary = cards_repo.batch_operational_summary(1, batch_id)
    assert summary["original_count"] == 2
    assert summary["total_cards"] == 2
    assert summary["archived_count"] == 1
    assert summary["operational_remaining_count"] == 1


def test_subscriber_policy_archives_to_recycle_bin_with_retention_metadata(client):
    from app.radius.db.connection import transaction

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO subscribers(tenant_id, username, password, expire_at, status, created_at)
            VALUES(1, 'lc-sub', 'pw', ?, 'expired', ?)
            """,
            (_iso(-10), _iso()),
        )
        sub_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    policy = client.post(
        "/api/v1/lifecycle/policies",
        json={
            "entity_type": "subscriber",
            "trigger_type": "expired_at",
            "delay_value": 1,
            "delay_unit": "days",
            "retention_value": 3,
            "retention_unit": "months",
            "enabled": True,
        },
        headers=AUTH,
    )
    assert policy.status_code == 201, policy.get_json()

    run = client.post("/api/v1/lifecycle/run", json={}, headers=AUTH)
    assert run.status_code == 200, run.get_json()
    assert run.get_json()["data"]["changed"] == 1

    listed = client.get("/api/v1/recycle-bin", query_string={"entity_type": "subscribers"}, headers=AUTH)
    assert listed.status_code == 200, listed.get_json()
    archived = next(item for item in listed.get_json()["data"]["items"] if item["id"] == sub_id)
    assert archived["archive_source"] == "auto"
    assert archived["archive_policy_id"]
    assert archived["retention_expires_at"]
    assert archived["restore_allowed"] is True


def test_external_file_policy_is_saved_but_not_executed_as_radius_action(client):
    created = client.post(
        "/api/v1/lifecycle/policies",
        json={
            "entity_type": "external_file",
            "trigger_type": "expired_at",
            "delay_value": 1,
            "delay_unit": "days",
            "retention_value": 90,
            "retention_unit": "days",
            "enabled": True,
        },
        headers=AUTH,
    )
    assert created.status_code == 201, created.get_json()

    preview = client.post("/api/v1/lifecycle/preview", json={}, headers=AUTH)
    policy_preview = preview.get_json()["data"]["policies"][0]
    assert policy_preview["supported"] is False
    assert policy_preview["cards_count"] == 0

    run = client.post("/api/v1/lifecycle/run", json={}, headers=AUTH)
    assert run.status_code == 200, run.get_json()
    assert run.get_json()["data"]["changed"] == 0
    assert run.get_json()["data"]["skipped"] >= 1


def test_lifecycle_web_page_is_protected_and_renders(client):
    unauth = client.get("/admin/radius/lifecycle")
    assert unauth.status_code in {302, 303}

    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "admin"
        sess["tenant_id"] = 1
        # lifecycle (data-retention settings) is now RBAC-gated (settings.view);
        # this synthetic session represents the owner → mark it super.
        sess["is_super_admin"] = True
    res = client.get("/admin/radius/lifecycle")
    assert res.status_code == 200
    assert "الأرشفة التلقائية".encode("utf-8") in res.data
