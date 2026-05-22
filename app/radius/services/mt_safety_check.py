"""mt_safety_check — O5 pre-execution safety evaluator.

Reusable service called BEFORE any dangerous router action. It
DOES NOT execute anything; it returns an allow/deny verdict +
the evidence so the calling route can:
  - block the action when allowed=False
  - render warnings to the operator when severity≥warning
  - record the check summary in the audit row

Composes from existing O1 (RouterOverview) + O2 (HealthScore)
+ S4.1 (interface classifier) + permission helper.

Verdict shape:
  SafetyCheck(
    allowed=bool,
    severity=info|warning|critical|blocked,
    blocking_reasons=[ar],
    warnings=[ar],
    recommendations=[ar],
    requires_confirmation=bool,
    summary={...}    # for audit payload
  )

Severity ladder (high → low):
  blocked   — must not run
  critical  — run only with explicit override permission
              (mikrotik.admin) AND confirmation
  warning   — run only with confirmation
  info      — run freely

`requires_confirmation` is True for warning + critical paths.
`allowed=False` only when severity == blocked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mt_health_score import (
    STATE_OFFLINE, STATE_RISKY, score_health,
)
from .mt_permissions import (
    PERM_ADMIN, PERM_PROGRAM, admin_permissions,
)
from .mt_router_overview import build_overview


SEV_BLOCKED  = "blocked"
SEV_CRITICAL = "critical"
SEV_WARNING  = "warning"
SEV_INFO     = "info"

_SEV_ORDER = {
    SEV_INFO: 0, SEV_WARNING: 1,
    SEV_CRITICAL: 2, SEV_BLOCKED: 3,
}


@dataclass
class SafetyCheck:
    allowed: bool
    severity: str
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    requires_confirmation: bool = False
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "severity": self.severity,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
            "recommendations": list(self.recommendations),
            "requires_confirmation": self.requires_confirmation,
            "summary": dict(self.summary),
        }


def _worse(a: str, b: str) -> str:
    return a if _SEV_ORDER[a] >= _SEV_ORDER[b] else b


# ─── Public API ──────────────────────────────────────────────


def evaluate(
    *, tenant_id: int, nas_id: int,
    admin=None,
    required_perm: str = PERM_PROGRAM,
    operation: str = "mt.programming.apply",
    override_admin: bool = False,
) -> SafetyCheck:
    """Run the safety check.

    Args:
      tenant_id / nas_id   — router scope.
      admin                — Admin DTO of the operator (or
                              None; absence is treated as
                              blocked).
      required_perm        — what the operator must hold to
                              proceed even on info severity.
      operation            — audit string identifying what's
                              about to run.
      override_admin       — True when the operator explicitly
                              acknowledged a critical-severity
                              risk. Only valid with PERM_ADMIN.
    """
    severity = SEV_INFO
    blocking: list[str] = []
    warnings: list[str] = []
    recs: list[str] = []

    # 1. Permission.
    held = admin_permissions(admin) if admin is not None else set()
    if required_perm not in held:
        return SafetyCheck(
            allowed=False, severity=SEV_BLOCKED,
            blocking_reasons=[
                f"ليست لديك صلاحية {required_perm} لتنفيذ هذه "
                "العملية."
            ],
            warnings=[], recommendations=[
                "اطلب من مدير النظام إضافة الصلاحية لدورك."
            ],
            requires_confirmation=False,
            summary={"reason": "permission_denied",
                     "required_perm": required_perm,
                     "operation": operation},
        )

    # 2. Build overview (also gives us scope).
    ov = build_overview(tenant_id=int(tenant_id), nas_id=int(nas_id))
    if ov is None:
        return SafetyCheck(
            allowed=False, severity=SEV_BLOCKED,
            blocking_reasons=[
                "الراوتر غير موجود في نطاقك."
            ],
            recommendations=[],
            requires_confirmation=False,
            summary={"reason": "scope_or_404",
                     "operation": operation},
        )

    # 3. Disabled / offline router → block.
    if not ov.enabled:
        return SafetyCheck(
            allowed=False, severity=SEV_BLOCKED,
            blocking_reasons=[
                "الراوتر معطّل — لا يمكن تنفيذ عمليات عليه."
            ],
            recommendations=["فعّل الراوتر من غرفة العمليات."],
            requires_confirmation=False,
            summary={"reason": "router_disabled",
                     "operation": operation},
        )

    # 4. Health score evaluation.
    hs = score_health(ov)
    if hs.state == STATE_OFFLINE:
        return SafetyCheck(
            allowed=False, severity=SEV_BLOCKED,
            blocking_reasons=[
                "الراوتر غير متصل — انتظر استعادة الاتصال "
                "قبل المحاولة."
            ],
            recommendations=hs.reasons,
            requires_confirmation=False,
            summary={
                "reason": "router_offline",
                "operation": operation,
                "health_state": hs.state,
                "health_score": hs.score,
            },
        )

    if hs.state == STATE_RISKY:
        # Critical alerts open / partial-apply detected →
        # critical severity. Operators with PERM_ADMIN can
        # override; others can't.
        severity = _worse(severity, SEV_CRITICAL)
        warnings.extend(hs.reasons)
        recs.append(hs.recommended_action_ar)

    # 5. Snapshot freshness — stale ≠ blocked but warning.
    if ov.snapshot_status == "stale":
        severity = _worse(severity, SEV_WARNING)
        warnings.append(
            "بيانات الراوتر قديمة — قد تكون الحالة الحالية "
            "مختلفة عما تراه.")
        recs.append("شغّل تشخيصًا لتحديث snapshot قبل التطبيق.")
    elif ov.snapshot_status == "unknown":
        severity = _worse(severity, SEV_WARNING)
        warnings.append(
            "لا توجد بيانات snapshot — حالة الراوتر غير "
            "مؤكّدة.")
        recs.append("شغّل تشخيصًا قبل المتابعة.")

    # 6. Backup readiness — missing/stale is warning, not block.
    if ov.backup_status == "missing":
        severity = _worse(severity, SEV_WARNING)
        warnings.append(
            "لا توجد نسخة احتياطية — إن فشلت العملية لن تستطيع "
            "الاستعادة.")
        recs.append("خذ نسخة احتياطية قبل أي تعديل خطر.")
    elif ov.backup_status == "stale":
        warnings.append("آخر نسخة احتياطية قديمة.")
        # Stale alone stays info severity.

    # 7. Partial-apply on the same router → critical (the router
    # is in an inconsistent state).
    if (ov.last_audit_result or "").lower() == "partial":
        severity = _worse(severity, SEV_CRITICAL)
        warnings.append(
            "آخر برمجة طُبّقت جزئيًا — حالة الراوتر غير متّسقة.")
        recs.append(
            "نفّذ Unprogram أوّلًا، ثم أعد البرمجة من جديد.")

    # 8. Override handling for critical severity.
    if severity == SEV_CRITICAL:
        if not override_admin:
            return SafetyCheck(
                allowed=False, severity=SEV_BLOCKED,
                blocking_reasons=[
                    "حالة الراوتر حرجة — التنفيذ يحتاج "
                    "تأكيدًا صريحًا من مدير ذي صلاحية "
                    "mikrotik.admin."
                ],
                warnings=warnings, recommendations=recs,
                requires_confirmation=True,
                summary={
                    "reason": "critical_no_override",
                    "operation": operation,
                    "health_state": hs.state,
                    "health_score": hs.score,
                },
            )
        # Override is requested — must hold PERM_ADMIN.
        if PERM_ADMIN not in held:
            return SafetyCheck(
                allowed=False, severity=SEV_BLOCKED,
                blocking_reasons=[
                    "لا تملك صلاحية mikrotik.admin لتجاوز "
                    "حالة حرجة."
                ],
                warnings=warnings, recommendations=recs,
                requires_confirmation=True,
                summary={
                    "reason": "override_denied",
                    "operation": operation,
                },
            )

    # 9. Final verdict.
    requires_confirm = severity in {SEV_WARNING, SEV_CRITICAL}
    return SafetyCheck(
        allowed=True, severity=severity,
        blocking_reasons=[], warnings=warnings,
        recommendations=recs,
        requires_confirmation=requires_confirm,
        summary={
            "operation": operation,
            "health_state": hs.state,
            "health_score": hs.score,
            "snapshot_status": ov.snapshot_status,
            "backup_status": ov.backup_status,
            "override_used": bool(override_admin
                                   and severity == SEV_CRITICAL),
        },
    )


__all__ = [
    "SEV_BLOCKED", "SEV_CRITICAL", "SEV_WARNING", "SEV_INFO",
    "SafetyCheck", "evaluate",
]
