"""Read-only permission governance page."""
from __future__ import annotations

from flask import Blueprint, render_template

from ..services.mt_permission_matrix import build_matrix
from ..services.mt_permissions import PERM_AUDIT_VIEW, requires_perm


def register_mt_permission_matrix_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/permissions",
        "mt_permission_matrix",
        requires_perm(PERM_AUDIT_VIEW)(mt_permission_matrix),
        methods=["GET"],
    )


_PERM_LABELS_AR: dict[str, str] = {
    "mikrotik.view": "عرض",
    "mikrotik.diagnostics": "تشخيص",
    "mikrotik.manage": "إدارة",
    "mikrotik.program": "برمجة",
    "mikrotik.deploy_login": "نشر صفحة الدخول",
    "mikrotik.rollback": "تراجع",
    "mikrotik.backup": "نسخ احتياطي",
    "mikrotik.restore": "استعادة",
    "mikrotik.audit.view": "سجل العمليات",
    "mikrotik.admin": "تحكم كامل",
    "site_exit.view": "عرض خروج الإنترنت عبر VPS",
    "site_exit.manage": "إدارة خروج الإنترنت عبر VPS",
    "site_exit.preview": "معاينة خروج الإنترنت عبر VPS",
    "site_exit.apply": "تطبيق خروج الإنترنت عبر VPS",
    "site_exit.override_backup_warning": "تجاوز تحذير النسخ",
    "site_exit.enable_risky_groups": "تفعيل مجموعات خطرة",
    "npc.remote_access.view": "عرض الوصول",
    "npc.remote_access.manage": "إدارة الوصول",
    "npc.remote_access.preview": "معاينة الوصول",
    "npc.remote_access.apply": "تطبيق الوصول",
    "npc.web_block.view": "عرض الحظر",
    "npc.web_block.manage": "إدارة الحظر",
    "npc.web_block.preview": "معاينة الحظر",
    "npc.web_block.apply": "تطبيق الحظر",
    "npc.walled_garden.view": "عرض Walled Garden",
    "npc.walled_garden.manage": "إدارة Walled Garden",
    "npc.walled_garden.preview": "معاينة Walled Garden",
    "npc.walled_garden.apply": "تطبيق Walled Garden",
}

_RISKY_PERMS = {
    "mikrotik.admin",
    "mikrotik.program",
    "mikrotik.rollback",
    "mikrotik.restore",
    "site_exit.apply",
    "site_exit.override_backup_warning",
    "site_exit.enable_risky_groups",
    "npc.remote_access.apply",
    "npc.web_block.apply",
    "npc.walled_garden.apply",
}

_PERM_GROUPS = (
    (
        "core",
        "صلاحيات الراوتر",
        "router",
        "القراءة والتشخيص والتشغيل اليومي",
        (
            "mikrotik.view",
            "mikrotik.diagnostics",
            "mikrotik.manage",
            "mikrotik.program",
            "mikrotik.deploy_login",
            "mikrotik.rollback",
            "mikrotik.backup",
            "mikrotik.restore",
            "mikrotik.audit.view",
            "mikrotik.admin",
        ),
    ),
    (
        "exit",
        "خروج الإنترنت عبر VPS",
        "route",
        "تحويل مواقع مختارة عبر الخادم",
        (
            "site_exit.view",
            "site_exit.manage",
            "site_exit.preview",
            "site_exit.apply",
            "site_exit.override_backup_warning",
            "site_exit.enable_risky_groups",
        ),
    ),
    (
        "npc",
        "سياسات الشبكة",
        "shield-halved",
        "الوصول والحظر والـ Walled Garden",
        (
            "npc.remote_access.view",
            "npc.remote_access.manage",
            "npc.remote_access.preview",
            "npc.remote_access.apply",
            "npc.web_block.view",
            "npc.web_block.manage",
            "npc.web_block.preview",
            "npc.web_block.apply",
            "npc.walled_garden.view",
            "npc.walled_garden.manage",
            "npc.walled_garden.preview",
            "npc.walled_garden.apply",
        ),
    ),
)


def _group_cards(matrix, grant_counts: dict[str, int]) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    for key, title, icon, description, permissions in _PERM_GROUPS:
        available = [perm for perm in permissions if perm in matrix.permissions]
        cards.append({
            "key": key,
            "title": title,
            "icon": icon,
            "description": description,
            "permission_count": len(available),
            "grant_count": sum(int(grant_counts.get(perm, 0) or 0) for perm in available),
        })
    return cards


def mt_permission_matrix():
    matrix = build_matrix()
    grant_counts = {permission: matrix.grants_for(permission) for permission in matrix.permissions}
    super_admins = sum(1 for row in matrix.rows if row.is_super_admin)
    via_admin = sum(1 for row in matrix.rows if row.via_admin)
    no_access = sum(1 for row in matrix.rows if row.granted_count <= 0)
    risky_grants = sum(
        int(grant_counts.get(permission, 0) or 0)
        for permission in matrix.permissions
        if permission in _RISKY_PERMS
    )
    return render_template(
        "radius/mt_permission_matrix.html",
        matrix=matrix,
        perm_labels=_PERM_LABELS_AR,
        grant_counts=grant_counts,
        total_admins=matrix.total_admins(),
        group_cards=_group_cards(matrix, grant_counts),
        summary={
            "super_admins": super_admins,
            "via_admin": via_admin,
            "no_access": no_access,
            "risky_grants": risky_grants,
        },
        risky_perms=_RISKY_PERMS,
    )
