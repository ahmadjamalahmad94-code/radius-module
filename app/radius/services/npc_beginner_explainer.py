"""npc_beginner_explainer — translate MikroTik jargon in an
NPC ScriptPlan into beginner-friendly Arabic.

Pure module: no DB, no Flask, no MikroTik.

Output shape:
  simple_ar          — short, jargon-free paragraph
  glossary[]         — only the terms actually used in this
                       plan (avoid noise)
  operator_notes_ar  — short bullets the operator should keep
                       in mind

The glossary is the single source of truth for the technical-
term → Arabic-friendly mapping used across the NPC UI. Other
phases can re-import `GLOSSARY` rather than re-inventing
labels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .npc_script_renderer import ScriptPlan


# ─── Master glossary ─────────────────────────────────────────


# (term_key, label_ar, explanation_ar)
# `term_key` is the canonical English MikroTik term we'll
# match against plan content. Keep the explanations short —
# this is rendered inline in the preview UI, not a manual.
GLOSSARY: dict[str, dict[str, str]] = {
    "firewall": {
        "label_ar": "الجدار الناري",
        "explanation_ar": (
            "آلية تصفية الحركة على الراوتر — تقرّر أي بيانات "
            "تمرّ وأي بيانات تُحجب."
        ),
    },
    "address-list": {
        "label_ar": "قائمة العناوين",
        "explanation_ar": (
            "مجموعة مسمّاة من النطاقات أو الـ IPs نشير إليها "
            "في القاعدة بدل كتابة كل عنوان بمفرده."
        ),
    },
    "hotspot": {
        "label_ar": "بوّابة الـ Hotspot",
        "explanation_ar": (
            "صفحة تسجيل الدخول التي تظهر للزبون عندما يتّصل "
            "بالـ WiFi قبل أن يُفتح له الإنترنت."
        ),
    },
    "walled-garden": {
        "label_ar": "الجدار الإستثنائي",
        "explanation_ar": (
            "قائمة المواقع المسموح فتحها قبل تسجيل دخول الزبون "
            "في الـ Hotspot — مثل بوّابة الدفع أو رمز OTP."
        ),
    },
    "input-chain": {
        "label_ar": "سلسلة input",
        "explanation_ar": (
            "القواعد التي تتحكّم بمن يصل إلى الراوتر نفسه "
            "(Winbox، WebFig، SSH، API)."
        ),
    },
    "forward-chain": {
        "label_ar": "سلسلة forward",
        "explanation_ar": (
            "القواعد التي تتحكّم بحركة المستخدمين الذين يمرّون "
            "عبر الراوتر للوصول إلى الإنترنت."
        ),
    },
    "drop": {
        "label_ar": "حذف الحركة (drop)",
        "explanation_ar": (
            "إيقاف الحركة فوراً دون إبلاغ المرسل — هو فعل "
            "الحظر داخل الجدار الناري."
        ),
    },
    "accept": {
        "label_ar": "السماح (accept)",
        "explanation_ar": (
            "السماح للحركة بالمرور — يُستخدم لفتح المنافذ "
            "الإدارية مثلاً."
        ),
    },
    "scheduler": {
        "label_ar": "المهمّة المجدوَلة",
        "explanation_ar": (
            "أمر يعمل على الراوتر في وقت محدَّد — "
            "نستخدمه لحذف القواعد تلقائياً عند انتهاء "
            "صلاحيتها."
        ),
    },
    "rollback": {
        "label_ar": "التراجع (rollback)",
        "explanation_ar": (
            "إعادة الراوتر إلى الحالة السابقة عبر سكربت مضاد "
            "يحذف فقط ما أنشأناه نحن."
        ),
    },
    "remote-access": {
        "label_ar": "الوصول البعيد",
        "explanation_ar": (
            "السماح بالاتصال بالراوتر من خارج الشبكة المحليّة "
            "(Winbox، WebFig، SSH …)."
        ),
    },
}


# ─── Output type ─────────────────────────────────────────────


@dataclass(frozen=True)
class GlossaryItem:
    term: str
    label_ar: str
    explanation_ar: str

    def as_dict(self) -> dict[str, str]:
        return {
            "term":           self.term,
            "label_ar":       self.label_ar,
            "explanation_ar": self.explanation_ar,
        }


@dataclass(frozen=True)
class BeginnerExplanation:
    simple_ar: str
    glossary: tuple[GlossaryItem, ...] = field(default_factory=tuple)
    operator_notes_ar: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "simple_ar":         self.simple_ar,
            "glossary":          [g.as_dict() for g in self.glossary],
            "operator_notes_ar": list(self.operator_notes_ar),
        }


# ─── Per-service prose ───────────────────────────────────────


_SIMPLE_AR_TEMPLATES = {
    "remote_access": (
        "هذه السياسة تفتح للمسؤولين باباً مخصَّصاً للوصول إلى "
        "الراوتر من بعيد — مثلاً عبر Winbox أو WebFig. الباب "
        "مغلق افتراضياً ولا يُفتح إلا بطلب صريح، ويُغلق "
        "تلقائياً في الوقت المحدَّد."
    ),
    "web_block": (
        "هذه السياسة تمنع المستخدمين خلف الراوتر من فتح المواقع "
        "المحدَّدة. الحظر يعمل على الجدار الناري للراوتر، ولا "
        "يمسّ بقيّة المواقع."
    ),
    "walled_garden": (
        "هذه السياسة تسمح بفتح بعض المواقع قبل تسجيل دخول "
        "الزبون في الـ Hotspot — مثل صفحة الدفع أو رسائل OTP — "
        "حتى لو لم يكن لديه حساب نشط بعد."
    ),
}


# ─── Plan-based term picker ──────────────────────────────────


def _terms_used(service: str, plan: ScriptPlan) -> list[str]:
    """Return only the glossary terms that actually appear in
    this plan. Avoids noise — the operator sees explanations
    for what they're about to apply, nothing else."""
    used: list[str] = ["firewall", "rollback"]
    if plan.address_list_ops:
        used.append("address-list")
    if plan.walled_garden_ops:
        used.extend(["hotspot", "walled-garden"])
    if plan.filter_ops:
        for c in plan.filter_ops:
            chain = c.attrs.get("chain", "")
            action = c.attrs.get("action", "")
            if chain == "input":
                used.append("input-chain")
            elif chain == "forward":
                used.append("forward-chain")
            if action == "drop":
                used.append("drop")
            elif action == "accept":
                used.append("accept")
    if plan.scheduler_ops:
        used.append("scheduler")
    if service == "remote_access":
        used.append("remote-access")
    # Deduplicate, preserve first-seen order.
    seen: set[str] = set()
    out: list[str] = []
    for t in used:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ─── Operator notes per service ─────────────────────────────


