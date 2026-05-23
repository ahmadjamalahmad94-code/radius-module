"""npc_remote_access_repo — Remote MikroTik Access policies.

One policy = one (router, name) request to allow a specific
set of admin ports (winbox / ssh / api / api_ssl / webfig http
or https) into a router for a bounded time window, optionally
restricted to a source address list.

The repo is the only place these fields are written. `update()`
is allow-listed — unknown keys are silently ignored so a
caller can't slip a `password` column past us if the schema
ever grew one.

Lifecycle of the live apply (preview → apply → expire) lives
on the shared `npc_deployments` table. This repo only owns
the policy definition.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from .npc_common import now_iso, slugify


# Allow-list of columns `update()` will write. Anything outside
# this set is ignored. Tightening this list is the easiest way
# to refuse a new secret column should one ever creep in.
_ALLOWED_UPDATE_FIELDS = frozenset({
    "name", "slug",
    "allow_winbox", "allow_ssh", "allow_api", "allow_api_ssl",
    "allow_webfig_http", "allow_webfig_https",
    "source_address_list",
    "expires_at",
    "reason",
    "enabled",
})

# Bool-coerced fields — `_pack()` converts True/False/0/1/'1'/'0'
# into the 0/1 integer the SQLite column expects.
_BOOL_FIELDS = frozenset({
    "allow_winbox", "allow_ssh", "allow_api", "allow_api_ssl",
    "allow_webfig_http", "allow_webfig_https", "enabled",
})


def _pack_bool(v: Any) -> int:
    """Lenient coercion: truthy → 1, falsy → 0. Strings '1'/'0'
    handled so HTML form posts and JSON booleans both work."""
    if v is True or v == 1 or v == "1":
        return 1
    return 0


def create(
    *, tenant_id: int, router_id: int,
    name: str,
    slug: Optional[str] = None,
    allow_winbox: bool = True,
    allow_ssh: bool = False,
    allow_api: bool = False,
    allow_api_ssl: bool = False,
    allow_webfig_http: bool = False,
    allow_webfig_https: bool = True,
    source_address_list: str = "",
    expires_at: str = "",
    reason: str = "",
    enabled: bool = True,
) -> int:
    """Insert one policy. Returns the new id.

    `slug` defaults to `slugify(name)`; an Arabic-only name
    gets the deterministic `policy-<hash>` fallback so the
    caller doesn't have to special-case Unicode.
    """
    final_slug = slugify(slug or name)
    if not final_slug:
        raise ValueError("remote-access policy slug cannot be empty")
    now = now_iso()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO npc_remote_access_policies
                (tenant_id, router_id, name, slug,
                 allow_winbox, allow_ssh, allow_api, allow_api_ssl,
                 allow_webfig_http, allow_webfig_https,
                 source_address_list, expires_at, reason,
                 enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id), int(router_id),
                name[:120], final_slug,
                _pack_bool(allow_winbox), _pack_bool(allow_ssh),
                _pack_bool(allow_api), _pack_bool(allow_api_ssl),
                _pack_bool(allow_webfig_http),
                _pack_bool(allow_webfig_https),
                (source_address_list or "")[:120],
                (expires_at or "")[:40],
                (reason or "")[:500],
                _pack_bool(enabled),
                now, now,
            ),
        )
        return int(cur.lastrowid)


def get_by_id(tenant_id: int, policy_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_remote_access_policies "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(policy_id)),
    ).fetchone()
    return dict(row) if row else None


def get_by_slug(tenant_id: int, slug: str) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM npc_remote_access_policies "
        "WHERE tenant_id=? AND slug=?",
        (int(tenant_id), str(slug)),
    ).fetchone()
    return dict(row) if row else None


def list_for_router(
    tenant_id: int, router_id: int, *,
    only_enabled: bool = False,
) -> list[dict]:
    sql = ["SELECT * FROM npc_remote_access_policies "
           "WHERE tenant_id=? AND router_id=?"]
    params: list[Any] = [int(tenant_id), int(router_id)]
    if only_enabled:
        sql.append("AND enabled=1")
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def list_for_tenant(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM npc_remote_access_policies "
        "WHERE tenant_id=? ORDER BY id",
        (int(tenant_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def update(
    tenant_id: int, policy_id: int, **changes,
) -> Optional[dict]:
    """Allow-listed update. Unknown keys are dropped silently —
    that's the defence-in-depth against a stray
    `password`/`secret` kwarg from a future caller."""
    payload: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in _ALLOWED_UPDATE_FIELDS:
            continue
        if k in _BOOL_FIELDS:
            payload[k] = _pack_bool(v)
        elif k == "slug":
            s = slugify(str(v))
            if not s:
                raise ValueError("slug cannot be empty")
            payload[k] = s
        else:
            payload[k] = (str(v) or "")[:500]
    if not payload:
        return get_by_id(tenant_id, policy_id)
    fields = ", ".join(f"{k}=?" for k in payload.keys())
    params: list[Any] = list(payload.values())
    params.extend([now_iso(), int(tenant_id), int(policy_id)])
    with transaction() as c:
        c.execute(
            f"UPDATE npc_remote_access_policies "
            f"SET {fields}, updated_at=? "
            "WHERE tenant_id=? AND id=?",
            tuple(params),
        )
    return get_by_id(tenant_id, policy_id)


def delete(tenant_id: int, policy_id: int) -> bool:
    """Hard delete + cascade to shared deployments/script
    versions. SQLite doesn't enforce FKs across our tables, so
    the cascade is explicit and lives here."""
    with transaction() as c:
        c.execute(
            "DELETE FROM npc_script_versions "
            "WHERE service='remote_access' AND policy_id=?",
            (int(policy_id),),
        )
        c.execute(
            "DELETE FROM npc_deployments "
            "WHERE service='remote_access' AND policy_id=?",
            (int(policy_id),),
        )
        cur = c.execute(
            "DELETE FROM npc_remote_access_policies "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(policy_id)),
        )
        return cur.rowcount > 0


__all__ = [
    "create", "get_by_id", "get_by_slug",
    "list_for_router", "list_for_tenant",
    "update", "delete",
]
