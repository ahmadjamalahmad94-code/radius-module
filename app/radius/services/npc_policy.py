"""npc_policy — pure helpers shared by the three NPC sub-services.

Pure module — no DB, no Flask, no network, no filesystem. The
test suite asserts this so the module's import contract is
predictable.

What lives here:
  * The anchored comment prefix convention (single source of
    truth — never reconstruct it in callers).
  * Common name / scope / category validation.
  * Lifecycle state constants the routes use to render UI
    state independently of the deployments repo's literals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# ─── Comment-prefix convention ───────────────────────────────


# Top-level prefix every NPC-managed RouterOS object carries in
# its `comment` attribute. The renderer (Phase 2) emits it; the
# cleanup/rollback path (Phase 5) matches it with an anchored
# regex so a third-party comment merely containing the string
# `HOBE_NPC_...` doesn't get clobbered.
PREFIX_ROOT = "HOBE_NPC"


# Suffix per sub-service (kept short so MikroTik's 255-char
# comment limit leaves room for the per-target detail).
PREFIX_SUFFIX = {
    "remote_access": "REMOTE",
    "web_block":     "BLOCK",
    "walled_garden": "WG",
}


def comment_prefix(service: str, policy_id: int) -> str:
    """Return the canonical comment prefix for `(service,
    policy_id)`. Format: `HOBE_NPC_<SVC>:<id>:`. The trailing
    colon is included so callers can concat a per-object
    suffix without juggling separators.

    Raises ValueError if `service` is unknown — callers don't
    silently emit bogus prefixes.
    """
    suffix = PREFIX_SUFFIX.get(service)
    if suffix is None:
        raise ValueError(
            f"unknown NPC service for prefix: {service!r}; "
            f"expected one of {sorted(PREFIX_SUFFIX)}"
        )
    if int(policy_id) <= 0:
        raise ValueError("policy_id must be a positive int")
    return f"{PREFIX_ROOT}_{suffix}:{int(policy_id)}:"


def cleanup_regex(service: str, policy_id: int) -> str:
    """The RouterOS-syntax regex that matches every managed
    object's comment for a single (service, policy). Anchored
    with `^` so a substring elsewhere in a third-party comment
    cannot match — mirrors the VX2.3a hardening.
    """
    # We re.escape the prefix to be safe even though the
    # current literals contain no regex metacharacters. Future
    # service names might.
    return f"^{re.escape(comment_prefix(service, policy_id))}"


# ─── Lifecycle state — UI-facing constants ───────────────────


LIFECYCLE_DRAFT     = "draft"
LIFECYCLE_PREVIEWED = "previewed"
LIFECYCLE_APPLIED   = "applied"
LIFECYCLE_FAILED    = "failed"
LIFECYCLE_DISABLED  = "disabled"

LIFECYCLE_TERMINAL = frozenset({
    LIFECYCLE_APPLIED, LIFECYCLE_FAILED, LIFECYCLE_DISABLED,
})


def is_lifecycle_terminal(status: str) -> bool:
    """`True` if the status is not expected to transition
    automatically (i.e. operator action needed to move on)."""
    return status in LIFECYCLE_TERMINAL


# ─── Name validation ─────────────────────────────────────────


# Maximum policy-name length we accept from the UI. The DB
# column itself is 120; we cap earlier here so operators see
# a useful error before hitting SQLite.
MAX_NAME_LEN = 120

# Names must contain at least one printable, non-whitespace
# character. Otherwise they collapse to an empty slug fallback
# (`policy-<sha1>`) which still works but reads as a bug to
# operators reviewing the policy list.
_NAME_HAS_VISIBLE = re.compile(r"\S")


@dataclass(frozen=True)
class NameValidation:
    ok: bool
    reason: str = ""           # operator-facing Arabic on failure
    cleaned: str = ""          # what we'd persist (trimmed)


def validate_name(raw: str) -> NameValidation:
    """Pure validator — returns a dataclass instead of raising
    so the route layer can render the operator-facing reason
    inline alongside the form."""
    if raw is None:
        return NameValidation(
            ok=False, reason="الاسم مطلوب.",
        )
    cleaned = str(raw).strip()
    if not cleaned:
        return NameValidation(
            ok=False, reason="الاسم لا يمكن أن يكون فارغاً.",
        )
    if not _NAME_HAS_VISIBLE.search(cleaned):
        return NameValidation(
            ok=False,
            reason="الاسم يجب أن يحوي حرفاً ظاهراً واحداً على الأقل.",
        )
    if len(cleaned) > MAX_NAME_LEN:
        return NameValidation(
            ok=False,
            reason=(
                f"طول الاسم أكبر من المسموح "
                f"({MAX_NAME_LEN} حرفاً)."
            ),
            cleaned=cleaned[:MAX_NAME_LEN],
        )
    return NameValidation(ok=True, cleaned=cleaned)


# ─── Per-target caps ─────────────────────────────────────────


# How many targets we'll let one policy carry. RouterOS firewall
# address-lists scale into the thousands but UI usability and
# preview-script size collapse much earlier. 5000 is plenty for
# the realistic site-block and walled-garden cases; the renderer
# also pages outputs into batches when applying.
MAX_TARGETS_PER_POLICY = 5000


def assert_target_count_ok(count: int) -> None:
    """Raise ValueError if adding more targets would push the
    policy past the supported maximum."""
    if int(count) > MAX_TARGETS_PER_POLICY:
        raise ValueError(
            f"policy has {count} targets — "
            f"exceeds the maximum of {MAX_TARGETS_PER_POLICY}."
        )


def category_label(category: str) -> str:
    """Map a raw `category` string to an operator-facing Arabic
    label. Unknown values fall back to the raw string so we
    never lose operator data, just rendering polish."""
    return _CATEGORY_LABELS.get(category, category)


_CATEGORY_LABELS = {
    "tiktok":         "تيك توك",
    "instagram":      "إنستغرام",
    "facebook":       "فيسبوك",
    "twitter":        "تويتر / X",
    "youtube":        "يوتيوب",
    "gambling":       "مواقع المراهنات",
    "adult":          "محتوى للبالغين",
    "torrent":        "تطبيقات التورنت",
    "gaming":         "ألعاب",
    "streaming":      "بث مباشر",
    "ads":            "إعلانات",
    "speedtest":      "اختبار السرعة",
    "ip_checkers":    "كاشفات الـ IP",
    "vpn_providers":  "مزوّدو VPN",
    "custom":         "مخصّص",
}


def known_categories() -> Iterable[str]:
    """Stable ordering for the UI dropdown."""
    return tuple(_CATEGORY_LABELS.keys())


__all__ = [
    "PREFIX_ROOT", "PREFIX_SUFFIX",
    "comment_prefix", "cleanup_regex",
    "LIFECYCLE_DRAFT", "LIFECYCLE_PREVIEWED",
    "LIFECYCLE_APPLIED", "LIFECYCLE_FAILED",
    "LIFECYCLE_DISABLED", "LIFECYCLE_TERMINAL",
    "is_lifecycle_terminal",
    "MAX_NAME_LEN", "MAX_TARGETS_PER_POLICY",
    "NameValidation", "validate_name",
    "assert_target_count_ok",
    "category_label", "known_categories",
]
