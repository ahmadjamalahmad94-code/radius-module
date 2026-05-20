"""Core Stabilization S2 soft-delete regression tests."""
from __future__ import annotations

from core_stabilization_helpers import AUTH, app, client, configured_plan, subscriber


def test_sensitive_deletes_archive_and_recycle_bin_can_restore_subscriber(client):
    item = subscriber(client)
    deleted = client.delete(f"/api/v1/accounts/{item['username']}", headers=AUTH)
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["data"]["archived"] is True

    hidden = client.get(f"/api/v1/accounts/{item['username']}", headers=AUTH)
    assert hidden.status_code == 404

    listed = client.get(
        "/api/v1/recycle-bin",
        query_string={"entity_type": "subscribers"},
        headers=AUTH,
    )
    assert listed.status_code == 200, listed.get_json()
    items = listed.get_json()["data"]["items"]
    archived = next(row for row in items if row["id"] == item["id"])
    assert archived["label"] == item["username"]

    restored = client.post(
        f"/api/v1/recycle-bin/subscribers/{item['id']}/restore",
        json={},
        headers=AUTH,
    )
    assert restored.status_code == 200, restored.get_json()
    visible = client.get(f"/api/v1/accounts/{item['username']}", headers=AUTH)
    assert visible.status_code == 200