def _operator_notes(
    service: str, plan: ScriptPlan,
) -> list[str]:
    notes: list[str] = []
    if service == "remote_access":
        notes.append(
            "تأكَّد من ترك قناة إدارية ثانية متاحة قبل التطبيق "
            "(مثلاً Winbox محلي عبر MAC) كي لا تنقطع عن "
            "الراوتر إن حدث خلل."
        )
        if plan.scheduler_ops:
            notes.append(
                "المهمّة المجدوَلة ستحذف القواعد تلقائياً عند "
                "وقت الانتهاء — راجع الوقت قبل التطبيق."
            )
    elif service == "web_block":
        notes.append(
            "السياسة تستخدم قائمة عناوين مُدارة باسم خاص — "
            "لا تلمس قوائم الـ firewall الأخرى."
        )
        notes.append(
            "إن كان الموقع مرتبطاً بـ CDN كبير (Google، Meta، "
            "Cloudflare …) فقد يحتاج الحظر إلى نطاقات مرتبطة."
        )
    elif service == "walled_garden":
        notes.append(
            "الإدخالات تُضاف إلى الإستثناءات فقط — لا تُحذف "
            "إدخالات سابقة لم نُنشئها نحن."
        )
    notes.append(
        "كل ما تراه هو معاينة — لا يُكتب على الراوتر إلا "
        "في مرحلة «التطبيق المحمي» المنفصلة."
    )
    return notes


# ─── Public API ──────────────────────────────────────────────


def explain(
    *, policy_type: str, plan: ScriptPlan,
    policy: dict | None = None,
) -> BeginnerExplanation:
    """Build the beginner explanation for one plan."""
    simple = _SIMPLE_AR_TEMPLATES.get(
        policy_type,
        "هذه سياسة شبكة في طور المعاينة.",
    )
    terms = _terms_used(policy_type, plan)
    glossary = tuple(
        GlossaryItem(
            term=t,
            label_ar=GLOSSARY[t]["label_ar"],
            explanation_ar=GLOSSARY[t]["explanation_ar"],
        )
        for t in terms if t in GLOSSARY
    )
    notes = _operator_notes(policy_type, plan)
    return BeginnerExplanation(
        simple_ar=simple,
        glossary=glossary,
        operator_notes_ar=tuple(notes),
    )


__all__ = [
    "GLOSSARY",
    "GlossaryItem", "BeginnerExplanation",
    "explain",
]
