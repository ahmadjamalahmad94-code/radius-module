"""npc_impact_analyzer — translate a generated NPC ScriptPlan
into a beginner-friendly Arabic impact summary.

Pure module: no DB, no Flask, no MikroTik. Tests assert this.

Consumed by the NPC preview API to surface, alongside the raw
RouterOS script, a structured `ImpactAnalysis` object that an
Arabic-first operator can read without knowing what `mangle`
or `place-before=0` means.

Detection rules (the dangerous patterns the brief enumerates):
  * `0.0.0.0/0` anywhere in the rendered script
  * input-chain modifications (admin reach)
  * forward-chain `action=drop` (web blocking)
  * `[find comment~"…"]` without our anchored prefix
    (would risk clobbering unmanaged rules)
  * missing rollback while the forward script has commands
  * "all routers" targeting (deferred — left as the caller's
    responsibility to set `policy["all_routers"]=True`)
  * empty target list (web_block / walled_garden)
  * renderer tripped a secret tripwire → CRITICAL

Risk grading:
  low      — managed scope, anchored cleanup, no destructive
              firewall verbs.
  medium   — touches the input chain OR drops traffic on
              forward, but with anchored cleanup + rollback.
  high     — multiple medium signals OR a `0.0.0.0/0` match.
  critical — secret tripwire, or unmanaged deletion pattern.

Output shape (frozen dataclass, JSON-friendly via `as_dict()`):
  summary_ar                — one-line Arabic headline
  beginner_explanation_ar   — multi-sentence layperson body
  technical_summary_ar      — slightly denser; uses MT terms
  affected_services         — tuple of sub-service keys
  affected_router_count     — how many MikroTik boxes the plan
                              would touch (read from policy /
                              hint kwargs)
  change_count              — plan.total_commands
  changes_summary           — dict of per-section counts
  warnings_ar               — list of plan warnings + analyzer
                              warnings
  rollback_available        — bool
  rollback_explanation_ar   — short Arabic explanation
  risk_level                — low|medium|high|critical
  risk_reasons_ar           — list of strings naming the
                              specific triggers

Beginner explanation policy: NEVER emits raw MikroTik syntax
as the only explanation. The raw script is shown separately
in the preview UI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .npc_script_renderer import (
    PlanCommand, RenderSafetyError, ScriptPlan,
)


# ─── Risk levels ─────────────────────────────────────────────


RISK_LOW      = "low"
RISK_MEDIUM   = "medium"
RISK_HIGH     = "high"
RISK_CRITICAL = "critical"

_RISK_ORDER = {
    RISK_LOW: 0, RISK_MEDIUM: 1,
    RISK_HIGH: 2, RISK_CRITICAL: 3,
}


def _escalate(current: str, candidate: str) -> str:
    """Pick the highest of two risk levels."""
    return candidate if _RISK_ORDER[candidate] > _RISK_ORDER[current] else current


# ─── Service-label helpers ───────────────────────────────────


_SERVICE_LABELS_AR = {
    "remote_access": "الوصول البعيد للراوتر",
    "web_block":     "حظر المواقع",
    "walled_garden": "الإستثناءات قبل تسجيل الدخول",
}


def _service_label(service: str) -> str:
    return _SERVICE_LABELS_AR.get(service, service)


# ─── Result type ─────────────────────────────────────────────


@dataclass(frozen=True)
class ImpactAnalysis:
    summary_ar: str
    beginner_explanation_ar: str
    technical_summary_ar: str
    affected_services: tuple[str, ...]
    affected_router_count: int
    change_count: int
    changes_summary: dict[str, int]
    warnings_ar: tuple[str, ...]
    rollback_available: bool
    rollback_explanation_ar: str
    risk_level: str
    risk_reasons_ar: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly projection for the preview API."""
        return {
            "summary_ar":              self.summary_ar,
            "beginner_explanation_ar": self.beginner_explanation_ar,
            "technical_summary_ar":    self.technical_summary_ar,
            "affected_services":       list(self.affected_services),
            "affected_router_count":   int(self.affected_router_count),
            "change_count":            int(self.change_count),
            "changes_summary":         dict(self.changes_summary),
            "warnings_ar":             list(self.warnings_ar),
            "rollback_available":      bool(self.rollback_available),
            "rollback_explanation_ar": self.rollback_explanation_ar,
            "risk_level":              self.risk_level,
            "risk_reasons_ar":         list(self.risk_reasons_ar),
        }


# ─── Detection helpers ───────────────────────────────────────


_RE_BLACKHOLE_CIDR = re.compile(r"\b0\.0\.0\.0/0\b")


def _scan_blackhole(script_text: str) -> bool:
    return bool(_RE_BLACKHOLE_CIDR.search(script_text or ""))


def _touches_input_chain(plan: ScriptPlan) -> bool:
    """`True` if any filter/add command writes to chain=input.
    Remote-access policies do this by design — we flag it so
    the UI can elevate the risk pill."""
    for c in plan.filter_ops:
        if c.kind == "add" and c.attrs.get("chain") == "input":
            return True
    return False


