"""site_exit_targets_repo — VX2 individual destinations.

A target is one of:
  - a domain                (target_type='domain')
  - an IPv4 address         (target_type='ip')
  - a CIDR range            (target_type='cidr')

Each row carries the operator's original value AND a normalized
form. The (policy_id, normalized_value) pair is unique at the DB
layer so a re-import idempotently refreshes instead of inserting
duplicates.

The target classifier (VX2.2) writes `group_name`; the planner
(VX2.3) reads it to decide whether to include the target. This
repo doesn't classify — it only persists what callers hand it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from ..connection import db, transaction


# target_type enum.
TARGET_TYPE_DOMAIN = "domain"
TARGET_TYPE_IP     = "ip"
TARGET_TYPE_CIDR   = "cidr"

ALLOWED_TARGET_TYPES = frozenset({
    TARGET_TYPE_DOMAIN, TARGET_TYPE_IP, TARGET_TYPE_CIDR,
})

# status enum. `manual_review` means the classifier left it
# pending and the operator must triage before apply.
STATUS_ACTIVE        = "active"
STATUS_DISABLED      = "disabled"
STATUS_INVALID       = "invalid"
STATUS_MANUAL_REVIEW = "manual_review"

ALLOWED_STATUSES = frozenset({
    STATUS_ACTIVE, STATUS_DISABLED,
    STATUS_INVALID, STATUS_MANUAL_REVIEW,
})

# group_name enum — matches the classifier output.
GROUP_SPEEDTEST_MEASUREMENT = "speedtest_measurement"
GROUP_PUBLIC_IP_CHECKERS    = "public_ip_checkers"
GROUP_VPN_PROVIDER_PAGES    = "vpn_provider_pages"
GROUP_NETWORK_DIAGNOSTICS   = "network_diagnostics"
GROUP_GENERAL_PROBE_SITES   = "general_probe_sites"
GROUP_RAW_IP_TARGETS        = "raw_ip_targets"
GROUP_MANUAL_REVIEW         = "manual_review"

ALLOWED_GROUPS = frozenset({
    GROUP_SPEEDTEST_MEASUREMENT, GROUP_PUBLIC_IP_CHECKERS,
    GROUP_VPN_PROVIDER_PAGES, GROUP_NETWORK_DIAGNOSTICS,
    GROUP_GENERAL_PROBE_SITES, GROUP_RAW_IP_TARGETS,
    GROUP_MANUAL_REVIEW,
})


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def add(
    *, policy_id: int, value: str,
    target_type: str,
    normalized_value: Optional[str] = None,
    group_name: str = GROUP_MANUAL_REVIEW,
    status: str = STATUS_ACTIVE,
    include_www: bool = True,
    include_subdomains: bool = True,
    notes: str = "",
) -> int:
    """Insert one target. Raises ValueError on bad enum.

    On (policy_id, normalized_value) conflict, returns the
    existing row id instead — re-imports are idempotent.
    """
    if target_type not in ALLOWED_TARGET_TYPES:
        raise ValueError(f"invalid target_type: {target_type!r}")
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    if group_name not in ALLOWED_GROUPS:
        raise ValueError(f"invalid group_name: {group_name!r}")
    nv = (normalized_value or value or "").strip().lower()
    if not nv:
        raise ValueError("normalized_value cannot be empty")
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO site_exit_targets
                (policy_id, group_name, target_type, value,
                 normalized_value, include_www,
                 include_subdomains, status, notes,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (policy_id, normalized_value)
            DO UPDATE SET
                group_name = excluded.group_name,
                target_type = excluded.target_type,
                value = excluded.value,
                status = excluded.status,
                include_www = excluded.include_www,
                include_subdomains = excluded.include_subdomains,
                updated_at = excluded.updated_at
            """,
            (
                int(policy_id), group_name, target_type,
                value[:255], nv[:255],
                1 if include_www else 0,
                1 if include_subdomains else 0,
                status, notes[:500], now, now,
            ),
        )
        rid = cur.lastrowid
        if rid:
            return int(rid)
        # ON CONFLICT path — lastrowid is 0; fetch by dedup key.
        row = db().execute(
            "SELECT id FROM site_exit_targets "
            "WHERE policy_id=? AND normalized_value=?",
            (int(policy_id), nv),
        ).fetchone()
        return int(row["id"]) if row else 0


