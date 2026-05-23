"""npc_execution_contracts — pure rule engine that decides
whether an NPC policy is allowed to enter the apply path.

Pure module: no DB, no Flask, no MikroTik. Phase 4's
`npc_apply_service` calls this BEFORE doing anything that
touches a router-shaped surface. Phase 1's preview UI calls
`npc_execution_readiness.evaluate(...)` (a thin orchestrator)
which composes inputs and delegates here.

Output shape: `ContractDecision` dataclass:
  ready                     — bool
  blockers                  — tuple[ContractIssue]
  warnings                  — tuple[ContractIssue]
  required_confirmations    — tuple[str]  (machine codes)
  recommended_mode          — canary | staged | full | hold
  execution_modes_allowed   — tuple[str]
  reason_ar                 — short top-level summary

Each `ContractIssue` carries a machine `code`, a `severity`,
and the Arabic operator-facing `message_ar`. Codes are stable
strings the apply route uses to gate behaviour.

The brief lists exact blockers. Each maps to a code:

  MISSING_APPLY_PERM           — actor doesn't have apply
  NO_VALID_PREVIEW             — no script_version exists yet
  PREVIEW_STALE                — policy.updated_at > preview ts
  PREVIEW_HASH_MISMATCH        — caller's hash != stored hash
  NO_ROLLBACK                  — rollback_available=False
  NO_SNAPSHOT                  — no snapshot id supplied
  NO_TARGET_ROUTERS            — target list is empty
  CRITICAL_RISK                — impact.risk_level=critical
  DANGEROUS_HEALTH             — health.grade=dangerous
  CRITICAL_CONFLICT            — at least one HIGH conflict
  UNSAFE_SCRIPT                — render_error set
  UNMANAGED_DELETION           — script contains bare
                                 `remove [find ...]` without
                                 anchored HOBE_NPC_ prefix
                                 (defence-in-depth; the
                                 renderer already refuses).
  TARGET_ROUTER_OFFLINE        — caller-supplied flag.
  ALL_ROUTERS_WITHOUT_CANARY   — all-routers + no canary opt-in
  SECRET_LIKE_CONTENT          — script substring tripwires

Warnings (do not block, must be shown):
  MEDIUM_RISK / HIGH_RISK      — impact.risk_level
  DEPENDENCY_UNCERTAINTY       — any dependency confidence < certain
  LARGE_BLAST                  — blast.blast_radius=large
  CANARY_RECOMMENDED           — canary.recommended_strategy=canary
  ESTIMATED_USERS_HEURISTIC    — blast carries estimated_user_count

Required confirmations (must be stored alongside the apply
request; the route enforces presence):
  confirm_large_blast_radius
  confirm_firewall_drop
  confirm_all_router_scope
  confirm_dependency_impact
  confirm_canary_bypass

Hard rules:
  * Critical blockers must stop apply. No bypass.
  * Warnings do not stop apply.
  * Required confirmations must be present on the apply
    request. Their absence becomes a synthetic blocker on
    re-evaluation inside the apply route.
  * No bypass for missing rollback / missing snapshot /
    unsafe script.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ─── Codes ──────────────────────────────────────────────────


# Blocker codes — apply MUST refuse if any of these are
# present.
BLOCK_MISSING_APPLY_PERM         = "missing_apply_perm"
BLOCK_NO_VALID_PREVIEW           = "no_valid_preview"
BLOCK_PREVIEW_STALE              = "preview_stale"
BLOCK_PREVIEW_HASH_MISMATCH      = "preview_hash_mismatch"
BLOCK_NO_ROLLBACK                = "no_rollback"
BLOCK_NO_SNAPSHOT                = "no_snapshot"
BLOCK_NO_TARGET_ROUTERS          = "no_target_routers"
BLOCK_CRITICAL_RISK              = "critical_risk"
BLOCK_DANGEROUS_HEALTH           = "dangerous_health"
BLOCK_CRITICAL_CONFLICT          = "critical_conflict"
BLOCK_UNSAFE_SCRIPT              = "unsafe_script"
BLOCK_UNMANAGED_DELETION         = "unmanaged_deletion"
BLOCK_TARGET_ROUTER_OFFLINE      = "target_router_offline"
BLOCK_ALL_ROUTERS_WITHOUT_CANARY = "all_routers_without_canary"
BLOCK_SECRET_LIKE_CONTENT        = "secret_like_content"
BLOCK_MISSING_CONFIRMATION       = "missing_confirmation"

# Warning codes — do NOT block. Operator should see them.
WARN_MEDIUM_RISK                = "medium_risk"
WARN_HIGH_RISK                  = "high_risk"
WARN_DEPENDENCY_UNCERTAINTY     = "dependency_uncertainty"
WARN_LARGE_BLAST                = "large_blast"
WARN_CANARY_RECOMMENDED         = "canary_recommended"
WARN_ESTIMATED_USERS_HEURISTIC  = "estimated_users_heuristic"

# Required-confirmation codes — must be stored alongside the
# apply request. The apply route synthesises a
# `BLOCK_MISSING_CONFIRMATION` if any of these is required but
# not present.
CONFIRM_LARGE_BLAST       = "confirm_large_blast_radius"
CONFIRM_FIREWALL_DROP     = "confirm_firewall_drop"
CONFIRM_ALL_ROUTER_SCOPE  = "confirm_all_router_scope"
CONFIRM_DEPENDENCY_IMPACT = "confirm_dependency_impact"
CONFIRM_CANARY_BYPASS     = "confirm_canary_bypass"


# Execution modes.
MODE_CANARY   = "canary"
MODE_STAGED   = "staged"
MODE_FULL     = "full"
MODE_HOLD     = "hold"


# ─── Result types ───────────────────────────────────────────


@dataclass(frozen=True)
class ContractIssue:
    code: str
    severity: str        # blocker | warning
    message_ar: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code":       self.code,
            "severity":   self.severity,
            "message_ar": self.message_ar,
        }


@dataclass(frozen=True)
class ContractDecision:
    ready: bool
    blockers: tuple[ContractIssue, ...] = field(default_factory=tuple)
    warnings: tuple[ContractIssue, ...] = field(default_factory=tuple)
    required_confirmations: tuple[str, ...] = field(default_factory=tuple)
    recommended_mode: str = MODE_FULL
    execution_modes_allowed: tuple[str, ...] = field(default_factory=tuple)
    reason_ar: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready":                  bool(self.ready),
            "blockers":               [b.as_dict() for b in self.blockers],
            "warnings":               [w.as_dict() for w in self.warnings],
            "required_confirmations": list(self.required_confirmations),
            "recommended_mode":       self.recommended_mode,
            "execution_modes_allowed": list(self.execution_modes_allowed),
            "reason_ar":              self.reason_ar,
        }


# ─── Inputs ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ContractInputs:
    """All the upstream signals the contracts engine evaluates
    over. The caller (typically `npc_execution_readiness`)
    composes these from the policy, the latest preview, the
    intelligence outputs, the snapshot status, and the actor's
    permissions."""

    # Policy state
    policy_id: int
    policy_type: str             # nc.SERVICE_*
    target_router_ids: tuple[int, ...] = ()
    all_routers_targeted: bool = False
    offline_router_ids: tuple[int, ...] = ()

    # Latest preview
    has_preview: bool = False
    preview_hash: str = ""
    expected_preview_hash: str = ""  # caller-supplied
    preview_at: str = ""             # ISO-8601 str
    policy_updated_at: str = ""

    # Script + render
    forward_script: str = ""
    rollback_script: str = ""
    render_error: str = ""

    # Intelligence
    impact_risk_level: str = "low"       # low|medium|high|critical
    health_grade: str = "good"           # excellent..dangerous
    blast_radius: str = "small"          # small|medium|large|critical
    blast_estimated_users: Optional[int] = None
    conflict_high_count: int = 0
    dependency_any_uncertain: bool = False
    rollback_available: bool = True
    canary_strategy: str = MODE_FULL     # canary|staged|full|hold

    # Snapshot
    snapshot_id: Optional[int] = None

    # Actor
    actor_has_apply_perm: bool = False
    confirmations_provided: tuple[str, ...] = ()

    # Canary opt-in (the operator explicitly accepts to bypass)
    canary_opt_in: bool = False


# ─── Helpers ─────────────────────────────────────────────────


# An anchored `remove [find comment~"PREFIX"]` pattern is what
# the renderer guarantees. Anything else in a remove statement
# triggers the unmanaged-deletion blocker.
_RE_REMOVE_FIND = re.compile(
    r"remove\s+\[find\s+comment~\"([^\"]+)\"\]",
    re.IGNORECASE,
)

_SAFE_PREFIX = "^HOBE_NPC_"

# Secret tripwires — duplicated from the script renderer so a
# defence-in-depth re-scan happens here too.
_SECRET_TRIPWIRES = (
    "private-key=", "PrivateKey =", "private_key=",
    "BEGIN PRIVATE KEY",
    "password=", "Password =",
)


def _scan_unmanaged_remove(script: str) -> bool:
    """`True` if any `remove [find comment~"X"]` carries a
    pattern that doesn't start with `^HOBE_NPC_`. The renderer
    already enforces this; we re-scan as a tripwire in case
    a future code path bypasses the renderer."""
    if not script:
        return False
    for m in _RE_REMOVE_FIND.finditer(script):
        pattern = m.group(1)
        if not pattern.startswith(_SAFE_PREFIX):
            return True
    return False


def _scan_secret_like(script: str) -> bool:
    if not script:
        return False
    return any(t in script for t in _SECRET_TRIPWIRES)


# ─── Engine ─────────────────────────────────────────────────


def evaluate(inputs: ContractInputs) -> ContractDecision:
    """Run the contracts rule-set over `inputs` and return a
    `ContractDecision`. Pure — no IO."""
    blockers: list[ContractIssue] = []
    warnings: list[ContractIssue] = []
    required: list[str] = []

    # ── Permission ──
    if not inputs.actor_has_apply_perm:
        blockers.append(ContractIssue(
            code=BLOCK_MISSING_APPLY_PERM, severity="blocker",
            message_ar=(
                "صلاحية apply مفقودة لدى المستخدم الحالي."
            ),
        ))

    # ── Preview existence / staleness / hash ──
    if not inputs.has_preview:
        blockers.append(ContractIssue(
            code=BLOCK_NO_VALID_PREVIEW, severity="blocker",
            message_ar=(
                "لا توجد معاينة محفوظة لهذه السياسة — يجب توليد "
                "معاينة قبل أي محاولة تنفيذ."
            ),
        ))
    if (inputs.has_preview
        and inputs.preview_at and inputs.policy_updated_at
        and inputs.preview_at < inputs.policy_updated_at):
        blockers.append(ContractIssue(
            code=BLOCK_PREVIEW_STALE, severity="blocker",
            message_ar=(
                "تم تعديل السياسة بعد آخر معاينة — أعد توليد "
                "المعاينة قبل المتابعة."
            ),
        ))
    if (inputs.has_preview
        and inputs.expected_preview_hash
        and inputs.preview_hash
        and inputs.expected_preview_hash != inputs.preview_hash):
        blockers.append(ContractIssue(
            code=BLOCK_PREVIEW_HASH_MISMATCH, severity="blocker",
            message_ar=(
                "تطابق المعاينة فشل — السكربت المراد تنفيذه "
                "ليس هو نفسه السكربت الذي تمت مراجعته."
            ),
        ))

    # ── Rollback ──
    if not inputs.rollback_available:
        blockers.append(ContractIssue(
            code=BLOCK_NO_ROLLBACK, severity="blocker",
            message_ar=(
                "لا يوجد سكربت rollback — التنفيذ بدون "
                "إمكانية تراجع غير مسموح."
            ),
        ))

    # ── Snapshot ──
    if inputs.snapshot_id is None or int(inputs.snapshot_id) <= 0:
        blockers.append(ContractIssue(
            code=BLOCK_NO_SNAPSHOT, severity="blocker",
            message_ar=(
                "لا يوجد snapshot قبل التنفيذ — يجب التقاط "
                "حالة الراوتر قبل أي تعديل."
            ),
        ))

    # ── Target routers ──
    if not inputs.target_router_ids:
        blockers.append(ContractIssue(
            code=BLOCK_NO_TARGET_ROUTERS, severity="blocker",
            message_ar=(
                "قائمة الراوترات المستهدفة فارغة."
            ),
        ))
    if inputs.offline_router_ids:
        blockers.append(ContractIssue(
            code=BLOCK_TARGET_ROUTER_OFFLINE, severity="blocker",
            message_ar=(
                f"بعض الراوترات المستهدفة غير متّصلة: "
                f"{sorted(inputs.offline_router_ids)}."
            ),
        ))

    # ── Risk + health + conflicts ──
    if inputs.impact_risk_level == "critical":
        blockers.append(ContractIssue(
            code=BLOCK_CRITICAL_RISK, severity="blocker",
            message_ar=(
                "تحليل الأثر يصنّف الخطّة critical — لا يمكن "
                "التنفيذ قبل إعادة التخطيط."
            ),
        ))
    elif inputs.impact_risk_level == "high":
        warnings.append(ContractIssue(
            code=WARN_HIGH_RISK, severity="warning",
            message_ar="مستوى الخطر مرتفع — راجع الأسباب أعلاه.",
        ))
    elif inputs.impact_risk_level == "medium":
        warnings.append(ContractIssue(
            code=WARN_MEDIUM_RISK, severity="warning",
            message_ar="مستوى الخطر متوسط.",
        ))

    if inputs.health_grade == "dangerous":
        blockers.append(ContractIssue(
            code=BLOCK_DANGEROUS_HEALTH, severity="blocker",
            message_ar="درجة السلامة منخفضة جداً.",
        ))

    if inputs.conflict_high_count > 0:
        blockers.append(ContractIssue(
            code=BLOCK_CRITICAL_CONFLICT, severity="blocker",
            message_ar=(
                f"يوجد {inputs.conflict_high_count} تعارض(ات) "
                "عالي الخطورة مع سياسات أخرى — حلّها أوّلاً."
            ),
        ))

    # ── Script safety ──
    if inputs.render_error:
        blockers.append(ContractIssue(
            code=BLOCK_UNSAFE_SCRIPT, severity="blocker",
            message_ar=(
                "السكربت مرفوض تلقائياً بسبب محتوى حسّاس — "
                "لا يمكن المتابعة."
            ),
        ))
    if _scan_secret_like(inputs.forward_script):
        blockers.append(ContractIssue(
            code=BLOCK_SECRET_LIKE_CONTENT, severity="blocker",
            message_ar=(
                "السكربت يحوي قيماً تشبه بيانات حسّاسة — رُفض "
                "تلقائياً."
            ),
        ))
    if _scan_unmanaged_remove(inputs.forward_script) \
            or _scan_unmanaged_remove(inputs.rollback_script):
        blockers.append(ContractIssue(
            code=BLOCK_UNMANAGED_DELETION, severity="blocker",
            message_ar=(
                "اكتُشف أمر حذف بدون البادئة المُدارة "
                "(^HOBE_NPC_...) — لا يُسمح بتنفيذه."
            ),
        ))

    # ── All-routers without canary opt-in ──
    if inputs.all_routers_targeted and not inputs.canary_opt_in:
        blockers.append(ContractIssue(
            code=BLOCK_ALL_ROUTERS_WITHOUT_CANARY,
            severity="blocker",
            message_ar=(
                "السياسة تستهدف كل الراوترات — يجب الموافقة "
                "صراحةً على تخطّي canary، أو اختيار راوترات "
                "محدَّدة."
            ),
        ))

    # ── Soft warnings ──
    if inputs.blast_radius == "large":
        warnings.append(ContractIssue(
            code=WARN_LARGE_BLAST, severity="warning",
            message_ar="نطاق التأثير واسع.",
        ))
        required.append(CONFIRM_LARGE_BLAST)
    elif inputs.blast_radius == "critical":
        # already added as critical-risk path? Yes, but if
        # impact_risk_level is not critical we still warn.
        warnings.append(ContractIssue(
            code=WARN_LARGE_BLAST, severity="warning",
            message_ar="نطاق التأثير حرج — مراجعة مطلوبة.",
        ))
        required.append(CONFIRM_LARGE_BLAST)

    if inputs.dependency_any_uncertain:
        warnings.append(ContractIssue(
            code=WARN_DEPENDENCY_UNCERTAINTY, severity="warning",
            message_ar=(
                "بعض التبعيّات غير مؤكَّدة — يفضّل المراجعة قبل "
                "التنفيذ."
            ),
        ))
        required.append(CONFIRM_DEPENDENCY_IMPACT)

    if inputs.canary_strategy in (MODE_CANARY, MODE_HOLD):
        warnings.append(ContractIssue(
            code=WARN_CANARY_RECOMMENDED, severity="warning",
            message_ar=(
                "توصية: تطبيق تدريجي (canary) قبل التنفيذ الكامل."
            ),
        ))

    if inputs.blast_estimated_users is not None:
        warnings.append(ContractIssue(
            code=WARN_ESTIMATED_USERS_HEURISTIC,
            severity="warning",
            message_ar=(
                f"عدد المستخدمين تقدير حسابي "
                f"(~{inputs.blast_estimated_users}) — وليس "
                "قياساً مباشراً."
            ),
        ))

    # firewall-drop confirm only when the script has forward drop.
    if "action=drop" in inputs.forward_script \
            and "chain=forward" in inputs.forward_script:
        required.append(CONFIRM_FIREWALL_DROP)

    if inputs.all_routers_targeted:
        required.append(CONFIRM_ALL_ROUTER_SCOPE)
    if inputs.canary_opt_in:
        required.append(CONFIRM_CANARY_BYPASS)

    # ── Confirmations check ──
    missing_confirms = [c for c in required
                         if c not in inputs.confirmations_provided]
    if missing_confirms:
        # Surface as one synthetic blocker — the apply route
        # uses the `code == BLOCK_MISSING_CONFIRMATION` to know
        # exactly which confirmations the operator must tick.
        blockers.append(ContractIssue(
            code=BLOCK_MISSING_CONFIRMATION, severity="blocker",
            message_ar=(
                "موافقات صريحة مطلوبة قبل التنفيذ — "
                f"المفقود: {missing_confirms}."
            ),
        ))

    # ── Recommended mode + allowed modes ──
    if inputs.canary_strategy == MODE_HOLD:
        recommended = MODE_HOLD
        allowed: tuple[str, ...] = ()
    elif inputs.canary_strategy == MODE_CANARY:
        recommended = MODE_CANARY
        allowed = (MODE_CANARY, MODE_STAGED)
    elif inputs.blast_radius == "large":
        recommended = MODE_CANARY
        allowed = (MODE_CANARY, MODE_STAGED, MODE_FULL)
    elif inputs.blast_radius == "medium":
        recommended = MODE_STAGED
        allowed = (MODE_STAGED, MODE_FULL)
    else:
        recommended = MODE_FULL
        allowed = (MODE_FULL, MODE_STAGED)

    ready = not blockers
    if ready:
        reason = (
            "الخطّة جاهزة للتنفيذ وفق المعايير الحاليّة."
        )
    elif any(b.code == BLOCK_CRITICAL_RISK for b in blockers):
        reason = (
            "الخطّة critical — التنفيذ ممنوع حتى إعادة التخطيط."
        )
    elif any(b.code == BLOCK_UNSAFE_SCRIPT
              or b.code == BLOCK_UNMANAGED_DELETION
              or b.code == BLOCK_SECRET_LIKE_CONTENT
              for b in blockers):
        reason = (
            "محتوى السكربت غير آمن — التنفيذ ممنوع."
        )
    elif any(b.code == BLOCK_MISSING_CONFIRMATION for b in blockers):
        reason = (
            "بانتظار موافقات صريحة من المشغّل قبل التنفيذ."
        )
    else:
        reason = (
            "هناك موانع تنفيذ — راجع التفاصيل وعالج الأسباب."
        )

    return ContractDecision(
        ready=ready,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        required_confirmations=tuple(required),
        recommended_mode=recommended,
        execution_modes_allowed=allowed,
        reason_ar=reason,
    )


__all__ = [
    # Codes
    "BLOCK_MISSING_APPLY_PERM", "BLOCK_NO_VALID_PREVIEW",
    "BLOCK_PREVIEW_STALE", "BLOCK_PREVIEW_HASH_MISMATCH",
    "BLOCK_NO_ROLLBACK", "BLOCK_NO_SNAPSHOT",
    "BLOCK_NO_TARGET_ROUTERS", "BLOCK_CRITICAL_RISK",
    "BLOCK_DANGEROUS_HEALTH", "BLOCK_CRITICAL_CONFLICT",
    "BLOCK_UNSAFE_SCRIPT", "BLOCK_UNMANAGED_DELETION",
    "BLOCK_TARGET_ROUTER_OFFLINE",
    "BLOCK_ALL_ROUTERS_WITHOUT_CANARY",
    "BLOCK_SECRET_LIKE_CONTENT",
    "BLOCK_MISSING_CONFIRMATION",
    "WARN_MEDIUM_RISK", "WARN_HIGH_RISK",
    "WARN_DEPENDENCY_UNCERTAINTY", "WARN_LARGE_BLAST",
    "WARN_CANARY_RECOMMENDED",
    "WARN_ESTIMATED_USERS_HEURISTIC",
    "CONFIRM_LARGE_BLAST", "CONFIRM_FIREWALL_DROP",
    "CONFIRM_ALL_ROUTER_SCOPE", "CONFIRM_DEPENDENCY_IMPACT",
    "CONFIRM_CANARY_BYPASS",
    "MODE_CANARY", "MODE_STAGED", "MODE_FULL", "MODE_HOLD",
    # Types
    "ContractIssue", "ContractDecision", "ContractInputs",
    # API
    "evaluate",
]
