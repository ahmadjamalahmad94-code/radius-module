"""mt_audit_presenter — O4 human-readable audit formatter.

Pure presenter layer over audit_log rows. Maps the stable
`action` codes (mt.programming.hotspot.apply,
mt.login_designer.deploy, mt.backup.save, ...) to natural
Arabic sentences operators understand.

What this is NOT:
  - It does NOT modify audit data.
  - It does NOT decide what to log; that's the audit service.
  - It does NOT format anything beyond display. Raw JSON
    payload remains accessible in audit_log_detail.html.

Unknown action codes fall back to the raw action string with
the actor + timestamp — never crash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TimelineEntry:
    """One row in the per-router activity timeline."""
    audit_id: int
    action: str                 # raw stable code
    actor: str
    headline_ar: str            # operator-facing sentence
    detail_ar: str              # second line (optional)
    severity: str               # info | warning | critical
    result_status: str          # success | failed | partial | ''
    risk_label_ar: str          # ""/تحذير/حرج
    recovery_hint_ar: str       # only when partial/failed
    related_job_id: int | None
    related_backup_id: int | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "action": self.action,
            "actor": self.actor,
            "headline_ar": self.headline_ar,
            "detail_ar": self.detail_ar,
            "severity": self.severity,
            "result_status": self.result_status,
            "risk_label_ar": self.risk_label_ar,
            "recovery_hint_ar": self.recovery_hint_ar,
            "related_job_id": self.related_job_id,
            "related_backup_id": self.related_backup_id,
            "created_at": self.created_at,
        }


# ─── Action → headline templates ─────────────────────────────


def _template(action: str) -> tuple[str, str]:
    """Return (headline_template, detail_template) for an
    action code. {actor} is substituted by the caller. The
    detail line is optional ('' = none)."""
    # Programming
    if action == "mt.programming.hotspot.apply":
        return (
            "{actor} طبّق برمجة Hotspot على الراوتر",
            "إعداد جديد لـ pool + DHCP + hotspot profile.",
        )
    if action == "mt.programming.pppoe.apply":
        return (
            "{actor} طبّق برمجة PPPoE-server على الراوتر",
            "إعداد جديد لـ pool + PPP profile + PPPoE listener.",
        )
    if action == "mt.programming.hotspot.unprogram":
        return (
            "{actor} نفّذ تراجعًا (Unprogram) لـ Hotspot",
            "أزال الكائنات التي تحمل comment=hoberadius:hs.",
        )
    if action == "mt.programming.pppoe.unprogram":
        return (
            "{actor} نفّذ تراجعًا (Unprogram) لـ PPPoE",
            "أزال الكائنات التي تحمل comment=hoberadius:pppoe.",
        )

    # Login designer
    if action == "mt.login_designer.deploy":
        return (
            "{actor} رفع صفحة الدخول (login.html) إلى الراوتر",
            "تم تحديث ملف hotspot/login.html.",
        )
    if action == "mt.login_designer.save":
        return (
            "{actor} حفظ تصميم صفحة الدخول",
            "تغيُّر في القالب أو المتغيّرات — بدون رفع للراوتر.",
        )

    # Backup
    if action == "mt.backup.save":
        return (
            "{actor} حفظ نسخة احتياطية للراوتر",
            "نسخة احتياطية ثنائية مسجَّلة في سجل النسخ.",
        )

    # Devices / fleet ops
    if action in {"mt.devices.toggle", "mt.devices.enabled"}:
        return ("{actor} فعّل/عطّل الراوتر", "")
    if action == "mt.devices.bulk_toggle":
        return ("{actor} نفّذ تفعيلًا/تعطيلًا جماعيًا", "")

    # K-family mutations
    if action == "mt.hotspot.disconnect":
        return ("{actor} قطع جلسة Hotspot", "")
    if action == "mt.ppp.disconnect":
        return ("{actor} قطع جلسة PPP", "")
    if action == "mt.system.reboot":
        return ("{actor} طلب إعادة تشغيل الراوتر", "")
    if action == "mt.system.identity.set":
        return ("{actor} عدّل اسم الراوتر (identity)", "")
    if action == "mt.system.backup.save":
        return ("{actor} طلب /system/backup/save على الراوتر", "")
    if action.startswith("mt.tools."):
        # ping / traceroute / dns-resolve — informational.
        return ("{actor} نفّذ أداة فحص شبكية", action)

    # Fallback: show the raw code so unknown types are still
    # operator-readable without crashing.
    return (f"{{actor}} نفّذ <code>{action}</code>", "")


def _risk_label(severity: str) -> str:
    if severity == "critical":
        return "حرج"
    if severity == "warning":
        return "تحذير"
    return ""


def _recovery_hint(action: str, result_status: str) -> str:
    """Recovery hint for failed/partial outcomes — points the
    operator at the right next step."""
    if not result_status:
        return ""
    r = (result_status or "").lower()
    if r == "partial":
        return (
            "نفّذ Unprogram لإزالة الكائنات الجزئية ثم أعد "
            "محاولة البرمجة بعد التحقق من سبب الفشل."
        )
    if r == "failed":
        if action.startswith("mt.programming."):
            return (
                "افحص رسالة الخطأ في تفاصيل العملية، صحّح "
                "المدخلات (CIDR / pool / interface) ثم أعد "
                "التطبيق."
            )
        if action == "mt.backup.save":
            return (
                "تحقّق من اتصال الراوتر ومن صلاحيات /file على "
                "حساب الـ API ثم أعد المحاولة."
            )
        if action == "mt.login_designer.deploy":
            return (
                "تأكد من أن الـ hotspot package مفعَّل على "
                "الراوتر، ثم أعد النشر."
            )
        return "افتح تفاصيل العملية لمعرفة الخطأ الدقيق."
    return ""


def _related_ids(row: dict) -> tuple[int | None, int | None]:
    """Best-effort extraction of related job/backup ids from
    the audit row's payload. Both audit and the producers store
    these inside `payload_json`, so we read them defensively."""
    import json
    raw = row.get("payload_json") or "{}"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        payload = {}
    job_id = payload.get("job_id")
    backup_id = payload.get("backup_id")
    try:
        job_id = int(job_id) if job_id is not None else None
    except (TypeError, ValueError):
        job_id = None
    try:
        backup_id = int(backup_id) if backup_id is not None else None
    except (TypeError, ValueError):
        backup_id = None
    return job_id, backup_id


# ─── Public API ──────────────────────────────────────────────


def present(row: dict) -> TimelineEntry:
    """Convert one audit_log row dict into a TimelineEntry."""
    action = row.get("action") or ""
    actor = row.get("actor") or "ui"
    severity = (row.get("severity") or "info").lower()
    result_status = (row.get("result_status") or "").lower()
    head_tmpl, detail_tmpl = _template(action)
    headline = head_tmpl.format(actor=actor)
    job_id, backup_id = _related_ids(row)
    return TimelineEntry(
        audit_id=int(row.get("id") or 0),
        action=action,
        actor=actor,
        headline_ar=headline,
        detail_ar=detail_tmpl,
        severity=severity,
        result_status=result_status,
        risk_label_ar=_risk_label(severity),
        recovery_hint_ar=_recovery_hint(action, result_status),
        related_job_id=job_id,
        related_backup_id=backup_id,
        created_at=row.get("created_at") or "",
    )


def present_many(rows: list[dict]) -> list[TimelineEntry]:
    return [present(r) for r in (rows or [])]


__all__ = ["TimelineEntry", "present", "present_many"]