def _has_forward_drop(plan: ScriptPlan) -> bool:
    """`True` when the plan emits at least one
    forward-chain action=drop rule (web-block territory)."""
    for c in plan.filter_ops:
        if (c.kind == "add"
            and c.attrs.get("chain") == "forward"
            and c.attrs.get("action") == "drop"):
            return True
    return False


def _unmanaged_remove_pattern(plan: ScriptPlan) -> bool:
    """Defence-in-depth: scan rollback + cleanup ops for any
    `remove [find comment~"..."]` whose find pattern doesn't
    start with `^HOBE_NPC_`. The renderer already rejects
    unanchored patterns, but the analyzer surfaces the warning
    explicitly so the operator sees the protection."""
    pools = (plan.cleanup_ops, plan.rollback_ops)
    for pool in pools:
        for c in pool:
            if c.kind != "remove":
                continue
            fp = c.find_pattern or ""
            if not fp.startswith("^HOBE_NPC_"):
                return True
    return False


def _no_op(plan: ScriptPlan) -> bool:
    """Plan that has no operator-meaningful adds.

    Cleanup ops are mechanical idempotency — the planner emits
    them on every plan so a re-apply wipes prior managed
    state. They don't represent intended new behaviour, so a
    plan with ONLY cleanup ops is still a no-op from the
    operator's point of view."""
    return not any((
        plan.address_list_ops,
        plan.filter_ops,
        plan.walled_garden_ops,
        plan.scheduler_ops,
    ))


# ─── Public analyzer ─────────────────────────────────────────


