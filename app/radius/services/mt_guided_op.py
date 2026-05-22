"""mt_guided_op — O12 guided operations assistant.

Composer that builds a server-rendered checklist for risky
MikroTik operations by stitching together the Phase O Foundation
services. NO new business logic — every step delegates to:

  - O2 (mt_health_score)     → "is the router healthy?"
  - O5 (mt_safety_check)     → "permissions + state sanity?"
  - O7 (router_backups_repo) → "is there a recent backup?"
  - O8 (mt_recovery_plan)    → "what to do if the last attempt
                                 failed?"

The result is a `GuidedChecklist` of `Step` items the route
template renders as plain rows. Each step has a clear state
(ok / warning / blocking / info) so the operator sees at a
glance what's done and what blocks them.

Supported operations:
  programming_hotspot
  programming_pppoe
  unprogramming
  restore
  backup_save

Unknown operation → falls back to a generic "view-only" plan.

This is intentionally read-only — calling render_checklist
never mutates state, never contacts a router, never triggers a
job. It just composes existing reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..db.repos import audit_repo, router_backups_repo as br
from .mt_health_score import (
    STATE_HEALTHY, STATE_OFFLINE, STATE_RISKY, score_health,
)
from .mt_permissions import (
    PERM_BACKUP, PERM_PROGRAM, PERM_RESTORE, PERM_ROLLBACK,
)
from .mt_router_overview import (
    BACKUP_FRESH_SEC, build_overview,
)
from .mt_safety_check import evaluate as evaluate_safety


# ─── Operations ──────────────────────────────────────────────


OP_PROGRAMMING_HOTSPOT = "programming_hotspot"
OP_PROGRAMMING_PPPOE   = "programming_pppoe"
OP_UNPROGRAMMING       = "unprogramming"
OP_RESTORE             = "restore"
OP_BACKUP_SAVE         = "backup_save"

ALL_OPERATIONS: tuple[str, ...] = (
    OP_PROGRAMMING_HOTSPOT, OP_PROGRAMMING_PPPOE,
    OP_UNPROGRAMMING, OP_RESTORE, OP_BACKUP_SAVE,
)

_OP_LABELS_AR: dict[str, str] = {
    OP_PROGRAMMING_HOTSPOT: "برمجة Hotspot",
    OP_PROGRAMMING_PPPOE:   "برمجة PPPoE",
    OP_UNPROGRAMMING:       "تراجع/Unprogram",
    OP_RESTORE:             "استعادة من نسخة",
    OP_BACKUP_SAVE:         "حفظ نسخة احتياطية",
}

_OP_REQUIRED_PERM: dict[str, str] = {
    OP_PROGRAMMING_HOTSPOT: PERM_PROGRAM,
    OP_PROGRAMMING_PPPOE:   PERM_PROGRAM,
    OP_UNPROGRAMMING:       PERM_ROLLBACK,
    OP_RESTORE:             PERM_RESTORE,
    OP_BACKUP_SAVE:         PERM_BACKUP,
}

# Step-state constants — keep these stable, templates key on them.
STEP_OK       = "ok"
STEP_INFO     = "info"
STEP_WARNING  = "warning"
STEP_BLOCKING = "blocking"


@dataclass(frozen=True)
class Step:
    key: str                # stable identifier (e.g. "health")
    label_ar: str           # short title
    state: str              # one of STEP_*
    detail_ar: str          # one-line explanation
    href: str = ""          # optional deep link to the relevant tool


@dataclass(frozen=True)
class GuidedChecklist:
    nas_id: int
    operation: str
    operation_label_ar: str
    steps: tuple[Step, ...]
    can_proceed: bool       # True iff no step is STEP_BLOCKING
    apply_href: str = ""    # link to the operation's real form

    def blocking_steps(self) -> tuple[Step, ...]:
        return tuple(s for s in self.steps if s.state == STEP_BLOCKING)

    def warning_steps(self) -> tuple[Step, ...]:
        return tuple(s for s in self.steps if s.state == STEP_WARNING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nas_id": self.nas_id,
            "operation": self.operation,
            "operation_label_ar": self.operation_label_ar,
            "can_proceed": self.can_proceed,
            "apply_href": self.apply_href,
            "steps": [
                {"key": s.key, "label_ar": s.label_ar,
                 "state": s.state, "detail_ar": s.detail_ar,
                 "href": s.href}
                for s in self.steps
            ],
        }


# ─── Step builders (pure where possible) ────────────────────


def _step_health(ov) -> Step:
    hs = score_health(ov)
    href = f"/admin/radius/mt/{ov.nas_id}/overview"
    if hs.state == STATE_HEALTHY:
        return Step("health", "صحة الراوتر", STEP_OK,
                    "الحالة سليمة.", href)
    if hs.state == STATE_OFFLINE:
        return Step("health", "صحة الراوتر", STEP_BLOCKING,
                    "الراوتر غير متصل — لا يمكن متابعة عملية حية.",
                    href)
    if hs.state == STATE_RISKY:
        return Step("health", "صحة الراوتر", STEP_WARNING,
                    f"صحة منخفضة (نقاط: {hs.score}). راجع المشاكل أولاً.",
                    href)
    return Step("health", "صحة الراوتر", STEP_INFO,
                f"الحالة: {hs.state} (نقاط: {hs.score}).", href)


def _step_safety(*, tenant_id, nas_id, admin,
                  required_perm, operation_audit_str) -> Step:
    sc = evaluate_safety(
        tenant_id=tenant_id, nas_id=nas_id,
        admin=admin, required_perm=required_perm,
        operation=operation_audit_str,
    )
    href = f"/admin/radius/mt/{nas_id}/overview"
    if not sc.allowed:
        return Step(
            "safety", "فحص ما قبل التنفيذ", STEP_BLOCKING,
            sc.blocking_reasons[0] if sc.blocking_reasons
            else "ممنوع.", href,
        )
    if sc.severity == "warning":
        return Step(
            "safety", "فحص ما قبل التنفيذ", STEP_WARNING,
            (sc.warnings[0] if sc.warnings
             else "تحذير قبل التنفيذ."), href,
        )
    return Step(
        "safety", "فحص ما قبل التنفيذ", STEP_OK,
        "السلامة جاهزة للتنفيذ.", href,
    )


def _step_backup(*, tenant_id, nas_id, operation) -> Step:
    href = f"/admin/radius/mt/{nas_id}/backups"
    rows = br.list_for_router(int(tenant_id), int(nas_id),
                                limit=1)
    last = rows[0] if rows else None
    if not last:
        # Backup-save itself is the step that creates the
        # backup — missing backup isn't blocking for it.
        if operation == OP_BACKUP_SAVE:
            return Step("backup", "النسخة الاحتياطية", STEP_INFO,
                        "لا توجد نسخة سابقة — ستكون هذه أول واحدة.",
                        href)
        return Step("backup", "النسخة الاحتياطية", STEP_BLOCKING,
                    "لا توجد نسخة احتياطية — خذ واحدة قبل المتابعة.",
                    href)
    # Compare age vs BACKUP_FRESH_SEC.
    from datetime import datetime, timezone
    try:
        ts = (last.get("created_at") or "").rstrip("Z")
        d = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - d).total_seconds()
    except Exception:  # noqa: BLE001
        age = 1e9
    if age <= BACKUP_FRESH_SEC:
        return Step("backup", "النسخة الاحتياطية", STEP_OK,
                    f"يوجد نسخة حديثة: {last.get('filename') or ''}.",
                    href)
    return Step("backup", "النسخة الاحتياطية", STEP_WARNING,
                "النسخة الأخيرة قديمة — يُستحسن أخذ نسخة جديدة.",
                href)


def _step_recent_failure(*, tenant_id, nas_id) -> Step:
    href = f"/admin/radius/mt/{nas_id}/timeline"
    rows = audit_repo.recent(
        int(tenant_id), router_id=int(nas_id), limit=10,
    )
    bad = next((r for r in rows
                if (r.get("result_status") or "")
                in {"failed", "partial"}), None)
    if not bad:
        return Step("recent_failure", "آخر العمليات", STEP_OK,
                    "لا توجد عملية فاشلة قريبة.", href)
    rid = bad.get("id")
    recovery = (f"/admin/radius/recovery/{rid}" if rid else href)
    return Step("recent_failure", "آخر العمليات", STEP_WARNING,
                "العملية السابقة فشلت/جزئية — راجع خطة التعافي قبل المتابعة.",
                recovery)


def _step_apply_link(*, nas_id, operation) -> Step:
    """Pointer to where the operator actually runs the op."""
    label_ar = "المتابعة للتنفيذ"
    detail_ar = "افتح صفحة العملية الفعلية بعد التحقق من الخطوات."
    if operation in (OP_PROGRAMMING_HOTSPOT,
                      OP_PROGRAMMING_PPPOE):
        href = f"/admin/radius/mt/{nas_id}/program"
    elif operation == OP_UNPROGRAMMING:
        href = f"/admin/radius/mt/{nas_id}/unprogram"
    elif operation == OP_RESTORE:
        href = f"/admin/radius/mt/{nas_id}/backups"
    elif operation == OP_BACKUP_SAVE:
        href = f"/admin/radius/mt/{nas_id}/backups"
    else:
        href = f"/admin/radius/mt/{nas_id}/dashboard"
    return Step("apply_link", label_ar, STEP_INFO, detail_ar, href)


# ─── Public API ──────────────────────────────────────────────


def build_checklist(
    *, tenant_id: int, nas_id: int, admin,
    operation: str,
) -> GuidedChecklist | None:
    """Return a checklist for `operation` on `nas_id`, or None
    when the router doesn't exist for that tenant."""
    if operation not in ALL_OPERATIONS:
        # Generic safe fallback: still valid, just minimal.
        operation = OP_BACKUP_SAVE
    ov = build_overview(tenant_id=int(tenant_id),
                         nas_id=int(nas_id))
    if ov is None:
        return None
    required_perm = _OP_REQUIRED_PERM[operation]
    op_audit_str = f"mt.guided.{operation}"
    steps: list[Step] = [
        _step_health(ov),
        _step_safety(
            tenant_id=int(tenant_id), nas_id=int(nas_id),
            admin=admin, required_perm=required_perm,
            operation_audit_str=op_audit_str,
        ),
        _step_backup(
            tenant_id=int(tenant_id), nas_id=int(nas_id),
            operation=operation,
        ),
        _step_recent_failure(
            tenant_id=int(tenant_id), nas_id=int(nas_id),
        ),
    ]
    apply = _step_apply_link(
        nas_id=int(nas_id), operation=operation)
    steps.append(apply)
    blocking = any(s.state == STEP_BLOCKING for s in steps)
    return GuidedChecklist(
        nas_id=int(nas_id),
        operation=operation,
        operation_label_ar=_OP_LABELS_AR.get(operation,
                                              operation),
        steps=tuple(steps),
        can_proceed=not blocking,
        apply_href=apply.href,
    )


__all__ = [
    "GuidedChecklist", "Step",
    "ALL_OPERATIONS",
    "OP_PROGRAMMING_HOTSPOT", "OP_PROGRAMMING_PPPOE",
    "OP_UNPROGRAMMING", "OP_RESTORE", "OP_BACKUP_SAVE",
    "STEP_OK", "STEP_INFO", "STEP_WARNING", "STEP_BLOCKING",
    "build_checklist",
]