def add_many(
    policy_id: int, items: Iterable[dict],
) -> dict[str, int]:
    """Bulk add — returns {"inserted": N, "updated": M, "skipped": K}.

    Each item dict must include: value, target_type. Optional:
    normalized_value, group_name, status, include_www,
    include_subdomains, notes.
    """
    inserted = updated = skipped = 0
    for item in items:
        try:
            existed_before = bool(db().execute(
                "SELECT 1 FROM site_exit_targets "
                "WHERE policy_id=? AND normalized_value=?",
                (int(policy_id),
                 (item.get("normalized_value")
                  or item.get("value") or "").strip().lower()),
            ).fetchone())
        except Exception:  # noqa: BLE001
            existed_before = False
        try:
            add(policy_id=int(policy_id), **item)
            if existed_before:
                updated += 1
            else:
                inserted += 1
        except (ValueError, TypeError):
            skipped += 1
    return {"inserted": inserted, "updated": updated,
            "skipped": skipped}


def get_by_id(target_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM site_exit_targets WHERE id=?",
        (int(target_id),),
    ).fetchone()
    return dict(row) if row else None


def list_for_policy(
    policy_id: int, *,
    group_name: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    sql = ["SELECT * FROM site_exit_targets WHERE policy_id=?"]
    params: list[Any] = [int(policy_id)]
    if group_name:
        sql.append("AND group_name=?")
        params.append(group_name)
    if status:
        sql.append("AND status=?")
        params.append(status)
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def group_counts(policy_id: int) -> dict[str, int]:
    """Returns {group_name: count} including a 'total' key."""
    rows = db().execute(
        "SELECT group_name, COUNT(*) AS n "
        "FROM site_exit_targets WHERE policy_id=? "
        "GROUP BY group_name",
        (int(policy_id),),
    ).fetchall()
    out = {g: 0 for g in ALLOWED_GROUPS}
    total = 0
    for r in rows:
        out[r["group_name"]] = int(r["n"])
        total += int(r["n"])
    out["total"] = total
    return out


def set_status(target_id: int, status: str) -> bool:
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    with transaction() as c:
        cur = c.execute(
            "UPDATE site_exit_targets SET status=?, updated_at=? "
            "WHERE id=?",
            (status, _now(), int(target_id)),
        )
        return cur.rowcount > 0


def delete(target_id: int) -> bool:
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM site_exit_targets WHERE id=?",
            (int(target_id),),
        )
        return cur.rowcount > 0


def delete_for_policy(policy_id: int) -> int:
    with transaction() as c:
        cur = c.execute(
            "DELETE FROM site_exit_targets WHERE policy_id=?",
            (int(policy_id),),
        )
        return int(cur.rowcount or 0)


__all__ = [
    "TARGET_TYPE_DOMAIN", "TARGET_TYPE_IP", "TARGET_TYPE_CIDR",
    "ALLOWED_TARGET_TYPES",
    "STATUS_ACTIVE", "STATUS_DISABLED",
    "STATUS_INVALID", "STATUS_MANUAL_REVIEW",
    "ALLOWED_STATUSES",
    "GROUP_SPEEDTEST_MEASUREMENT", "GROUP_PUBLIC_IP_CHECKERS",
    "GROUP_VPN_PROVIDER_PAGES", "GROUP_NETWORK_DIAGNOSTICS",
    "GROUP_GENERAL_PROBE_SITES", "GROUP_RAW_IP_TARGETS",
    "GROUP_MANUAL_REVIEW", "ALLOWED_GROUPS",
    "add", "add_many", "get_by_id",
    "list_for_policy", "group_counts",
    "set_status", "delete", "delete_for_policy",
]
