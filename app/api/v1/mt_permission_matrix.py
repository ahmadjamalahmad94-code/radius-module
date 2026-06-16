"""mikrotik permission-matrix — v1 JSON API (feat/api-first-parity, group 7f).

Mirrors the MikroTik permission-matrix web page
(`routes/mt_permission_matrix.py`, `/admin/radius/permissions`) as JSON.
Read-only — reuses `mt_permission_matrix.build_matrix` + the route's Arabic
labels / group cards / summary. (The web page has no save action — RBAC is
edited via the roles/admins screens — so this is GET-only.)
"""
from __future__ import annotations

import dataclasses

from flask import Blueprint, g

from ...radius.routes import mt_permission_matrix as web
from ...radius.services.mt_permission_matrix import build_matrix
from ..auth import require_api_token
from ..responses import ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/mikrotik/permissions", "mt_permission_matrix",
                    require_api_token(matrix), methods=["GET"])


def matrix():
    """GET /mikrotik/permissions — مصفوفة صلاحيات المايكروتيك لكل مدير +
    العدّادات/البطاقات/الملخّص (يطابق صفحة الويب)."""
    m = build_matrix()
    grant_counts = {p: m.grants_for(p) for p in m.permissions}
    summary = {
        "super_admins": sum(1 for r in m.rows if r.is_super_admin),
        "via_admin": sum(1 for r in m.rows if r.via_admin),
        "no_access": sum(1 for r in m.rows if r.granted_count <= 0),
        "risky_grants": sum(int(grant_counts.get(p, 0) or 0)
                            for p in m.permissions if p in web._RISKY_PERMS),
    }
    return ok({
        "permissions": list(m.permissions),
        "perm_labels": web._PERM_LABELS_AR,
        "rows": [dataclasses.asdict(r) for r in m.rows],
        "grant_counts": grant_counts,
        "total_admins": m.total_admins(),
        "group_cards": web._group_cards(m, grant_counts),
        "summary": summary,
        "risky_perms": list(web._RISKY_PERMS),
    })
