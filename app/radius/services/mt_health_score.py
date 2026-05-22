"""mt_health_score — O2 deterministic router health scoring.

Pure function. Inputs: O1's `RouterOverview`. Output: a
`HealthScore` dataclass with state, numeric score, ordered
reasons, and a single recommended action.

No live router contact. No DB access. Deterministic — the same
overview always yields the same score. This is the only place
where the operational signals (snapshot freshness, backup age,
critical alerts, recent failures, dangerous-interface flag,
partial-apply state, router scope) get folded into one verdict.

States in increasing-severity order:
  healthy   — all signals green
  attention — at least one yellow signal (e.g. stale backup)
  risky     — at least one red signal but not offline
  offline   — connectivity failed or router disabled
  unknown   — not enough data (no snapshot ever, no alerts row,
              new router)

Numeric score is 0..100. The score is for the UI bar; the
*state* is the operational decision. Thresholds are constants
at the top of the module — tweak there, not in templates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mt_router_overview import (
    BACKUP_FRESH_SEC, BACKUP_STALE_SEC,
    SNAPSHOT_FRESH_SEC, SNAPSHOT_STALE_SEC,
    RouterOverview,
)


# ─── States + thresholds ─────────────────────────────────────


STATE_HEALTHY   = "healthy"
STATE_ATTENTION = "attention"
STATE_RISKY     = "risky"
STATE_OFFLINE   = "offline"
STATE_UNKNOWN   = "unknown"

ALL_STATES = (STATE_HEALTHY, STATE_ATTENTION, STATE_RISKY,
              STATE_OFFLINE, STATE_UNKNOWN)

# Severity ladder for `_worse(a, b)`. Lower = better.
_ORDER = {
    STATE_HEALTHY:   0,
    STATE_UNKNOWN:   1,   # cautious-default, not bad
    STATE_ATTENTION: 2,
    STATE_RISKY:     3,
    STATE_OFFLINE:   4,
}


# Per-signal point deductions. Tuned so:
#   - one critical alert immediately drops to risky (<50)
#   - missing backup alone keeps you in attention (>=65)
#   - failed snapshot is offline (handled by state cap, not points)
_POINTS = {
    "critical_alert":     -25,   # each, capped at -60
    "warning_alert":      -8,
    "stale_snapshot":     -15,
    "missing_snapshot":   -10,
    "stale_backup":       -10,
    "missing_backup":     -15,
    "recent_failure":     -20,
    "partial_apply":      -25,
    "dangerous_interface": -10,
    "disabled":           -100,  # always offline
}


@dataclass
class HealthScore:
    state: str
    score: int                  # 0..100, where 100 = perfect
    reasons: list[str] = field(default_factory=list)
    recommended_action_ar: str = ""
    # Stable identifier for the primary issue. Useful for
    # filtering on the topology page (O10) and for unit tests
    # that don't want to match Arabic.
    primary_signal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "score": self.score,
            "reasons": list(self.reasons),
            "recommended_action_ar": self.recommended_action_ar,
            "primary_signal": self.primary_signal,
        }


# ─── Internals ───────────────────────────────────────────────


def _worse(a: str, b: str) -> str:
    return a if _ORDER.get(a, 0) >= _ORDER.get(b, 0) else b


def _add_reason(out: list[str], text: str) -> None:
    if text and text not in out:
        out.append(text)


# ─── Public API ──────────────────────────────────────────────


def score_health(ov: RouterOverview | None) -> HealthScore:
    """Compute the health verdict for one router overview.

    Tolerates `None` (router doesn't exist or couldn't be loaded)
    by returning an UNKNOWN state instead of raising — the
    caller usually has a 404 path for that case but this keeps
    the function safe to call defensively.
    """
    if ov is None:
        return HealthScore(
            state=STATE_UNKNOWN, score=50,
            reasons=["لا توجد بيانات كافية للراوتر."],
            recommended_action_ar="افتح صفحة الراوتر وحدِّث البيانات.",
            primary_signal="no_data",
        )

    state = STATE_HEALTHY
    score = 100
    reasons: list[str] = []
    primary_signal = ""

    # 1. Disabled → always offline.
    if not ov.enabled:
        state = STATE_OFFLINE
        score += _POINTS["disabled"]   # → 0
        _add_reason(reasons, "الراوتر معطّل من الإعدادات.")
        primary_signal = "disabled"
        return HealthScore(
            state=state, score=max(0, score),
            reasons=reasons,
            recommended_action_ar=(
                "فعّل الراوتر من غرفة العمليات إذا كان يجب أن "
                "يكون نشطًا."
            ),
            primary_signal=primary_signal,
        )

    # 2. Snapshot freshness.
    if ov.snapshot_status == "failed":
        state = _worse(state, STATE_OFFLINE)
        score += _POINTS["missing_snapshot"]
        _add_reason(reasons,
                    "آخر محاولة لتحديث الـ snapshot فشلت.")
        if not primary_signal:
            primary_signal = "snapshot_failed"
    elif ov.snapshot_status == "stale":
        state = _worse(state, STATE_ATTENTION)
        score += _POINTS["stale_snapshot"]
        _add_reason(reasons, "بيانات الراوتر قديمة (snapshot stale).")
        if not primary_signal:
            primary_signal = "snapshot_stale"
    elif ov.snapshot_status == "unknown" and ov.last_audit_id is None:
        # Truly new router — no snapshot AND no audit history.
        state = _worse(state, STATE_UNKNOWN)
        score += _POINTS["missing_snapshot"]
        _add_reason(reasons,
                    "راوتر جديد بلا بيانات تشغيلية بعد.")
        if not primary_signal:
            primary_signal = "no_data"

    # 3. Critical alerts → straight to risky.
    if ov.active_alerts_critical > 0:
        state = _worse(state, STATE_RISKY)
        deduct = max(
            -60, _POINTS["critical_alert"] * ov.active_alerts_critical)
        score += deduct
        _add_reason(reasons,
                    f"{ov.active_alerts_critical} تنبيه حرج مفتوح.")
        if not primary_signal:
            primary_signal = "critical_alert"

    # 4. Warning alerts → attention.
    if ov.active_alerts_warning > 0:
        state = _worse(state, STATE_ATTENTION)
        score += _POINTS["warning_alert"] * ov.active_alerts_warning
        _add_reason(reasons,
                    f"{ov.active_alerts_warning} تنبيه تحذيري.")
        if not primary_signal:
            primary_signal = "warning_alert"

    # 5. Backup.
    if ov.backup_status == "missing":
        state = _worse(state, STATE_ATTENTION)
        score += _POINTS["missing_backup"]
        _add_reason(reasons, "لا توجد نسخة احتياطية لهذا الراوتر.")
        if not primary_signal:
            primary_signal = "missing_backup"
    elif ov.backup_status == "stale":
        state = _worse(state, STATE_ATTENTION)
        score += _POINTS["stale_backup"]
        _add_reason(reasons, "آخر نسخة احتياطية قديمة.")
        if not primary_signal:
            primary_signal = "stale_backup"

    # 6. Recent failure in audit history.
    if ov.last_failed_id:
        state = _worse(state, STATE_ATTENTION)
        score += _POINTS["recent_failure"]
        _add_reason(reasons,
                    f"آخر عملية فاشلة: {ov.last_failed_action}.")
        if not primary_signal:
            primary_signal = "recent_failure"

    # 7. Partial-apply (S4.3 result_status='partial' surfaced
    # via audit last_audit_result).
    if (ov.last_audit_result or "").lower() == "partial":
        state = _worse(state, STATE_RISKY)
        score += _POINTS["partial_apply"]
        _add_reason(reasons,
                    "آخر عملية برمجة طُبِّقت جزئيًا — حالة "
                    "غير متّسقة.")
        if not primary_signal:
            primary_signal = "partial_apply"

    score = max(0, min(100, score))

    # 8. Recommended action — pick by primary signal.
    recommend = _recommend(primary_signal, state)

    return HealthScore(
        state=state, score=score, reasons=reasons,
        recommended_action_ar=recommend,
        primary_signal=primary_signal or "ok",
    )


def _recommend(primary_signal: str, state: str) -> str:
    """Single Arabic next-step. One per signal — the overview
    page's `suggested_actions` already gives the full list."""
    if state == STATE_HEALTHY:
        return "الراوتر صحّي — لا إجراء مطلوب."
    if primary_signal == "disabled":
        return "فعّل الراوتر من الإعدادات."
    if primary_signal in {"snapshot_failed", "snapshot_stale"}:
        return "شغّل تشخيصًا لتحديث snapshot الراوتر."
    if primary_signal == "no_data":
        return "أنشئ snapshot أول عبر تشغيل تشخيص."
    if primary_signal == "critical_alert":
        return "افتح التنبيهات الحرجة وعالجها قبل أي تعديل."
    if primary_signal == "warning_alert":
        return "راجع التنبيهات المفتوحة."
    if primary_signal == "missing_backup":
        return ("خذ نسخة احتياطية قبل أي تعديل خطر — لا توجد "
                "نسخة سابقة.")
    if primary_signal == "stale_backup":
        return "خذ نسخة احتياطية محدّثة."
    if primary_signal == "recent_failure":
        return "افحص تفاصيل آخر عملية فاشلة."
    if primary_signal == "partial_apply":
        return ("نفّذ تراجع/Unprogram للأوامر التي طُبِّقت قبل "
                "إعادة المحاولة.")
    return "راجع لوحة الراوتر للتفاصيل."


__all__ = [
    "STATE_HEALTHY", "STATE_ATTENTION", "STATE_RISKY",
    "STATE_OFFLINE", "STATE_UNKNOWN", "ALL_STATES",
    "HealthScore", "score_health",
]
