"""npc_recommendations — rule-based recommendations that
combine the upstream intelligence outputs into an ordered list
of action items.

Pure module: no DB, no Flask, no MikroTik. No machine learning,
no external APIs, no fake AI claims. Every recommendation
comes from a hand-curated rule that fires on observable signals
from the prior phases:

  * Impact risk_level (Phase A)
  * Conflict severity (Phase B)
  * Dependency hints (Phase C)
  * Blast radius bucket (Phase D)
  * Canary strategy (Phase G)
  * Rollback availability (Phase A)

Output: a list of `Recommendation` items with:
  * title_ar           — short headline
  * explanation_ar     — operator-facing rationale
  * action_type        — taxonomy (e.g. "create_rollback")
  * confidence         — high | medium | low
  * related_policy_id  — int or None (for conflict-driven recs)
  * priority           — 1 (highest) .. 5 (lowest)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .npc_blast_radius import BlastRadius
from .npc_canary_planner import CanaryPlan, STRATEGY_CANARY, STRATEGY_HOLD
from .npc_conflict_detector import ConflictAnalysis
from .npc_dependency_detector import DependencyAnalysis
from .npc_impact_analyzer import ImpactAnalysis


CONFIDENCE_HIGH   = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW    = "low"


# Action taxonomy — operators can route to UI flows by this
# field. Keep them short, stable, snake_case.
ACTION_CANARY_FIRST       = "canary_first"
ACTION_CREATE_ROLLBACK    = "create_rollback"
ACTION_RESOLVE_CONFLICT   = "resolve_conflict"
ACTION_REVIEW_DEPS        = "review_dependencies"
ACTION_HOLD_AND_REPLAN    = "hold_and_replan"
ACTION_ADD_RELATED_DOMS   = "add_related_domains"
ACTION_LIMIT_SCOPE        = "limit_scope"
ACTION_ADD_EXPIRY         = "add_expiry"


@dataclass(frozen=True)
class Recommendation:
    title_ar: str
    explanation_ar: str
    action_type: str
    confidence: str
    priority: int
    related_policy_id: Optional[int] = None
    related_domains: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "title_ar":          self.title_ar,
            "explanation_ar":    self.explanation_ar,
            "action_type":       self.action_type,
            "confidence":        self.confidence,
            "priority":          int(self.priority),
            "related_policy_id": self.related_policy_id,
            "related_domains":   list(self.related_domains),
        }


@dataclass(frozen=True)
class RecommendationSet:
    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommendations":
                [r.as_dict() for r in self.recommendations],
        }


# ─── Public API ──────────────────────────────────────────────


def build(
    *,
    impact: ImpactAnalysis,
    conflicts: ConflictAnalysis,
    dependencies: DependencyAnalysis,
    blast: BlastRadius,
    canary: CanaryPlan,
    policy_type: str = "",
    policy: Optional[dict] = None,
) -> RecommendationSet:
    """Combine the upstream intelligence into an ordered set
    of recommendations. Pure — no IO."""
    recs: list[Recommendation] = []

    # ── 1. Hold-and-replan when canary says hold ───────
    if canary.recommended_strategy == STRATEGY_HOLD:
        recs.append(Recommendation(
            title_ar="أوقف التطبيق وأعِد التخطيط",
            explanation_ar=(
                "نطاق التأثير حرج جداً — راجع السياسة وحاول "
                "تقليل عدد الراوترات أو فترة التطبيق قبل "
                "المتابعة."
            ),
            action_type=ACTION_HOLD_AND_REPLAN,
            confidence=CONFIDENCE_HIGH,
            priority=1,
        ))

    # ── 2. Canary-first when blast says so ─────────────
    elif canary.recommended_strategy == STRATEGY_CANARY:
        recs.append(Recommendation(
            title_ar="ابدأ بـ Canary على راوتر واحد",
            explanation_ar=(
                "النطاق واسع — طبِّق على راوتر واحد، انتظر "
                f"({canary.wait_time_recommendation_ar}) ثم "
                "وسِّع التطبيق تدريجياً."
            ),
            action_type=ACTION_CANARY_FIRST,
            confidence=CONFIDENCE_HIGH,
            priority=2,
        ))

    # ── 3. Missing rollback → high-priority warning ────
    if not impact.rollback_available:
        recs.append(Recommendation(
            title_ar="أنشئ خطة تراجع",
            explanation_ar=(
                "لا يوجد سكربت rollback لهذه السياسة — تأكَّد "
                "من إنتاج خطة تراجع يدوية قبل التطبيق، أو راجع "
                "إعدادات السياسة لتفعيل التراجع التلقائي."
            ),
            action_type=ACTION_CREATE_ROLLBACK,
            confidence=CONFIDENCE_HIGH,
            priority=1,
        ))

    # ── 4. Conflict-driven recommendations ─────────────
    if conflicts.has_conflicts:
        for c in conflicts.conflicts:
            # One recommendation per conflict; priority bumps
            # with severity.
            prio = (1 if c.severity == "high"
                    else 2 if c.severity == "medium" else 3)
            confidence = (CONFIDENCE_HIGH if c.severity == "high"
                          else CONFIDENCE_MEDIUM)
            recs.append(Recommendation(
                title_ar=f"حلّ التعارض مع «{c.policy_name}»",
                explanation_ar=(
                    f"{c.reason_ar} — {c.recommendation_ar}"
                ),
                action_type=ACTION_RESOLVE_CONFLICT,
                confidence=confidence,
                priority=prio,
                related_policy_id=c.policy_id,
            ))

    # ── 5. Dependency review when there are hints ─────
    if dependencies.dependencies:
        # Collect all related domains so the UI can render a
        # single combined chip list.
        domains: list[str] = []
        for d in dependencies.dependencies:
            for rd in d.related_domains:
                if rd and rd not in domains:
                    domains.append(rd)
        recs.append(Recommendation(
            title_ar="راجع التبعيّات المُكتشَفة",
            explanation_ar=(
                "النظام اكتشف عائلة خدمات قد تتأثّر بهذه "
                "السياسة. راجع القائمة وقرِّر إن كنت تريد إضافة "
                "النطاقات المرتبطة قبل التطبيق."
            ),
            action_type=ACTION_REVIEW_DEPS,
            confidence=CONFIDENCE_MEDIUM,
            priority=3,
            related_domains=tuple(domains[:10]),
        ))
        # When dependencies are CERTAIN we ALSO suggest adding
        # them — explicit action, not just review.
        certain = [d for d in dependencies.dependencies
                   if d.confidence == "certain"]
        if certain and policy_type == "web_block":
            sample_domains: list[str] = []
            for d in certain:
                for rd in d.related_domains:
                    if rd:
                        sample_domains.append(rd)
            recs.append(Recommendation(
                title_ar=(
                    "أضف النطاقات المرتبطة بثقة عالية"
                ),
                explanation_ar=(
                    "بعض التبعيّات معروفة بثقة كاملة — يُنصح "
                    "بإضافتها إلى قائمة الحظر حتى يكون التطبيق "
                    "مكتملاً."
                ),
                action_type=ACTION_ADD_RELATED_DOMS,
                confidence=CONFIDENCE_HIGH,
                priority=3,
                related_domains=tuple(sample_domains[:10]),
            ))

    # ── 6. Limit scope for very high blast ─────────────
    if blast.blast_radius in ("large", "critical"):
        recs.append(Recommendation(
            title_ar="قلِّل نطاق التطبيق",
            explanation_ar=(
                "النطاق واسع — انظر إن كان بالإمكان تقسيم "
                "السياسة إلى سياسات أصغر لكل مجموعة من "
                "الراوترات."
            ),
            action_type=ACTION_LIMIT_SCOPE,
            confidence=CONFIDENCE_MEDIUM,
            priority=2,
        ))

    # ── 7. Remote-access: missing expiry note ─────────
    if (policy_type == "remote_access" and policy is not None):
        if not (policy.get("expires_at") or "").strip():
            recs.append(Recommendation(
                title_ar="حدِّد وقت انتهاء صلاحية تلقائي",
                explanation_ar=(
                    "هذه سياسة وصول بعيد — تركها بدون وقت "
                    "انتهاء يفتح المنفذ إلى أجل غير مسمّى. "
                    "يُنصح بضبط expires_at حتى تُحذف القاعدة "
                    "تلقائياً."
                ),
                action_type=ACTION_ADD_EXPIRY,
                confidence=CONFIDENCE_HIGH,
                priority=2,
            ))

    # ── Sort by priority then title for stable output ──
    recs.sort(key=lambda r: (r.priority, r.title_ar))
    return RecommendationSet(recommendations=tuple(recs))


__all__ = [
    "CONFIDENCE_HIGH", "CONFIDENCE_MEDIUM", "CONFIDENCE_LOW",
    "ACTION_CANARY_FIRST", "ACTION_CREATE_ROLLBACK",
    "ACTION_RESOLVE_CONFLICT", "ACTION_REVIEW_DEPS",
    "ACTION_HOLD_AND_REPLAN", "ACTION_ADD_RELATED_DOMS",
    "ACTION_LIMIT_SCOPE", "ACTION_ADD_EXPIRY",
    "Recommendation", "RecommendationSet",
    "build",
]
