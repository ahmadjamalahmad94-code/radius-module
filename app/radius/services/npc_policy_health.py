"""npc_policy_health — combine the upstream intelligence
outputs into a single 0..100 advisory score + Arabic grade.

Pure module: no DB, no Flask, no MikroTik.

Inputs (the route hands these in — all already produced
upstream in the same preview pass):
  * impact_analysis    — ImpactAnalysis
  * conflict_analysis  — ConflictAnalysis
  * dependency_analysis — DependencyAnalysis
  * blast_radius       — BlastRadius
  * rollback_available — bool

Output:
  score 0..100, grade, positives_ar[], negatives_ar[],
  reasoning_ar, is_advisory=True.

Heuristic — additive starting from 100, deducting per signal.
Signals are weighted by how much the operator should care:

  CRITICAL impact            −60
  HIGH impact                −35
  MEDIUM impact              −15
  Plan has blocking errors   −60   (covered by CRITICAL impact)
  No rollback available      −25
  Conflict severity HIGH     −25
  Conflict severity MEDIUM   −10
  Conflict severity LOW      −2
  Blast radius CRITICAL      −30
  Blast radius LARGE         −18
  Blast radius MEDIUM        −7
  Dependency warning present −5
  Dependencies all CERTAIN   +3  (operator was warned)
  Limited router scope (=1)  +5

The score is clamped to [0, 100]. Grades:
  excellent  ≥ 90
  good       70..89
  caution    50..69
  risky      30..49
  dangerous  < 30
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .npc_blast_radius import BlastRadius
from .npc_conflict_detector import ConflictAnalysis
from .npc_dependency_detector import DependencyAnalysis
from .npc_impact_analyzer import ImpactAnalysis


# ─── Grades ──────────────────────────────────────────────────


GRADE_EXCELLENT = "excellent"
GRADE_GOOD      = "good"
GRADE_CAUTION   = "caution"
GRADE_RISKY     = "risky"
GRADE_DANGEROUS = "dangerous"


def _grade_for(score: int) -> str:
    if score >= 90:
        return GRADE_EXCELLENT
    if score >= 70:
        return GRADE_GOOD
    if score >= 50:
        return GRADE_CAUTION
    if score >= 30:
        return GRADE_RISKY
    return GRADE_DANGEROUS


# ─── Result type ─────────────────────────────────────────────


@dataclass(frozen=True)
class HealthScore:
    score: int                # 0..100
    grade: str
    positives_ar: tuple[str, ...] = field(default_factory=tuple)
    negatives_ar: tuple[str, ...] = field(default_factory=tuple)
    reasoning_ar: str = ""
    is_advisory: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "score":        int(self.score),
            "grade":        self.grade,
            "positives_ar": list(self.positives_ar),
            "negatives_ar": list(self.negatives_ar),
            "reasoning_ar": self.reasoning_ar,
            "is_advisory":  bool(self.is_advisory),
        }


# ─── Score computation ───────────────────────────────────────


def compute(
    *,
    impact: ImpactAnalysis,
    conflicts: ConflictAnalysis,
    dependencies: DependencyAnalysis,
    blast: BlastRadius,
    rollback_available: Optional[bool] = None,
    canary_recommended: bool = False,
) -> HealthScore:
    """Combine the inputs into a single advisory score.

    `rollback_available` falls back to `impact.rollback_available`
    when not supplied — that's the same source of truth the
    impact analyzer used."""
    score = 100
    positives: list[str] = []
    negatives: list[str] = []

    # ── Impact ────────────────────────────────────────────
    if impact.risk_level == "critical":
        score -= 60
        negatives.append(
            "تحليل الأثر يصنّف هذه السياسة كـ critical."
        )
    elif impact.risk_level == "high":
        score -= 35
        negatives.append(
            "تحليل الأثر يصنّف هذه السياسة كـ high."
        )
    elif impact.risk_level == "medium":
        score -= 15
        negatives.append(
            "تحليل الأثر يصنّف هذه السياسة كـ medium."
        )
    else:
        positives.append("تحليل الأثر يصنّف الخطّة كـ low.")

    # ── Rollback availability ────────────────────────────
    if rollback_available is None:
        rollback_available = impact.rollback_available
    if rollback_available:
        positives.append("سكربت rollback متاح ومرجعنا انعكاسي.")
    else:
        score -= 25
        negatives.append(
            "لا يوجد سكربت rollback — التراجع سيتطلّب تدخّلاً يدوياً."
        )

    # ── Conflicts ────────────────────────────────────────
    if conflicts.has_conflicts:
        if conflicts.severity == "high":
            score -= 25
        elif conflicts.severity == "medium":
            score -= 10
        else:
            score -= 2
        negatives.append(
            f"تم اكتشاف {len(conflicts.conflicts)} تعارض(ات) "
            f"مع سياسات أخرى — مستوى الخطر "
            f"{conflicts.severity}."
        )
    else:
        positives.append("لا تعارض مع السياسات الأخرى.")

    # ── Blast radius ─────────────────────────────────────
    if blast.blast_radius == "critical":
        score -= 30
        negatives.append("نطاق التأثير حرج (critical).")
    elif blast.blast_radius == "large":
        score -= 18
        negatives.append("نطاق التأثير واسع (large).")
    elif blast.blast_radius == "medium":
        score -= 7
        negatives.append("نطاق التأثير متوسّط.")
    else:
        positives.append("نطاق التأثير ضيّق.")

    # ── Single-router bonus ──────────────────────────────
    if blast.affected_router_count == 1 \
            and blast.blast_radius == "small":
        score += 5
        positives.append("التأثير محدود براوتر واحد فقط.")

    # ── Dependencies ─────────────────────────────────────
    if dependencies.dependencies:
        # Operator was warned — small deduction so they at
        # least look at the list, plus a partial offset when
        # all dependencies are CERTAIN (less uncertain).
        score -= 5
        if all(
            d.confidence == "certain"
            for d in dependencies.dependencies
        ):
            score += 3
            positives.append(
                "كل التبعيّات معروفة بثقة عالية."
            )
        negatives.append(
            "هناك تبعيّات قد تحتاج مراجعة قبل التطبيق."
        )

    # ── Canary recommendation (optional from caller) ─────
    if canary_recommended:
        positives.append(
            "هناك توصية بالتطبيق التدريجي (canary)."
        )

    # ── Clamp ───────────────────────────────────────────
    if score > 100:
        score = 100
    if score < 0:
        score = 0

    grade = _grade_for(score)
    reasoning = (
        f"الدرجة {score}/100 — {grade}. هذه قيمة استشارية فقط "
        "وليست بديلاً عن مراجعة المسؤول البشري قبل التطبيق."
    )

    return HealthScore(
        score=int(score),
        grade=grade,
        positives_ar=tuple(positives),
        negatives_ar=tuple(negatives),
        reasoning_ar=reasoning,
        is_advisory=True,
    )


__all__ = [
    "GRADE_EXCELLENT", "GRADE_GOOD", "GRADE_CAUTION",
    "GRADE_RISKY", "GRADE_DANGEROUS",
    "HealthScore", "compute",
]
