"""npc_snapshots_repo — persist NPC router-state snapshots.

Phase H of the NPC roadmap. The repo is intentionally narrow:
no router contact, no MikroTik adapter calls. Callers (future
read-only collectors) hand in pre-collected payloads and we
store them with secret rejection.

Tables:
  network_policy_snapshots          — header
  network_policy_snapshot_items     — items
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from ..connection import db, transaction


SNAPSHOT_TYPE_FILTER       = "firewall_filter"
SNAPSHOT_TYPE_ADDRESS_LIST = "address_list"
SNAPSHOT_TYPE_WALLED       = "walled_garden"
SNAPSHOT_TYPE_SCHEDULER    = "scheduler"
SNAPSHOT_TYPE_COMPOSITE    = "composite"

ALLOWED_SNAPSHOT_TYPES = frozenset({
    SNAPSHOT_TYPE_FILTER, SNAPSHOT_TYPE_ADDRESS_LIST,
    SNAPSHOT_TYPE_WALLED, SNAPSHOT_TYPE_SCHEDULER,
    SNAPSHOT_TYPE_COMPOSITE,
})

STATUS_PENDING = "pending"
STATUS_STORED  = "stored"
STATUS_EXPIRED = "expired"
STATUS_FAILED  = "failed"

ALLOWED_STATUSES = frozenset({
    STATUS_PENDING, STATUS_STORED,
    STATUS_EXPIRED, STATUS_FAILED,
})


ITEM_FILTER  = "firewall_filter_rule"
ITEM_ADDR    = "address_list_entry"
ITEM_WG_HOST = "walled_garden_host"
ITEM_WG_IP   = "walled_garden_ip"
ITEM_SCHED   = "scheduler_entry"

ALLOWED_ITEM_KINDS = frozenset({
    ITEM_FILTER, ITEM_ADDR, ITEM_WG_HOST,
    ITEM_WG_IP, ITEM_SCHED,
})


class SecretInSnapshotError(ValueError):
    """Snapshot payload contains a substring that smells like a
    secret. Reject loudly — same defence-in-depth posture as
    the NPC scripts repo."""


# Substrings the renderer + scripts repo enforce on script
# bodies. Snapshots are JSON dicts not script text, so we also
# scan dict KEYS against a forbidden-key list below.
_SECRET_SUBSTRINGS = (
    "private-key=", "PrivateKey =", "private_key=",
    "BEGIN PRIVATE KEY",
    "password=", "Password =",
)


# Forbidden dict keys (case-insensitive exact match OR
# contains). Catches structured payloads like
# {"password": "..."} that JSON-serialise without an `=`.
_FORBIDDEN_KEY_PARTS = (
    "password", "private_key", "private-key", "privatekey",
    "secret", "api_password", "api-password",
)


def _key_looks_secret(key: str) -> bool:
    k = (key or "").strip().lower()
    return any(p in k for p in _FORBIDDEN_KEY_PARTS)


def _walk_for_secret_keys(blob: Any) -> Optional[str]:
    """Walk dict / list structures looking for forbidden keys.
    Returns the offending key name on first hit, None
    otherwise."""
    if isinstance(blob, dict):
        for k, v in blob.items():
            if _key_looks_secret(str(k)):
                return str(k)
            nested = _walk_for_secret_keys(v)
            if nested is not None:
                return nested
    elif isinstance(blob, (list, tuple)):
        for v in blob:
            nested = _walk_for_secret_keys(v)
            if nested is not None:
                return nested
    return None


def _assert_no_secrets(blob: Any) -> None:
    """Reject string-form RouterOS-shaped tripwires AND
    structured dict keys that look like secrets."""
    if blob in (None, "", {}, []):
        return
    # 1) Structured scan: forbidden dict keys anywhere in the
    # nested payload.
    bad = _walk_for_secret_keys(blob)
    if bad is not None:
        raise SecretInSnapshotError(
            f"refusing to store snapshot item — forbidden key "
            f"{bad!r} detected. Snapshots must never carry "
            "secrets."
        )
    # 2) Substring scan: catch incidental MikroTik-shaped
    # `password=` etc. in serialised text or display strings.
    text = blob if isinstance(blob, str) else json.dumps(
        blob, default=str, ensure_ascii=False,
    )
    for tw in _SECRET_SUBSTRINGS:
        if tw in text:
            raise SecretInSnapshotError(
                f"refusing to store snapshot item — tripwire "
                f"{tw!r} detected."
            )


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def create_snapshot(
    *, tenant_id: int, router_id: int,
    snapshot_type: str,
    policy_id: Optional[int] = None,
    policy_type: str = "",
    created_by: str = "",
    expires_at: str = "",
    notes: str = "",
    status: str = STATUS_STORED,
) -> int:
    if snapshot_type not in ALLOWED_SNAPSHOT_TYPES:
        raise ValueError(
            f"invalid snapshot_type: {snapshot_type!r}"
        )
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO network_policy_snapshots
                (tenant_id, router_id, policy_id, policy_type,
                 snapshot_type, status, created_by, created_at,
                 expires_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id), int(router_id),
                int(policy_id) if policy_id is not None else None,
                (policy_type or "")[:60],
                snapshot_type, status,
                (created_by or "")[:120],
                now,
                (expires_at or "")[:40],
                (notes or "")[:500],
            ),
        )
        return int(cur.lastrowid)


def add_item(
    *, snapshot_id: int, item_kind: str,
    source_id: str = "",
    payload: Optional[dict] = None,
    display_text: str = "",
    position: int = 0,
) -> int:
    if item_kind not in ALLOWED_ITEM_KINDS:
        raise ValueError(f"invalid item_kind: {item_kind!r}")
    payload = payload or {}
    # Secret rejection — both the structured payload AND any
    # incidental display text.
    _assert_no_secrets(payload)
    _assert_no_secrets(display_text)
    payload_json = json.dumps(payload, ensure_ascii=False)
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO network_policy_snapshot_items
                (snapshot_id, item_kind, source_id,
                 payload_json, display_text, position,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(snapshot_id), item_kind,
                (source_id or "")[:80],
                payload_json,
                (display_text or "")[:500],
                int(position), now,
            ),
        )
        return int(cur.lastrowid)


def add_items_many(
    snapshot_id: int, items: Iterable[dict],
) -> dict[str, int]:
    inserted = rejected = 0
    for it in items:
        try:
            add_item(
                snapshot_id=int(snapshot_id),
                item_kind=str(it.get("item_kind") or ""),
                source_id=str(it.get("source_id") or ""),
                payload=it.get("payload") or {},
                display_text=str(it.get("display_text") or ""),
                position=int(it.get("position") or 0),
            )
            inserted += 1
        except (ValueError, TypeError, SecretInSnapshotError):
            rejected += 1
    return {"inserted": inserted, "rejected": rejected}


def get_snapshot(
    tenant_id: int, snapshot_id: int,
) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM network_policy_snapshots "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(snapshot_id)),
    ).fetchone()
    return dict(row) if row else None


def list_for_router(
    tenant_id: int, router_id: int, *, limit: int = 50,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM network_policy_snapshots "
        "WHERE tenant_id=? AND router_id=? "
        "ORDER BY id DESC LIMIT ?",
        (int(tenant_id), int(router_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def list_for_policy(
    tenant_id: int, *,
    policy_type: str, policy_id: int,
    limit: int = 50,
) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM network_policy_snapshots "
        "WHERE tenant_id=? AND policy_type=? AND policy_id=? "
        "ORDER BY id DESC LIMIT ?",
        (int(tenant_id), str(policy_type),
         int(policy_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def list_items(
    snapshot_id: int, *,
    item_kind: Optional[str] = None,
) -> list[dict]:
    if item_kind is not None and item_kind not in ALLOWED_ITEM_KINDS:
        raise ValueError(f"invalid item_kind: {item_kind!r}")
    if item_kind:
        rows = db().execute(
            "SELECT * FROM network_policy_snapshot_items "
            "WHERE snapshot_id=? AND item_kind=? "
            "ORDER BY position, id",
            (int(snapshot_id), item_kind),
        ).fetchall()
    else:
        rows = db().execute(
            "SELECT * FROM network_policy_snapshot_items "
            "WHERE snapshot_id=? ORDER BY position, id",
            (int(snapshot_id),),
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload_json") or "{}")
        except (TypeError, ValueError):
            d["payload"] = {}
        out.append(d)
    return out


def set_status(
    tenant_id: int, snapshot_id: int, status: str,
) -> Optional[dict]:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    with transaction() as c:
        c.execute(
            "UPDATE network_policy_snapshots SET status=? "
            "WHERE tenant_id=? AND id=?",
            (status, int(tenant_id), int(snapshot_id)),
        )
    return get_snapshot(tenant_id, snapshot_id)


def delete_snapshot(
    tenant_id: int, snapshot_id: int,
) -> bool:
    with transaction() as c:
        c.execute(
            "DELETE FROM network_policy_snapshot_items "
            "WHERE snapshot_id=?",
            (int(snapshot_id),),
        )
        cur = c.execute(
            "DELETE FROM network_policy_snapshots "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(snapshot_id)),
        )
        return cur.rowcount > 0


__all__ = [
    "SNAPSHOT_TYPE_FILTER", "SNAPSHOT_TYPE_ADDRESS_LIST",
    "SNAPSHOT_TYPE_WALLED", "SNAPSHOT_TYPE_SCHEDULER",
    "SNAPSHOT_TYPE_COMPOSITE", "ALLOWED_SNAPSHOT_TYPES",
    "STATUS_PENDING", "STATUS_STORED", "STATUS_EXPIRED",
    "STATUS_FAILED", "ALLOWED_STATUSES",
    "ITEM_FILTER", "ITEM_ADDR", "ITEM_WG_HOST",
    "ITEM_WG_IP", "ITEM_SCHED", "ALLOWED_ITEM_KINDS",
    "SecretInSnapshotError",
    "create_snapshot", "add_item", "add_items_many",
    "get_snapshot", "list_for_router", "list_for_policy",
    "list_items",
    "set_status", "delete_snapshot",
]
