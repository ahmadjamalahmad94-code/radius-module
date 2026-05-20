"""Core Stabilization S4 distributor scope tests."""
from __future__ import annotations

import secrets

from core_stabilization_helpers import AUTH, app, batch, client, configured_plan


def test_distributor_token_sees_only_assigned_batches(client):
    from app.radius.db.repos import admins_repo, api_tokens_repo, operations_repo

    admin = admins_repo.create_admin(
        username="dist_" + secrets.token_hex(4),
        password="pw123456",
        full_name="Scoped Distributor",
        enabled=True,
    )
    record, plain = api_tokens_repo.create_token(
        tenant_id=1,
        name="dist-token",
        scopes=["cards.view"],
        created_by=admin.id,
    )
    distributor = operations_repo.create_distributor(
        1,
        {
            "admin_id": admin.id,
            "name": "dist_" + secrets.token_hex(4),
            "permissions": ["cards.view"],
            "scope": {"card_batches": "assigned"},
        },
        actor="test",
    )
    batch_a = batch(client, "da" + secrets.token_hex(2))
    batch_b = batch(client, "db" + secrets.token_hex(2))
    operations_repo.assign_batch(
        1,
        distributor_id=distributor["id"],
        batch_id=batch_a["id"],
        actor="test",
    )

    scoped_auth = {"Authorization": "Bearer " + plain}
    listed = client.get("/api/v1/cards/batches", headers=scoped_auth)
    assert listed.status_code == 200, listed.get_json()
    assert [item["id"] for item in listed.get_json()["data"]["items"]] == [batch_a["id"]]

    denied = client.get(f"/api/v1/cards/batches/{batch_b['id']}", headers=scoped_auth)
    assert denied.status_code == 403
    assert denied.get_json()["error"]["code"] == "forbidden"
    assert record["created_by"] == admin.id
