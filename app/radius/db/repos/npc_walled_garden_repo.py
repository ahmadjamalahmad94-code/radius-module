"""npc_walled_garden_repo — Hotspot walled-garden allowlist.

A walled-garden _adds_ destinations to the captive portal's
pre-auth allowlist (think: payment gateway, SMS-OTP provider,
support chat) so clients can reach them without logging in.

Two tables:
  - npc_walled_garden_policies — header per (router, hotspot
    profile, name).
  - npc_walled_garden_entries  — individual allowlist rows.
    Entry type maps to MikroTik's /ip/hotspot/walled-garden vs
    /ip/hotspot/walled-garden/ip split.

Entries dedup on (policy_id, entry_type, normalized_value) —
the same host/IP can legitimately appear with different entry
types (e.g. `dst_host` AND `dst_address` for belt-and-braces
allowlisting) so we key on the triple.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from ..connection import db, transaction
from .npc_common import now_iso, slugify


# ─── Policy header ───────────────────────────────────────────


_ALLOWED_POLICY_UPDATE_FIELDS = frozenset({
    "name", "slug", "hotspot_profile", "enabled",
})

_POLICY_BOOL_FIELDS = frozenset({"enabled"})


def _pack_bool(v: Any) -> int:
    if v is True or v == 1 or v == "1":
        return 1
    return 0


def create_policy(
    *, tenant_id: int, router_id: int,
    name: str,
    slug: Optional[str] = None,
    hotspot_profile: str = "",
    enabled: bool = True,
) -> int:
    final_slug = slugify(slug or name)
    if not final_slug:
        raise ValueError(
            "walled-garden policy slug cannot be empty"
        )
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_walled_garden_policies
                (tenant_id, router_id, hotspot_profile,
                 name, slug, enabled,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id), int(router_id),
                (hotspot_profile or "")[:80],
                name[:120], final_slug,
                _pack_bool(enabled),
                now, now,
            ),
        )
        return int(cur.lastrowid)


def get_policy(
    tenant_id: int, policy_id: int,
) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_walled_garden_policies "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(policy_id)),
    ).fetchone()
    return dict(row) if row else None


def get_policy_by_slug(
    tenant_id: int, slug: str,
) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_walled_garden_policies "
        "WHERE tenant_id=? AND slug=?",
        (int(tenant_id), str(slug)),
    ).fetchone()
    return dict(row) if row else None


def list_policies_for_router(
    tenant_id: int, router_id: int, *,
    only_enabled: bool = False,
) -> list[dict]:
    sql = ["SELECT * FROM npc_walled_garden_policies "
           "WHERE tenant_id=? AND router_id=?"]
    params: list[Any] = [int(tenant_id), int(router_id)]
    if only_enabled:
        sql.append("AND enabled=1")
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def list_policies_for_tenant(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM npc_walled_garden_policies "
        "WHERE tenant_id=? ORDER BY id",
        (int(tenant_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def update_policy(
    tenant_id: int, policy_id: int, **changes,
) -> Optional[dict]:
    payload: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in _ALLOWED_POLICY_UPDATE_FIELDS:
            continue
        if k in _POLICY_BOOL_FIELDS:
            payload[k] = _pack_bool(v)
        elif k == "slug":
            s = slugify(str(v))
            if not s:
                raise ValueError("slug cannot be empty")
            payload[k] = s
        else:
            payload[k] = (str(v) or "")[:120]
    if not payload:
        return get_policy(tenant_id, policy_id)
    fields = ", ".join(f"{k}=?" for k in payload.keys())
    params: list[Any] = list(payload.values())
    params.extend([now_iso(), int(tenant_id), int(policy_id)])
    with transaction() as c:
        c.execute(
            f"UPDATE npc_walled_garden_policies "
            f"SET {fields}, updated_at=? "
            "WHERE tenant_id=? AND id=?",
            tuple(params),
        )
    return get_policy(tenant_id, policy_id)


def delete_policy(tenant_id: int, policy_id: int) -> bool:
    with transaction() as c:
        c.execute(
            "DELETE FROM npc_walled_garden_entries "
            "WHERE policy_id=?",
            (int(policy_id),),
        )
        c.execute(
            "DELETE FROM npc_script_versions "
            "WHERE service='walled_garden' AND policy_id=?",
            (int(policy_id),),
        )
        c.execute(
            "DELETE FROM npc_deployments "
            "WHERE service='walled_garden' AND policy_id=?",
            (int(policy_id),),
        )
        cur = c.execute(
            "DELETE FROM npc_walled_garden_policies "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(policy_id)),
        )
        return cur.rowcount > 0


# ─── Entries ─────────────────────────────────────────────────


ENTRY_TYPE_DST_HOST         = "dst_host"
ENTRY_TYPE_DST_ADDRESS      = "dst_address"
ENTRY_TYPE_DST_ADDRESS_LIST = "dst_address_list"

ALLOWED_ENTRY_TYPES = frozenset({
    ENTRY_TYPE_DST_HOST,
    ENTRY_TYPE_DST_ADDRESS,
    ENTRY_TYPE_DST_ADDRESS_LIST,
})

STATUS_ACTIVE        = "active"
STATUS_DISABLED      = "disabled"
STATUS_INVALID       = "invalid"
STATUS_MANUAL_REVIEW = "manual_review"

ALLOWED_STATUSES = frozenset({
    STATUS_ACTIVE, STATUS_DISABLED,
    STATUS_INVALID, STATUS_MANUAL_REVIEW,
})


def add_entry(
    *, policy_id: int, value: str,
    entry_type: str,
    normalized_value: Optional[str] = None,
    dst_port: str = "",
    protocol: str = "",
    status: str = STATUS_ACTIVE,
    notes: str = "",
) -> int:
    if entry_type not in ALLOWED_ENTRY_TYPES:
        raise ValueError(f"invalid entry_type: {entry_type!r}")
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    nv = (normalized_value or value or "").strip().lower()
    if not nv:
        raise ValueError("normalized_value cannot be empty")
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_walled_garden_entries
                (policy_id, entry_type, value, normalized_value,
                 dst_port, protocol, status, notes,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (policy_id, entry_type, normalized_value)
            DO UPDATE SET
                value = excluded.value,
                dst_port = excluded.dst_port,
                protocol = excluded.protocol,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                int(policy_id), entry_type,
                value[:255], nv[:255],
                (dst_port or "")[:40],
                (protocol or "")[:20],
                status, (notes or "")[:500],
                now, now,
            ),
        )
        rid = cur.lastrowid
        if rid:
            return int(rid)
        row = db().execute(
            "SELECT id FROM npc_walled_garden_entries "
            "WHERE policy_id=? AND entry_type=? "
            "AND normalized_value=?",
            (int(policy_id), entry_type, nv),
        ).fetchone()
        return int(row["id"]) if row else 0


def add_entries_many(
    policy_id: int, items: Iterable[dict],
) -> dict[str, int]:
    inserted = updated = skipped = 0
    for item in items:
        try:
            existed = bool(db().execute(
                "SELECT 1 FROM npc_walled_garden_entries "
                "WHERE policy_id=? AND entry_type=? "
                "AND normalized_value=?",
                (int(policy_id),
                 (item.get("entry_type") or ""),
                 (item.get("normalized_value")
                  or item.get("value") or "").strip().lower()),
            ).fetchone())
        except Exception:  # noqa: BLE001
            existed = False
        try:
            add_entry(policy_id=int(policy_id), **item)
            if existed:
                updated += 1
            else:
                inserted += 1
        except (ValueError, TypeError):
            skipped += 1
    return {"inserted": inserted, "updated": updated,
            "skipped": skipped}


def get_entry(entry_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_walled_garden_entries WHERE id=?",
        (int(entry_id),),
    ).fetchone()
    return dict(row) if row else None


def list_entries(
    policy_id: int, *,
    entry_type: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    sql = ["SELECT * FROM npc_walled_garden_entries "
           "WHERE policy_id=?"]
    params: list[Any] = [int(policy_id)]
    if entry_type:
        sql.append("AND entry_type=?")
        params.append(entry_type)
    if status:
        sql.append("AND status=?")
        params.append(status)
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def entry_counts(policy_id: int) -> dict[str, int]:
    """Counts keyed by entry_type, plus 'total'."""
    rows = db().execute(
        "SELECT entry_type, COUNT(*) AS n "
        "FROM npc_walled_garden_entries WHERE policy_id=? "
        "GROUP BY entry_type",
        (int(policy_id),),
    ).fetchall()
    out = {t: 0 for t in ALLOWED_ENTRY_TYPES}
    total = 0
    for r in rows:
        out[r["entry_type"]] = int(r["n"])
        total += int(r["n"])
    out["total"] = total
    return out


def set_entry_status(entry_id: int, status: str) -> bool:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    with transaction() as c:
        cur = c.execute(
            "UPDATE npc_walled_garden_entries "
            "SET status=?, updated_at=? WHERE id=?",
            (status, now_iso(), int(entry_id)),
        )
        return cur.rowcount > 0


def delete_entry(entry_id: int) -> bool:
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM npc_walled_garden_entries WHERE id=?",
            (int(entry_id),),
        )
        return cur.rowcount > 0


def delete_entries_for_policy(policy_id: int) -> int:
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM npc_walled_garden_entries "
            "WHERE policy_id=?",
            (int(policy_id),),
        )
        return int(cur.rowcount or 0)


__all__ = [
    "ENTRY_TYPE_DST_HOST", "ENTRY_TYPE_DST_ADDRESS",
    "ENTRY_TYPE_DST_ADDRESS_LIST",
    "ALLOWED_ENTRY_TYPES",
    "STATUS_ACTIVE", "STATUS_DISABLED",
    "STATUS_INVALID", "STATUS_MANUAL_REVIEW",
    "ALLOWED_STATUSES",
    "create_policy", "get_policy", "get_policy_by_slug",
    "list_policies_for_router", "list_policies_for_tenant",
    "update_policy", "delete_policy",
    "add_entry", "add_entries_many",
    "get_entry", "list_entries", "entry_counts",
    "set_entry_status",
    "delete_entry", "delete_entries_for_policy",
]