def analyze(
    *,
    policy_type: str,
    policy: dict,
    plan: ScriptPlan,
    targets: Optional[Iterable[dict]] = None,
    rendered_forward: Optional[str] = None,
    rendered_rollback: Optional[str] = None,
    render_error: Optional[str] = None,
    affected_router_count: Optional[int] = None,
    all_routers_targeted: bool = False,
) -> ImpactAnalysis:
    """Build an `ImpactAnalysis` for one (policy, plan) pair.

    `rendered_forward` / `rendered_rollback` are passed in so
    the analyzer doesn't have to re-render (and so the secret
    tripwire path can hand in `render_error=` without ever
    holding the bytes).

    `targets` is the iterable of target/entry dicts the planner
    consumed. Optional — used for the human-friendly count.
    """
    reasons: list[str] = []
    warnings: list[str] = list(plan.warnings or ())
    risk = RISK_LOW

    # 1) Renderer tripwire → CRITICAL regardless of plan shape.
    if render_error:
        risk = RISK_CRITICAL
        reasons.append(
            f"رفض المُولِّد توليد السكربت لاحتوائه على بيانات "
            f"حسّاسة: {render_error}"
        )

    fwd = rendered_forward or ""
    rb  = rendered_rollback or ""

    # 2) 0.0.0.0/0 anywhere in the rendered text.
    if _scan_blackhole(fwd):
        risk = _escalate(risk, RISK_HIGH)
        reasons.append(
            "يحتوي السكربت على 0.0.0.0/0 — قد يقطع الإنترنت كاملاً."
        )

    # 3) Input-chain changes.
    if _touches_input_chain(plan):
        risk = _escalate(risk, RISK_MEDIUM)
        reasons.append(
            "السكربت يعدّل سلسلة input (الوصول الإداري للراوتر)."
        )

    # 4) Forward-chain drop.
    if _has_forward_drop(plan):
        risk = _escalate(risk, RISK_MEDIUM)
        reasons.append(
            "السكربت يحذف حركة forward — سيؤثر على المستخدمين."
        )

    # 5) Unmanaged deletion pattern.
    if _unmanaged_remove_pattern(plan):
        risk = _escalate(risk, RISK_CRITICAL)
        reasons.append(
            "هناك أمر حذف بدون البادئة المُدارة — قد يمسّ قواعد "
            "لم نُنشئها (محظور بالفعل من قِبل المُولِّد)."
        )

    # 6) Missing rollback even though the forward has content.
    rollback_available = bool(rb.strip())
    if fwd.strip() and not rollback_available:
        risk = _escalate(risk, RISK_HIGH)
        reasons.append(
            "السكربت لا يحوي خطة تراجع — لن يكون التراجع التلقائي ممكنًا."
        )

    # 7) All-router targeting (caller flag).
    if all_routers_targeted:
        risk = _escalate(risk, RISK_HIGH)
        reasons.append(
            "السياسة تستهدف كل الراوترات (نطاق واسع جداً)."
        )

    # 8) Empty target list — only meaningful for the
    # block / walled-garden services.
    target_count = sum(1 for _ in (targets or ()))
    if policy_type in ("web_block", "walled_garden"):
        if target_count == 0:
            warnings.append(
                "قائمة الوجهات/الإدخالات فارغة — السياسة لن تغيّر "
                "شيئاً عند تطبيقها."
            )

    # 9) No-op plan messaging.
    is_no_op = _no_op(plan)

    # 10) Plan's own blocking errors — surface as critical.
    if plan.blocking_errors:
        risk = _escalate(risk, RISK_CRITICAL)
        for be in plan.blocking_errors:
            reasons.append(f"الخطّة مرفوضة: {be}")

    # Affected router count — caller wins if it passes a hint.
    if affected_router_count is None:
        # `policy["router_id"]` is the single-router fall-back.
        affected_router_count = 1 if policy.get("router_id") else 0

    # ─── Build the explanation strings ─────────────────────

    svc_ar = _service_label(policy_type)

    if render_error:
        summary_ar = (
            f"تم رفض السكربت تلقائياً بسبب بيانات حسّاسة "
            f"— لا يمكن المتابعة."
        )
        beginner = (
            "النظام اكتشف أن السكربت يحوي بيانات يجب ألا تظهر "
            "أبداً في ملف يُعرض على الشاشة (مثل كلمة سر أو "
            "مفتاح خاص). تم إيقاف توليد المعاينة كحماية. "
            "هذا ليس خطأً منك — هذا حاجز أمان متعمَّد."
        )
        technical = (
            f"render aborted with RenderSafetyError: "
            f"{render_error!r}"
        )
    elif is_no_op:
        summary_ar = "السياسة لن تغيّر شيئاً حالياً."
        beginner = (
            f"معاينة سياسة «{policy.get('name') or '—'}» "
            f"({svc_ar}) لا تحوي أوامر جديدة على الراوتر. "
            "إذا كنت تتوقّع تغييراً، أضف بعض الإدخالات أو راجع "
            "إعدادات السياسة ثم أعِد المعاينة."
        )
        technical = (
            f"plan.total_commands=0 across all sections "
            f"({_changes_phrase(plan)})"
        )
    else:
        summary_ar = (
            f"معاينة {svc_ar} — {plan.total_commands} أمر "
            f"على {affected_router_count} راوتر."
        )
        beginner_lines = [
            f"عند تطبيق هذه السياسة، سيقوم النظام بإضافة "
            f"{plan.total_commands} أمر على راوتر "
            f"{affected_router_count}."
        ]
        if policy_type == "remote_access":
            beginner_lines.append(
                "ستفتح هذه السياسة منافذ إدارية على الراوتر "
                "(مثل Winbox أو WebFig)؛ تأكَّد من تحديد عناوين "
                "مصدر أو وقت انتهاء لقفل الفتحة تلقائياً."
            )
        elif policy_type == "web_block":
            beginner_lines.append(
                "ستحجب هذه السياسة قائمة المواقع/العناوين "
                "المحدَّدة عن المستخدمين خلف هذا الراوتر."
            )
        elif policy_type == "walled_garden":
            beginner_lines.append(
                "ستسمح هذه السياسة بالوصول إلى الإدخالات المحدَّدة "
                "قبل تسجيل الدخول في الـ Hotspot."
            )
        if not reasons:
            beginner_lines.append("لم يكتشف النظام أي مخاطر بارزة.")
        beginner = " ".join(beginner_lines)
        technical = (
            f"plan.total_commands={plan.total_commands}; "
            f"sections=({_changes_phrase(plan)})"
        )

    rollback_explanation = (
        "يمكن التراجع عبر سكربت rollback المُولَّد تلقائياً — "
        f"يطابق التعليقات المُدارة فقط ({plan.comment_prefix})."
        if rollback_available else
        "لا يوجد سكربت تراجع — قد يحتاج التراجع إلى تدخّل يدوي."
    )

    return ImpactAnalysis(
        summary_ar=summary_ar,
        beginner_explanation_ar=beginner,
        technical_summary_ar=technical,
        affected_services=(policy_type,),
        affected_router_count=int(affected_router_count),
        change_count=int(plan.total_commands),
        changes_summary=_changes_summary(plan),
        warnings_ar=tuple(warnings),
        rollback_available=rollback_available,
        rollback_explanation_ar=rollback_explanation,
        risk_level=risk,
        risk_reasons_ar=tuple(reasons),
    )


# ─── Helpers for the strings above ───────────────────────────


def _changes_summary(plan: ScriptPlan) -> dict[str, int]:
    return {
        "cleanup":       len(plan.cleanup_ops),
        "address_list":  len(plan.address_list_ops),
        "walled_garden": len(plan.walled_garden_ops),
        "filter":        len(plan.filter_ops),
        "scheduler":     len(plan.scheduler_ops),
    }


def _changes_phrase(plan: ScriptPlan) -> str:
    parts = []
    for k, n in _changes_summary(plan).items():
        if n:
            parts.append(f"{k}={n}")
    return ", ".join(parts) if parts else "—"


__all__ = [
    "RISK_LOW", "RISK_MEDIUM", "RISK_HIGH", "RISK_CRITICAL",
    "ImpactAnalysis", "analyze",
]
