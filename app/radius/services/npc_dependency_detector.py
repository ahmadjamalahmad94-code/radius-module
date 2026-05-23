"""npc_dependency_detector — curated rule-map that warns when
a blocked / allowlisted destination has well-known peer
domains the operator might need to also block or allow.

Pure module: no DB, no Flask, no DNS, no internet. The map is
hand-curated and conservative; we never claim a domain is
"definitely" tied to a service unless the linkage is
well-documented.

Confidence levels (no fake certainty):
  certain  — the same vendor explicitly serves these
             domains as a single product family.
  likely   — strong public documentation links them.
  possible — folklore / pattern-based association, may be
             obsolete; surface as a hint.

The dependency map is intentionally short — exhaustive lists
go stale fast. The brief lists the vendors we care about:
googleapis, gstatic, firebase, facebookcdn, fbcdn, tiktokcdn,
bytecdn, whatsapp, cloudflare, apple, microsoft.

Output: DependencyAnalysis dataclass with two lists:
  * dependencies[]   — peer destinations the operator should
                        also consider.
  * warnings_ar[]    — operator-facing notes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ─── Result types ────────────────────────────────────────────


CONFIDENCE_CERTAIN  = "certain"
CONFIDENCE_LIKELY   = "likely"
CONFIDENCE_POSSIBLE = "possible"


@dataclass(frozen=True)
class Dependency:
    service_name: str         # operator-recognisable label
    impact_ar: str            # what changes for the user
    confidence: str           # certain | likely | possible
    reason_ar: str
    related_domains: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "service_name":    self.service_name,
            "impact_ar":       self.impact_ar,
            "confidence":      self.confidence,
            "reason_ar":       self.reason_ar,
            "related_domains": list(self.related_domains),
        }


@dataclass(frozen=True)
class DependencyAnalysis:
    dependencies: tuple[Dependency, ...] = field(default_factory=tuple)
    warnings_ar: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dependencies": [d.as_dict() for d in self.dependencies],
            "warnings_ar":  list(self.warnings_ar),
        }


# ─── Curated trigger map ─────────────────────────────────────


# Triggers are matched against the operator's domain list by
# either exact host equality or "ends-with" suffix (`.example.com`
# would also match `example.com`). The match function is
# `_matches(target, triggers)` below.

# Each rule entry:
#   trigger_domains: tuple of patterns (without leading dot)
#   dependency: Dependency the analyzer should add when matched


_RULES: tuple[tuple[tuple[str, ...], Dependency], ...] = (
    # ─── Google product family ──────────────────────────
    (
        ("google.com", "google.ps", "googleapis.com",
         "gstatic.com", "googleusercontent.com",
         "youtube.com"),
        Dependency(
            service_name="Google",
            impact_ar=(
                "حظر/سماح google قد يؤثّر على Gmail و YouTube "
                "و Google Maps و Chrome وأي تطبيق يستخدم "
                "googleapis.com — يفضّل التعامل مع الكل معاً."
            ),
            confidence=CONFIDENCE_CERTAIN,
            reason_ar=(
                "نطاقات google التابعة لـ Alphabet مرتبطة "
                "ببعضها في خوادم نفس المزوّد."
            ),
            related_domains=(
                "googleapis.com", "gstatic.com",
                "googleusercontent.com", "youtube.com",
                "ytimg.com",
            ),
        ),
    ),
    # ─── Firebase = Google ──────────────────────────────
    (
        ("firebase.com", "firebaseio.com",
         "firebaseapp.com", "firebasestorage.googleapis.com"),
        Dependency(
            service_name="Firebase / Google Cloud",
            impact_ar=(
                "حظر Firebase سيؤدي إلى تعطيل دفع الإشعارات "
                "في كثير من تطبيقات الهاتف (FCM)."
            ),
            confidence=CONFIDENCE_CERTAIN,
            reason_ar=(
                "Firebase خدمة من Google؛ كثير من التطبيقات "
                "تعتمد على googleapis.com كذلك."
            ),
            related_domains=(
                "googleapis.com", "gstatic.com",
                "android.googleapis.com",
            ),
        ),
    ),
    # ─── Meta / Facebook ────────────────────────────────
    (
        ("facebook.com", "fb.com", "fbcdn.net",
         "facebookcdn.net", "messenger.com",
         "instagram.com", "whatsapp.com", "whatsapp.net"),
        Dependency(
            service_name="Meta (Facebook / Instagram / WhatsApp)",
            impact_ar=(
                "حظر facebook.com بدون فروعه قد يعطّل "
                "Messenger و Instagram جزئيّاً، فالـ CDN "
                "مشترك (fbcdn.net / fbcdn.com)."
            ),
            confidence=CONFIDENCE_LIKELY,
            reason_ar=(
                "Meta تستخدم بنية CDN موحَّدة عبر facebook / "
                "instagram / messenger."
            ),
            related_domains=(
                "fbcdn.net", "fbcdn.com", "messenger.com",
                "instagram.com", "cdninstagram.com",
            ),
        ),
    ),
    # ─── TikTok / ByteDance ────────────────────────────
    (
        ("tiktok.com", "tiktokcdn.com", "tiktokv.com",
         "musical.ly", "byteoversea.com", "bytecdn.cn",
         "ttvnw.net"),
        Dependency(
            service_name="TikTok / ByteDance",
            impact_ar=(
                "حظر tiktok.com لوحده لن يكفي — الفيديو يُحمَّل "
                "عبر tiktokcdn و bytecdn و ttvnw."
            ),
            confidence=CONFIDENCE_LIKELY,
            reason_ar=(
                "البنية متعددة الـ CDN؛ حظر النطاق الرئيسي "
                "وحده يترك معظم المحتوى متاحاً."
            ),
            related_domains=(
                "tiktokcdn.com", "tiktokv.com",
                "byteoversea.com", "ttvnw.net",
            ),
        ),
    ),
    # ─── Cloudflare ─────────────────────────────────────
    (
        ("cloudflare.com", "cloudflare.net",
         "cloudflareinsights.com"),
        Dependency(
            service_name="Cloudflare CDN",
            impact_ar=(
                "حظر Cloudflare سيكسر آلاف المواقع التي "
                "تستخدمه كـ CDN عام (ليس مرتبطاً بمزوّد محتوى "
                "واحد)."
            ),
            confidence=CONFIDENCE_CERTAIN,
            reason_ar=(
                "Cloudflare هي طبقة شبكة عابرة لكثير من "
                "المواقع — تأثير الحظر واسع."
            ),
            related_domains=(
                "cloudflareinsights.com",
            ),
        ),
    ),
    # ─── Apple ──────────────────────────────────────────
    (
        ("apple.com", "icloud.com", "mzstatic.com",
         "itunes.apple.com", "apps.apple.com"),
        Dependency(
            service_name="Apple",
            impact_ar=(
                "حظر apple.com سيؤثّر على iCloud و iMessage و"
                " App Store و تحديثات نظام iOS / macOS."
            ),
            confidence=CONFIDENCE_CERTAIN,
            reason_ar=(
                "خدمات Apple موحَّدة في نفس مزوّد البنية "
                "التحتية."
            ),
            related_domains=(
                "icloud.com", "mzstatic.com",
                "itunes.apple.com",
            ),
        ),
    ),
    # ─── Microsoft / Office 365 ────────────────────────
    (
        ("microsoft.com", "office.com", "office365.com",
         "outlook.com", "live.com", "microsoftonline.com",
         "azure.com", "azureedge.net"),
        Dependency(
            service_name="Microsoft / Office 365",
            impact_ar=(
                "حظر microsoft.com لوحده لن يكفي — Office "
                "وExchange/Outlook و Teams تعتمد على "
                "office365.com و microsoftonline.com و"
                " azureedge.net."
            ),
            confidence=CONFIDENCE_LIKELY,
            reason_ar=(
                "خدمات M365 موزَّعة على عدة نطاقات تابعة لـ "
                "Microsoft."
            ),
            related_domains=(
                "office365.com", "outlook.office.com",
                "microsoftonline.com", "azureedge.net",
            ),
        ),
    ),
)


# ─── Matchers ────────────────────────────────────────────────


def _normalize(host: str) -> str:
    """Lowercase + strip leading `www.` + trailing dot."""
    s = (host or "").strip().lower().rstrip(".")
    if s.startswith("www."):
        s = s[4:]
    return s


def _matches(target: str, triggers: Iterable[str]) -> bool:
    """`True` if `target` equals one of the triggers or is a
    subdomain of one."""
    n = _normalize(target)
    if not n:
        return False
    for t in triggers:
        tn = _normalize(t)
        if not tn:
            continue
        if n == tn or n.endswith("." + tn):
            return True
    return False


# ─── Public API ──────────────────────────────────────────────


def analyze(
    *, targets: Iterable[dict],
    policy_type: str = "",
) -> DependencyAnalysis:
    """Build the dependency report. `targets` is the iterable
    of target/entry dicts (web_block targets, walled_garden
    entries) that the policy will operate on. `policy_type`
    is informational — used to tailor the impact phrasing
    (block vs allow)."""
    values = [
        _normalize(t.get("normalized_value") or t.get("value") or "")
        for t in (targets or ())
        if t.get("status") in (None, "active")
    ]
    if not values:
        return DependencyAnalysis(
            dependencies=(),
            warnings_ar=(),
        )

    deps: list[Dependency] = []
    seen: set[str] = set()
    for triggers, dep in _RULES:
        if dep.service_name in seen:
            continue
        for v in values:
            if _matches(v, triggers):
                deps.append(dep)
                seen.add(dep.service_name)
                break

    warnings: list[str] = []
    if deps:
        warnings.append(
            "هذه الاعتمادية قائمة على قائمة منسَّقة يدوياً — "
            "قد تكون بعض النطاقات قديمة. راجعها قبل التطبيق."
        )

    return DependencyAnalysis(
        dependencies=tuple(deps),
        warnings_ar=tuple(warnings),
    )


__all__ = [
    "CONFIDENCE_CERTAIN", "CONFIDENCE_LIKELY",
    "CONFIDENCE_POSSIBLE",
    "Dependency", "DependencyAnalysis",
    "analyze",
]
