"""npc_snapshot_service — operator-facing facade over the
snapshot repo.

No router contact. No MikroTik adapter calls. This service
accepts pre-collected payloads from a future read-only
collector and persists them with secret rejection.

The brief explicitly says: "Reject secret-like content. Tenant
scoped. Only store provided snapshot payloads for future use."
We honor both rules here and re-prove them via tests in
test_npc_h_snapshots.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..db.repos import npc_snapshots_repo as repo


@dataclass(frozen=True)
class StoredSnapshot:
    id: int
    tenant_id: int
    router_id: int
    policy_id: Optional[int]
    policy_type: str
    snapshot_type: str
    status: str
    item_count: int
    created_by: str
    created_at: str
    expires_at: str
    notes: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id":             self.id,
            "tenant_id":      self.tenant_id,
            "router_id":      self.router_id,
            "policy_id":      self.policy_id,
            "policy_type":    self.policy_type,
            "snapshot_type":  self.snapshot_type,
            "status":         self.status,
            "item_count":     self.item_count,
            "created_by":     self.created_by,
            "created_at":     self.created_at,
            "expires_at":     self.expires_at,
            "notes":          self.notes,
        }


def store(
    *,
    tenant_id: int, router_id: int,
    snapshot_type: str,
    items: Iterable[dict],
    policy_id: Optional[int] = None,
    policy_type: str = "",
    created_by: str = "",
    expires_at: str = "",
    notes: str = "",
) -> StoredSnapshot:
    """Persist a snapshot header + its items in a single call.

    Each item is a dict with keys understood by
    `npc_snapshots_repo.add_item`. Items that fail secret
    rejection or enum validation are silently rejected; the
    summary returns the inserted vs rejected counts via the
    `item_count` field.

    No MikroTik contact happens — `items` must be supplied by
    the caller from whatever read-only source they came from.
    """
    sid = repo.create_snapshot(
        tenant_id=tenant_id, router_id=router_id,
        snapshot_type=snapshot_type,
        policy_id=policy_id, policy_type=policy_type,
        created_by=created_by,
        expires_at=expires_at, notes=notes,
        status=repo.STATUS_STORED,
    )
    counts = repo.add_items_many(sid, items)
    header = repo.get_snapshot(tenant_id, sid)
    if header is None:
        raise RuntimeError("snapshot row vanished after insert")
    return StoredSnapshot(
        id=int(header["id"]),
        tenant_id=int(header["tenant_id"]),
        router_id=int(header["router_id"]),
        policy_id=(int(header["policy_id"])
                    if header["policy_id"] is not None else None),
        policy_type=str(header["policy_type"] or ""),
        snapshot_type=str(header["snapshot_type"]),
        status=str(header["status"]),
        item_count=int(counts["inserted"]),
        created_by=str(header["created_by"] or ""),
        created_at=str(header["created_at"]),
        expires_at=str(header["expires_at"] or ""),
        notes=str(header["notes"] or ""),
    )


def list_for_router(
    tenant_id: int, router_id: int, *, limit: int = 50,
) -> list[dict]:
    return repo.list_for_router(
        tenant_id, router_id, limit=limit,
    )


def list_for_policy(
    tenant_id: int, *, policy_type: str,
    policy_id: int, limit: int = 50,
) -> list[dict]:
    return repo.list_for_policy(
        tenant_id, policy_type=policy_type,
        policy_id=policy_id, limit=limit,
    )


def get(
    tenant_id: int, snapshot_id: int, *,
    include_items: bool = False,
) -> Optional[dict]:
    header = repo.get_snapshot(tenant_id, snapshot_id)
    if header is None:
        return None
    out = dict(header)
    if include_items:
        out["items"] = repo.list_items(snapshot_id)
    return out


def delete(tenant_id: int, snapshot_id: int) -> bool:
    return repo.delete_snapshot(tenant_id, snapshot_id)


__all__ = [
    "StoredSnapshot",
    "store", "list_for_router", "list_for_policy",
    "get", "delete",
]
