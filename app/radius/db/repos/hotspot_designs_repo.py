"""hotspot_designs_repo — persistence for R2 designer state.

One row per (tenant_id, nas_id). UPSERT semantics: if a row
already exists for this nas, we replace it. Tests verify the
unique constraint holds.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..connection import db, transaction


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def get_design(tenant_id: int, nas_id: int) -> dict[str, Any] | None:
    row = db().execute(
        "SELECT id, tenant_id, nas_id, template_slug, variables_json, "
        "       updated_at "
        "FROM hotspot_designs "
        "WHERE tenant_id=? AND nas_id=?",
        (int(tenant_id), int(nas_id)),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["variables"] = json.loads(out["variables_json"] or "{}")
    except (TypeError, ValueError):
        out["variables"] = {}
    return out


def save_design(
    tenant_id: int, nas_id: int, *,
    template_slug: str, variables: dict[str, str],
) -> None:
    """UPSERT a design. `variables` is JSON-serialized at the
    boundary so the caller doesn't have to remember the column
    is text; nothing else writes to this table."""
    payload = json.dumps(variables, ensure_ascii=False)
    with transaction() as c:
        # SQLite UPSERT — relies on the UNIQUE(tenant_id, nas_id)
        # index from the 036 migration.
        c.execute(
            "INSERT INTO hotspot_designs "
            "  (tenant_id, nas_id, template_slug, variables_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, nas_id) DO UPDATE SET "
            "  template_slug = excluded.template_slug, "
            "  variables_json = excluded.variables_json, "
            "  updated_at = excluded.updated_at",
            (int(tenant_id), int(nas_id), template_slug, payload, _now()),
        )


def delete_design(tenant_id: int, nas_id: int) -> None:
    with transaction() as c:
        c.execute(
            "DELETE FROM hotspot_designs "
            "WHERE tenant_id=? AND nas_id=?",
            (int(tenant_id), int(nas_id)),
        )


__all__ = ["get_design", "save_design", "delete_design"]
