"""O11 — Permission review matrix route.

Read-only page at /admin/radius/permissions. Lists every active
admin × every MikroTik permission so operators can audit who
can do what.

Guarded by PERM_AUDIT_VIEW so it lives under the same gate as
the audit log itself — both are visibility tools.
"""
from __future__ import annotations

from flask import Blueprint, render_template

from ..services.mt_permission_matrix import build_matrix
from ..services.mt_permissions import (
    PERM_AUDIT_VIEW, requires_perm,
)


def register_mt_permission_matrix_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/permissions", "mt_permission_matrix",
        requires_perm(PERM_AUDIT_VIEW)(mt_permission_matrix),
        methods=["GET"],
    )


_PERM_LABELS_AR: dict[str, str] = {
    "mikrotik.view":          "عرض",
    "mikrotik.diagnostics":   "تشخيص",
    "mikrotik.manage":        "إدارة",
    "mikrotik.program":       "برمجة",
    "mikrotik.deploy_login":  "نشر صفحة الدخول",
    "mikrotik.rollback":      "تراجع",
    "mikrotik.backup":        "نسخ احتياطي",
    "mikrotik.restore":       "استعادة",
    "mikrotik.audit.view":    "عرض السجل",
    "mikrotik.admin":         "صلاحية كاملة",
}


def mt_permission_matrix():
    matrix = build_matrix()
    # Pre-compute per-permission grant counts so the template
    # can render a "X من Y" footer per column without doing it
    # inline.
    grant_counts = {
        p: matrix.grants_for(p) for p in matrix.permissions
    }
    return render_template(
        "radius/mt_permission_matrix.html",
        matrix=matrix,
        perm_labels=_PERM_LABELS_AR,
        grant_counts=grant_counts,
        total_admins=matrix.total_admins(),
    )
