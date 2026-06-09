"""mt_problems — O3 Operations Problems Center aggregator.

Walks every router in the tenant + composes a list of problems
the operator should see in one place. Pure read; no router
contact. Reuses O1's `build_overview` so we don't re-derive
freshness logic.

Problems are grouped by urgency bucket:
  now   — needs immediate action (state in {risky, offline})
  soon  — needs attention soon (state in {attention})
  info  — informational (state in {unknown}, plus low-severity
          partial-data items)

Each problem item carries:
  router_id, router_name, type, severity, title_ar,
  explanation_ar, suggested_action_ar, suggested_href,
  first_seen, last_seen.

`type` is a stable string identifier used by:
  - filters on the UI (operator narrows the page)
  - O10 topology grouping
  - future O9 alert rules (when this evolves into a
    deduplicated alert pipeline)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..db.connection import db
from .mt_health_score import (
    STATE_ATTENTION, STATE_OFFLINE, STATE_RISKY, STATE_UNKNOWN,
    score_health,
)
from .mt_router_overview import build_overview


# Stable type identifiers — never rename, only add.
PROBLEM_OFFLINE         = "router.offline"
PROBLEM_DISABLED        = "router.disabled"
PROBLEM_SNAPSHOT_STALE  = "snapshot.stale"
PROBLEM_SNAPSHOT_FAILED = "snapshot.failed"
PROBLEM_BACKUP_MISSING  = "backup.missing"
PROBLEM_BACKUP_STALE    = "backup.stale"
PROBLEM_CRITICAL_ALERT  = "alert.critical"
PROBLEM_WARNING_ALERT   = "alert.warning"
PROBLEM_RECENT_FAILURE  = "audit.failure"
PROBLEM_PARTIAL_APPLY   = "audit.partial"


ALL_PROBLEM_TYPES: tuple[str, ...] = (
    PROBLEM_OFFLINE, PROBLEM_DISABLED,
    PROBLEM_SNAPSHOT_STALE, PROBLEM_SNAPSHOT_FAILED,
    PROBLEM_BACKUP_MISSING, PROBLEM_BACKUP_STALE,
    PROBLEM_CRITICAL_ALERT, PROBLEM_WARNING_ALERT,
    PROBLEM_RECENT_FAILURE, PROBLEM_PARTIAL_APPLY,
)


# Severity for sorting + filter chips.
SEV_CRITICAL = "critical"
SEV_WARNING  = "warning"
SEV_INFO     = "info"
_SEV_ORDER = {SEV_CRITICAL: 0, SEV_WARNING: 1, SEV_INFO: 2}


# Urgency bucket per severity.
_BUCKET = {
    SEV_CRITICAL: "now",
    SEV_WARNING:  "soon",
    SEV_INFO:     "info",
}


@dataclass
class Problem:
    router_id: int
    router_name: str
    type: str
    severity: str
    title_ar: str
    explanation_ar: str
    suggested_action_ar: str
    suggested_href: str
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Per-router signal extraction ────────────────────────────


def _problems_for_router(ov) -> list[Problem]:
    """Translate one RouterOverview into zero-or-more Problem
    items. Each signal is independent — multiple problems can
    fire for the same router (e.g. critical alert + missing
    backup)."""
    if ov is None:
        return []
    out: list[Problem] = []
    rid = ov.nas_id
    rname = ov.name or f"#{rid}"
    href_overview = f"/admin/radius/mt/{rid}/overview"

    if not ov.enabled:
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_DISABLED, severity=SEV_INFO,
            title_ar=f"{rname} معطّل",
            explanation_ar="الراوتر معطّل من الإعدادات — "
                            "لا تشغيل عليه حاليًا.",
            suggested_action_ar="فعّله من غرفة العمليات "
                                "إذا كان يجب أن يعمل.",
            suggested_href=href_overview,
        ))
        # When disabled we stop here — other signals are noise.
        return out

    if ov.snapshot_status == "failed":
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_SNAPSHOT_FAILED, severity=SEV_CRITICAL,
            title_ar=f"{rname} لا يستجيب",
            explanation_ar="آخر محاولة لقراءة بيانات الراوتر فشلت. "
                            "السبب الأشيع بعد إطفاء طويل: ساعة الراوتر "
                            "رجعت للماضي فرفض WireGuard المصافحة القديمة.",
            suggested_action_ar="افتح التشخيص لقائمة الأسباب المرتّبة وحلولها "
                                "(تصحيح الوقت/NTP، تغيّر الآي بي، النفق، الإطفاء).",
            suggested_href=(
                f"/admin/radius/jobs/diagnostics/{rid}"),
            last_seen=ov.snapshot_last_success_at or "",
        ))
    elif ov.snapshot_status == "stale":
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_SNAPSHOT_STALE, severity=SEV_WARNING,
            title_ar=f"{rname} ببيانات قديمة",
            explanation_ar="آخر snapshot قديم — قد يكون "
                            "الراوتر بعيدًا عن التحديث.",
            suggested_action_ar="شغّل تشخيصًا.",
            suggested_href=(
                f"/admin/radius/jobs/diagnostics/{rid}"),
            last_seen=ov.snapshot_last_success_at or "",
        ))

    if ov.backup_status == "missing":
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_BACKUP_MISSING, severity=SEV_WARNING,
            title_ar=f"{rname} بلا نسخة احتياطية",
            explanation_ar="لا توجد نسخة احتياطية مسجَّلة لهذا "
                            "الراوتر — أي تعديل خطر يفقده قابلية "
                            "الاستعادة.",
            suggested_action_ar="خذ نسخة احتياطية الآن.",
            suggested_href=f"/admin/radius/mt/{rid}/backups",
        ))
    elif ov.backup_status == "stale":
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_BACKUP_STALE, severity=SEV_INFO,
            title_ar=f"{rname} نسخة احتياطية قديمة",
            explanation_ar="آخر نسخة احتياطية قديمة.",
            suggested_action_ar="خذ نسخة جديدة اليوم عند أول فرصة.",
            suggested_href=f"/admin/radius/mt/{rid}/backups",
            last_seen=ov.last_backup_at or "",
        ))

    if ov.active_alerts_critical > 0:
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_CRITICAL_ALERT, severity=SEV_CRITICAL,
            title_ar=(f"{rname} يحمل "
                      f"{ov.active_alerts_critical} تنبيهًا حرجًا"),
            explanation_ar="تنبيهات لم تُعالَج من نظام التنبيهات.",
            suggested_action_ar="افتح صفحة التنبيهات الحرجة.",
            suggested_href=(
                f"/admin/radius/alerts?router_id={rid}"
                "&severity=critical"),
        ))
    if ov.active_alerts_warning > 0:
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_WARNING_ALERT, severity=SEV_WARNING,
            title_ar=(f"{rname} يحمل "
                      f"{ov.active_alerts_warning} تنبيهًا تحذيريًا"),
            explanation_ar="تنبيهات تحذيرية مفتوحة.",
            suggested_action_ar="راجع التنبيهات.",
            suggested_href=(
                f"/admin/radius/alerts?router_id={rid}"
                "&severity=warning"),
        ))

    if ov.last_failed_id:
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_RECENT_FAILURE, severity=SEV_WARNING,
            title_ar=f"{rname}: آخر عملية فاشلة",
            explanation_ar=f"الإجراء «{ov.last_failed_action}» "
                            "فشل آخر مرة.",
            suggested_action_ar="افتح تفاصيل العملية في سجل العمليات.",
            suggested_href=(
                f"/admin/radius/audit/{ov.last_failed_id}"),
            last_seen=ov.last_failed_at or "",
        ))

    if (ov.last_audit_result or "").lower() == "partial":
        out.append(Problem(
            router_id=rid, router_name=rname,
            type=PROBLEM_PARTIAL_APPLY, severity=SEV_CRITICAL,
            title_ar=f"{rname}: تطبيق جزئي يحتاج تراجع",
            explanation_ar=("آخر برمجة طُبِّقت جزئيًا — حالة "
                             "غير متّسقة على الراوتر."),
            suggested_action_ar=("شغّل Unprogram لإزالة الكائنات "
                                  "التي حملت comment="
                                  "hoberadius:* ثم أعد المحاولة."),
            suggested_href=f"/admin/radius/mt/{rid}/program",
            last_seen=ov.last_audit_at or "",
        ))

    return out


# ─── Tenant-wide aggregator ──────────────────────────────────


def _list_routers(tenant_id: int) -> list[dict]:
    rows = db().execute(
        "SELECT id FROM nas_devices "
        "WHERE tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='') "
        "ORDER BY id",
        (int(tenant_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def build_problems(
    tenant_id: int, *,
    router_id: int | None = None,
    severity: str | None = None,
    type: str | None = None,
) -> dict[str, Any]:
    """Walk routers + compose problem list, grouped + filtered.

    Returns:
        {
          "now":  [Problem, ...],   # critical
          "soon": [Problem, ...],   # warning
          "info": [Problem, ...],   # info
          "total": int,
          "filters": {...},
        }
    """
    rows = _list_routers(int(tenant_id))
    if router_id is not None:
        rows = [r for r in rows if int(r["id"]) == int(router_id)]

    items: list[Problem] = []
    for r in rows:
        ov = build_overview(tenant_id=int(tenant_id),
                             nas_id=int(r["id"]))
        items.extend(_problems_for_router(ov))

    # Filter
    if severity:
        items = [p for p in items if p.severity == severity]
    if type:
        items = [p for p in items if p.type == type]

    # Sort by severity (critical first), then router id, then type
    items.sort(key=lambda p: (
        _SEV_ORDER.get(p.severity, 99),
        p.router_id,
        p.type,
    ))

    buckets: dict[str, list[Problem]] = {
        "now": [], "soon": [], "info": [],
    }
    for p in items:
        buckets[_BUCKET.get(p.severity, "info")].append(p)

    return {
        "now":  buckets["now"],
        "soon": buckets["soon"],
        "info": buckets["info"],
        "total": len(items),
        "filters": {
            "router_id": router_id,
            "severity": severity,
            "type": type,
        },
    }


__all__ = [
    "PROBLEM_OFFLINE", "PROBLEM_DISABLED",
    "PROBLEM_SNAPSHOT_STALE", "PROBLEM_SNAPSHOT_FAILED",
    "PROBLEM_BACKUP_MISSING", "PROBLEM_BACKUP_STALE",
    "PROBLEM_CRITICAL_ALERT", "PROBLEM_WARNING_ALERT",
    "PROBLEM_RECENT_FAILURE", "PROBLEM_PARTIAL_APPLY",
    "ALL_PROBLEM_TYPES",
    "SEV_CRITICAL", "SEV_WARNING", "SEV_INFO",
    "Problem", "build_problems",
]
