"""site_exit_policies_repo — VX2 selected-sites policies.

A policy ties a (router, exit_node) pair together with the
operator-chosen behaviour: name, fail_mode, subdomain
treatment, whether to also route router-originated traffic.

Enums (fail_mode) are enforced here, not in the DB schema —
that matches the existing project convention (see
router_backups_repo.reason).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from ..connection import db, transaction


# fail_mode constants — the script renderer keys behaviour on
# these strings, so changes here are coordinated changes.
FAIL_MODE_BLOCK_WHEN_VPS_DOWN = "block_when_vps_down"
FAIL_MODE_FALLBACK_TO_WAN     = "fallback_to_wan"

ALLOWED_FAIL_MODES = frozenset({
    FAIL_MODE_BLOCK_WHEN_VPS_DOWN,
    FAIL_MODE_FALLBACK_TO_WAN,
})

# source_scope is intentionally narrow today — `all_users` is
# the only supported value. Other future scopes (per-group,
# per-subscriber) get added here when the planner learns them.
SOURCE_SCOPE_ALL_USERS = "all_users"
ALLOWED_SOURCE_SCOPES = frozenset({SOURCE_SCOPE_ALL_USERS})


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def slugify(value: str) -> str:
    """Lowercase, ASCII, dash-separated. Empty input → ''.

    Arabic / Unicode names that contain no ASCII letters or
    digits get a deterministic `policy-<hash>` fallback so the
    operator's chosen name doesn't break URL routing. The
    fallback is stable for the same input — re-creating with
    the exact same Arabic name reuses the same slug, which
    the (tenant_id, slug) UNIQUE index then catches as a
    legitimate dup.
    """
    if not value:
        return ""
    s = value.strip().lower().replace(" ", "-")
    s = _SLUG_RE.sub("-", s)
    s = s.strip("-")
    if not s:
        # No ASCII letters / digits in the input — Arabic-only
        # name, emoji-only, etc. Generate a stable, URL-safe
        # handle from a SHA-1 of the original.
        import hashlib
        h = hashlib.sha1(value.strip().encode("utf-8")).hexdigest()[:10]
        return f"policy-{h}"
    return s[:64]


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def create(
    *, tenant_id: int, router_id: int, exit_node_id: int,
    name: str,
    slug: Optional[str] = None,
    fail_mode: str = FAIL_MODE_BLOCK_WHEN_VPS_DOWN,
    source_scope: str = SOURCE_SCOPE_ALL_USERS,
    include_subdomains: bool = True,
    include_router_output: bool = False,
    enabled: bool = True,
) -> int:
    if fail_mode not in ALLOWED_FAIL_MODES:
        raise ValueError(f"invalid fail_mode: {fail_mode!r}")
    if source_scope not in ALLOWED_SOURCE_SCOPES:
        raise ValueError(f"invalid source_scope: {source_scope!r}")
    final_slug = slugify(slug or name)
    if not final_slug:
        raise ValueError("policy slug cannot be empty")
    now = _now()
    with transaction() as c:
        cur = c.execute(
            """
            INSERT INTO site_exit_policies
                (tenant_id, router_id, exit_node_id, name, slug,
                 source_scope, fail_mode,
                 include_subdomains, include_router_output,
                 enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(tenant_id), int(router_id), int(exit_node_id),
                name[:120], final_slug,
                source_scope, fail_mode,
                1 if include_subdomains else 0,
                1 if include_router_output else 0,
                1 if enabled else 0, now, now,
            ),
        )
        return int(cur.lastrowid)


def get_by_id(tenant_id: int, policy_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM site_exit_policies "
        "WHERE tenant_id=? AND id=?",
        (int(tenant_id), int(policy_id)),
    ).fetchone()
    return dict(row) if row else None


def get_by_slug(tenant_id: int, slug: str) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM site_exit_policies "
        "WHERE tenant_id=? AND slug=?",
        (int(tenant_id), str(slug)),
    ).fetchone()
    return dict(row) if row else None


def list_for_router(
    tenant_id: int, router_id: int, *,
    only_enabled: bool = False,
) -> list[dict]:
    sql = ["SELECT * FROM site_exit_policies "
           "WHERE tenant_id=? AND router_id=?"]
    params: list[Any] = [int(tenant_id), int(router_id)]
    if only_enabled:
        sql.append("AND enabled=1")
    sql.append("ORDER BY id")
    rows = db().execute(" ".join(sql), tuple(params)).fetchall()
    return [dict(r) for r in rows]


def list_for_tenant(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT * FROM site_exit_policies "
        "WHERE tenant_id=? ORDER BY id",
        (int(tenant_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def update(
    tenant_id: int, policy_id: int, **changes,
) -> Optional[dict]:
    allowed = {
        "name", "slug", "exit_node_id",
        "source_scope", "fail_mode",
        "include_subdomains", "include_router_output",
        "enabled",
    }
    payload: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in allowed:
            continue
        if k == "fail_mode" and v not in ALLOWED_FAIL_MODES:
            raise ValueError(f"invalid fail_mode: {v!r}")
        if k == "source_scope" and v not in ALLOWED_SOURCE_SCOPES:
            raise ValueError(f"invalid source_scope: {v!r}")
        if k in {"include_subdomains", "include_router_output",
                  "enabled"}:
            payload[k] = 1 if v else 0
        elif k == "slug":
            s = slugify(str(v))
            if not s:
                raise ValueError("slug cannot be empty")
            payload[k] = s
        elif k == "exit_node_id":
            payload[k] = int(v)
        else:
            payload[k] = (str(v) or "")[:120]
    if not payload:
        return get_by_id(tenant_id, policy_id)
    fields = ", ".join(f"{k}=?" for k in payload.keys())
    params: list[Any] = list(payload.values())
    params.extend([_now(), int(tenant_id), int(policy_id)])
    with transaction() as c:
        c.execute(
            f"UPDATE site_exit_policies SET {fields}, updated_at=? "
            "WHERE tenant_id=? AND id=?",
            tuple(params),
        )
    return get_by_id(tenant_id, policy_id)


def delete(tenant_id: int, policy_id: int) -> bool:
    """Hard delete policy + cascades targets/deployments/versions.

    SQLite without FK enforcement won't cascade automatically;
    we do it explicitly here so the repo is the single source
    of truth for the lifecycle.
    """
    with transaction() as c:
        c.execute(
            "DELETE FROM site_exit_targets WHERE policy_id=?",
            (int(policy_id),),
        )
        c.execute(
            "DELETE FROM site_exit_deployments WHERE policy_id=?",
            (int(policy_id),),
        )
        c.execute(
            "DELETE FROM site_exit_script_versions "
            "WHERE policy_id=?",
            (int(policy_id),),
        )
        cur = c.execute(
            "DELETE FROM site_exit_policies "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(policy_id)),
        )
        return cur.rowcount > 0


__all__ = [
    "FAIL_MODE_BLOCK_WHEN_VPS_DOWN", "FAIL_MODE_FALLBACK_TO_WAN",
    "ALLOWED_FAIL_MODES",
    "SOURCE_SCOPE_ALL_USERS", "ALLOWED_SOURCE_SCOPES",
    "slugify",
    "create", "get_by_id", "get_by_slug",
    "list_for_router", "list_for_tenant",
    "update", "delete",
]
