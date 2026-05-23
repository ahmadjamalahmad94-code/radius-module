"""npc_blast_radius — estimate how many devices/users an NPC
policy would touch on apply.

Pure module: no DB, no Flask, no MikroTik. The caller passes
in counts already known from the local repos (active hotspot
sessions, hotspot profile users, etc.) — this module just
applies the heuristic.

Output shape:
  affected_router_count       — int, exact
  estimated_user_count        — int or None (None = unknown)
  estimated_profile_count     — int or None
  blast_radius                — small | medium | large | critical
  recommendation_ar           — operator-facing guidance
  heuristic_note_ar           — disclaimer that estimates are
                                 not real-time user counts

Heuristic:
  small    — exactly 1 router, no forward-chain drop.
  medium   — 2–N routers, OR 1 router + forward-chain drop.
  large    — `all_routers_targeted=True`.
  critical — `all_routers_targeted=True` AND forward-chain drop.
             (catastrophic — operator should NEVER apply
              without a canary first.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .npc_script_renderer import ScriptPlan


BLAST_SMALL    = "small"
BLAST_MEDIUM   = "medium"
BLAST_LARGE    = "large"
BLAST_CRITICAL = "critical"


@dataclass(frozen=True)
class BlastRadius:
    affected_router_count: int
    estimated_user_count: Optional[int]
    estimated_profile_count: Optional[int]
    blast_radius: str
    recommendation_ar: str
    heuristic_note_ar: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "affected_router_count":    int(self.affected_router_count),
            "estimated_user_count":     self.estimated_user_count,
            "estimated_profile_count":  self.estimated_profile_count,
            "blast_radius":             self.blast_radius,
            "recommendation_ar":        self.recommendation_ar,
            "heuristic_note_ar":        self.heuristic_note_ar,
        }


def _has_forward_drop(plan: ScriptPlan) -> bool:
    for c in plan.filter_ops:
        if (c.kind == "add"
            and c.attrs.get("chain") == "forward"
            and c.attrs.get("action") == "drop"):
            return True
    return False


def _has_input_chain(plan: ScriptPlan) -> bool:
    for c in plan.filter_ops:
        if c.kind == "add" and c.attrs.get("chain") == "input":
            return True
    return False


_NOTE_DEFAULT = (
    "هذه أرقام تقديريّة بناءً على بيانات قاعدة البيانات "
    "المحليّة فقط — قد لا تعكس عدد المستخدمين المتّصلين فعلياً "
    "في هذه اللحظة."
)


def analyze(
    *,
    policy_type: str,
    plan: ScriptPlan,
    affected_router_count: int = 1,
    estimated_user_count: Optional[int] = None,
    estimated_profile_count: Optional[int] = None,
    all_routers_targeted: bool = False,
) -> BlastRadius:
    """Compute the blast-radius report.

    `affected_router_count` should reflect the number of
    routers the policy will actually touch. For now NPC always
    targets one router per policy, so the default is 1; the
    caller can override (e.g. when a future "policy group"
    feature lands).
    """
    forward_drop = _has_forward_drop(plan)
    input_touch  = _has_input_chain(plan)

    # ── Pick the bucket ──
    if all_routers_targeted and forward_drop:
        bucket = BLAST_CRITICAL
        rec = (
            "هذه السياسة قد تقطع الإنترنت لكل المستخدمين على كل "
            "الراوترات. لا تطبَّق مباشرة — اختبرها على راوتر واحد "
            "ثم وسِّع التطبيق تدريجياً (canary)."
        )
    elif all_routers_targeted:
        bucket = BLAST_LARGE
        rec = (
            "السياسة تستهدف كل الراوترات. ابدأ بتطبيقها على "
            "راوتر واحد أوّلاً قبل التوسعة."
        )
    elif affected_router_count >= 5:
        bucket = BLAST_LARGE
        rec = (
            "السياسة تستهدف عدداً كبيراً من الراوترات. خطِّط "
            "للتطبيق على مراحل."
        )
    elif affected_router_count > 1 or forward_drop:
        bucket = BLAST_MEDIUM
        rec = (
            "نطاق متوسط — راجع تأثير السياسة على المستخدمين "
            "ضمن هذه الراوترات قبل التطبيق."
        )
    else:
        bucket = BLAST_SMALL
        rec = (
            "نطاق ضيّق — التغيير محصور بهذا الراوتر فقط."
        )

    # Append context lines to the recommendation.
    if input_touch and bucket != BLAST_SMALL:
        rec += (
            " ملاحظة: السياسة تعدّل سلسلة input — تأكَّد من "
            "بقاء قناة إدارية ثانية متاحة قبل التطبيق."
        )

    return BlastRadius(
        affected_router_count=int(affected_router_count),
        estimated_user_count=(
            int(estimated_user_count)
            if estimated_user_count is not None else None
        ),
        estimated_profile_count=(
            int(estimated_profile_count)
            if estimated_profile_count is not None else None
        ),
        blast_radius=bucket,
        recommendation_ar=rec,
        heuristic_note_ar=_NOTE_DEFAULT,
    )


__all__ = [
    "BLAST_SMALL", "BLAST_MEDIUM", "BLAST_LARGE", "BLAST_CRITICAL",
    "BlastRadius", "analyze",
]
