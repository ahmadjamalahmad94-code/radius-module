"""npc_canary_planner — recommend a staged rollout plan based
on the upstream blast-radius assessment.

Pure module: no DB, no Flask, no MikroTik. No execution; this
service only outputs guidance the operator can follow when
the live-apply phase ships.

Output shape:
  recommended_strategy            — full | canary | staged | hold
  steps[]                         — ordered Arabic instructions
  wait_time_recommendation_ar     — text recommendation
  rollback_checkpoint_required    — bool
  recommendation_ar               — one-line headline
"""
from __future__ import annotations
from app.i18n_text import N_

from dataclasses import dataclass, field
from typing import Any

from .npc_blast_radius import BlastRadius


STRATEGY_FULL    = "full"      # apply everywhere at once
STRATEGY_CANARY  = "canary"    # one router first then expand
STRATEGY_STAGED  = "staged"    # multi-step rollout
STRATEGY_HOLD    = "hold"      # do not apply yet


@dataclass(frozen=True)
class CanaryPlan:
    recommended_strategy: str
    steps: tuple[str, ...]
    wait_time_recommendation_ar: str
    rollback_checkpoint_required: bool
    recommendation_ar: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommended_strategy":         self.recommended_strategy,
            "steps":                        list(self.steps),
            "wait_time_recommendation_ar":  self.wait_time_recommendation_ar,
            "rollback_checkpoint_required": bool(self.rollback_checkpoint_required),
            "recommendation_ar":            self.recommendation_ar,
        }


# ─── Reusable step templates ─────────────────────────────────


_STEP_BACKUP = (
    N_("خذ نسخة احتياطية كاملة من إعدادات الراوتر الهدف قبل "
    "أي تعديل.")
)
_STEP_APPLY_ONE = (
    N_("طبِّق السكربت على راوتر اختبار واحد فقط.")
)
_STEP_WAIT_SHORT = (
    N_("انتظر بين 5 و 10 دقائق وراقب الاتصال + شكاوى المستخدمين.")
)
_STEP_WAIT_LONG = (
    N_("انتظر 15-30 دقيقة وراقب اللوحة الإدارية + إشعارات "
    "المستخدمين قبل التوسعة.")
)
_STEP_ROLLBACK_READY = (
    N_("تأكَّد من جاهزية سكربت rollback في علامة تبويب أخرى — "
    "في حالة الطوارئ ستحتاجه فوراً.")
)
_STEP_EXPAND_GRADUAL = (
    N_("وسِّع التطبيق على مجموعة صغيرة من الراوترات (2-3) أوّلاً.")
)
_STEP_EXPAND_FULL = (
    N_("بعد التحقق من سلامة المجموعة الأولى، طبِّق على بقيّة "
    "الراوترات.")
)
_STEP_RECORD_BASELINE = (
    N_("سجِّل قيم أساس (مستخدمين متّصلين / استهلاك مسار حركة) "
    "قبل التطبيق كي تتمكّن من المقارنة بعدها.")
)


# ─── Public API ──────────────────────────────────────────────


def plan(*, blast: BlastRadius) -> CanaryPlan:
    """Map a BlastRadius bucket to a rollout strategy.

    Single-router policies (`small`) can apply directly but
    still get the backup + rollback-ready checklist. Larger
    blast radii force a multi-step rollout."""
    bucket = blast.blast_radius

    if bucket == "critical":
        return CanaryPlan(
            recommended_strategy=STRATEGY_HOLD,
            steps=(
                _STEP_BACKUP,
                _STEP_ROLLBACK_READY,
                _STEP_RECORD_BASELINE,
                (
                    N_("لا تطبَّق هذه السياسة الآن. ابدأ بسياسة "
                    "بنطاق أضيق، أو راجع ال‌planner لتقليل عدد "
                    "الراوترات الهدف.")
                ),
                (
                    N_("إذا قرَّر فريق العمليّات المتابعة رغم ذلك، "
                    "اتبَع خطوات الـ canary ولا تطبَّق على أكثر "
                    "من راوتر واحد في المرحلة الأولى.")
                ),
            ),
            wait_time_recommendation_ar=(
                N_("ساعة على الأقل بين كل توسعة وأخرى، مع مراجعة "
                "الاتصال على عدد عيِّنة من الزبائن.")
            ),
            rollback_checkpoint_required=True,
            recommendation_ar=(
                N_("النطاق حرج — يُنصح بشدّة بتأجيل التطبيق وإعادة "
                "التخطيط.")
            ),
        )

    if bucket == "large":
        return CanaryPlan(
            recommended_strategy=STRATEGY_CANARY,
            steps=(
                _STEP_BACKUP,
                _STEP_APPLY_ONE,
                _STEP_WAIT_LONG,
                _STEP_ROLLBACK_READY,
                _STEP_EXPAND_GRADUAL,
                _STEP_WAIT_LONG,
                _STEP_EXPAND_FULL,
            ),
            wait_time_recommendation_ar=(
                N_("15-30 دقيقة بين كل مرحلة، أطول إن كان عدد "
                "المستخدمين عالياً في وقت الذروة.")
            ),
            rollback_checkpoint_required=True,
            recommendation_ar=(
                N_("نطاق واسع — طبِّق على راوتر واحد أوّلاً ثم وسِّع "
                "تدريجياً.")
            ),
        )

    if bucket == "medium":
        return CanaryPlan(
            recommended_strategy=STRATEGY_STAGED,
            steps=(
                _STEP_BACKUP,
                _STEP_RECORD_BASELINE,
                _STEP_APPLY_ONE,
                _STEP_WAIT_SHORT,
                _STEP_ROLLBACK_READY,
                _STEP_EXPAND_FULL,
            ),
            wait_time_recommendation_ar=(
                N_("5-10 دقائق بين الراوتر الأوّل وبقيّة الراوترات.")
            ),
            rollback_checkpoint_required=True,
            recommendation_ar=(
                N_("نطاق متوسط — تطبيق مرحَلتين مع فترة مراقبة بينهما.")
            ),
        )

    # small
    return CanaryPlan(
        recommended_strategy=STRATEGY_FULL,
        steps=(
            _STEP_BACKUP,
            _STEP_ROLLBACK_READY,
            (
                N_("طبِّق السكربت مباشرة — النطاق محصور بهذا الراوتر.")
            ),
            _STEP_WAIT_SHORT,
        ),
        wait_time_recommendation_ar=(
            N_("راقب 5 دقائق بعد التطبيق ثم تابع عملك.")
        ),
        rollback_checkpoint_required=False,
        recommendation_ar=(
            N_("نطاق ضيّق — تطبيق مباشر مقبول مع نسخة احتياطية "
            "وسكربت رجوع جاهز.")
        ),
    )


__all__ = [
    "STRATEGY_FULL", "STRATEGY_CANARY",
    "STRATEGY_STAGED", "STRATEGY_HOLD",
    "CanaryPlan", "plan",
]
