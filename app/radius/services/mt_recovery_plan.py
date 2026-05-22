"""mt_recovery_plan — O8 read-only recovery plan derivation.

Composes a recovery plan from existing data sources:
  audit_log row (the failed/partial event itself) +
  router_backups (nearest pre-operation backup) +
  jobs (if the event was driven by a tracked job).

No automation. No rollback executor. The plan tells the
operator WHAT to do; doing it is still manual.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..db.repos import (
    audit_repo, jobs_repo, router_backups_repo,
)


@dataclass
class RecoveryPlan:
    audit_id: int
    router_id: int | None
    operation: str
    actor: str
    timestamp: str
    result_status: str
    risk_label_ar: str
    what_changed_ar: str
    suggested_steps_ar: list[str] = field(default_factory=list)
    nearest_backup: dict | None = None
    related_job: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "router_id": self.router_id,
            "operation": self.operation,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "result_status": self.result_status,
            "risk_label_ar": self.risk_label_ar,
            "what_changed_ar": self.what_changed_ar,
            "suggested_steps_ar": list(self.suggested_steps_ar),
            "nearest_backup": dict(self.nearest_backup)
                              if self.nearest_backup else None,
            "related_job": dict(self.related_job)
                           if self.related_job else None,
        }


def _suggested_steps(action: str, result_status: str) -> list[str]:
    """Step list per (action, status) — most-specific first."""
    if not result_status:
        return []
    r = (result_status or "").lower()
    steps: list[str] = []
    if r == "partial":
        if action.startswith("mt.programming."):
            kind = action.split(".")[2]   # hotspot / pppoe
            steps.append(
                "افتح صفحة برمجة الشبكة على الراوتر.")
            steps.append(
                f"اضغط زر التراجع/Unprogram لإزالة كل الكائنات "
                f"التي حملت comment=hoberadius:{kind}.")
            steps.append(
                "أعد فحص الواجهة + الـ CIDR + الـ pool في "
                "النموذج، ثم أعد التطبيق.")
            steps.append(
                "تحقّق من سجل العمليات بعد إعادة التطبيق.")
        else:
            steps.append("افتح تفاصيل العملية في سجل العمليات.")
            steps.append("صحّح المسبب الموضّح في رسالة الخطأ.")
            steps.append("أعد المحاولة.")
    elif r == "failed":
        if action.startswith("mt.programming."):
            steps.append(
                "افتح تفاصيل العملية + رسالة الخطأ.")
            steps.append(
                "تأكد من أن الراوتر متصل (تشغيل تشخيص).")
            steps.append(
                "صحّح المدخلات (interface / CIDR / pool / "
                "gateway) قبل إعادة التطبيق.")
        elif action == "mt.backup.save":
            steps.append(
                "تأكّد من اتصال الراوتر + صلاحيات /file لحساب "
                "الـ API.")
            steps.append("جرّب أخذ النسخة من /admin/radius/mt/"
                          "<id>/backups مرة أخرى.")
        elif action == "mt.login_designer.deploy":
            steps.append(
                "تأكّد من أن الـ hotspot package مفعَّل على "
                "الراوتر.")
            steps.append("أعد محاولة النشر.")
        else:
            steps.append("افتح تفاصيل العملية للحصول على رسالة "
                          "الخطأ الدقيقة.")
            steps.append("صحّح السبب وأعد المحاولة.")
    return steps


def _what_changed_summary(action: str, result_status: str) -> str:
    r = (result_status or "").lower()
    if r == "partial":
        if action.startswith("mt.programming."):
            return ("بعض أوامر البرمجة طُبِّقت على الراوتر "
                    "والبعض الآخر فشل — الراوتر في حالة جزئية "
                    "غير متّسقة.")
        return "العملية اكتملت جزئيًا."
    if r == "failed":
        return ("العملية فشلت قبل تطبيق أي تغيير دائم. "
                "الراوتر يجب أن يكون في حالته الأصلية.")
    return "—"


def build_plan(*, tenant_id: int, audit_id: int) -> RecoveryPlan | None:
    """Return a RecoveryPlan for one audit row, or None if the
    row doesn't exist / isn't a candidate (success + low severity
    don't need recovery)."""
    row = audit_repo.get_by_id(int(tenant_id), int(audit_id))
    if not row:
        return None
    result_status = (row.get("result_status") or "").lower()
    severity = (row.get("severity") or "").lower()
    # Only failed / partial / critical-severity events are
    # candidates. A "success/info" row needs no recovery.
    if result_status not in {"failed", "partial"} \
            and severity not in {"critical", "warning"}:
        return None

    action = row.get("action") or ""
    router_id = row.get("router_id")
    nearest_backup = None
    if router_id:
        # Find the most recent successful backup BEFORE this
        # audit event — that's the rollback target.
        cands = router_backups_repo.list_for_router(
            int(tenant_id), int(router_id), limit=20)
        ev_ts = row.get("created_at") or ""
        for b in cands:
            if (b.get("status") == "success"
                    and (b.get("created_at") or "") < ev_ts):
                nearest_backup = b
                break

    # Related job (if the audit payload carried job_id).
    import json
    related_job = None
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    jid = payload.get("job_id")
    if jid:
        try:
            related_job = jobs_repo.get(int(jid))
        except Exception:  # noqa: BLE001
            related_job = None

    risk_label = "حرج" if severity == "critical" \
                  else ("تحذير" if severity == "warning" else "")

    return RecoveryPlan(
        audit_id=int(row.get("id") or 0),
        router_id=router_id,
        operation=action,
        actor=row.get("actor") or "—",
        timestamp=row.get("created_at") or "",
        result_status=result_status,
        risk_label_ar=risk_label,
        what_changed_ar=_what_changed_summary(action, result_status),
        suggested_steps_ar=_suggested_steps(action, result_status),
        nearest_backup=nearest_backup,
        related_job=related_job,
    )


__all__ = ["RecoveryPlan", "build_plan"]
