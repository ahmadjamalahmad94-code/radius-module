"""npc_conflict_detector — detect conflicts between an
in-flight NPC policy and the rest of the tenant's policies.

Pure module: no DB, no Flask. The route layer fetches all
peer policies (and their children/entries) and hands them in
as a list of dicts; this module computes the conflicts.

Conflict categories:
  * block_vs_allow      — same/overlapping host appears as a
                          web_block target AND a walled_garden
                          allow entry on overlapping routers.
  * duplicate_policy    — another policy on the same router
                          with the same slug (would refuse to
                          coexist on apply anyway).
  * overlapping_router  — another policy of the same service
                          + same router that's enabled.
  * overlapping_target  — same normalized destination in
                          another web_block policy on the same
                          router.
  * overlapping_entry   — same (entry_type, value) in another
                          walled_garden policy on the same
                          router.
  * hotspot_profile     — another walled_garden policy on the
                          same router targeting the same
                          hotspot_profile.

Severity:
  high   — block_vs_allow, duplicate_policy.
  medium — overlapping_router, hotspot_profile.
  low    — overlapping_target / overlapping_entry (re-import
            is idempotent inside one policy, but operator
            should know it's already managed elsewhere).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ─── Severity ────────────────────────────────────────────────


SEV_LOW    = "low"
SEV_MEDIUM = "medium"
SEV_HIGH   = "high"

_SEV_ORDER = {SEV_LOW: 0, SEV_MEDIUM: 1, SEV_HIGH: 2}


def _max_sev(*sevs: str) -> str:
    if not sevs:
        return SEV_LOW
    return max(sevs, key=lambda s: _SEV_ORDER.get(s, 0))


# ─── Result type ─────────────────────────────────────────────


@dataclass(frozen=True)
class Conflict:
    kind: str             # block_vs_allow | duplicate_policy | …
    policy_id: int        # the OTHER policy
    policy_name: str
    service: str
    reason_ar: str
    severity: str
    recommendation_ar: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind":             self.kind,
            "policy_id":        int(self.policy_id),
            "policy_name":      self.policy_name,
            "service":          self.service,
            "reason_ar":        self.reason_ar,
            "severity":         self.severity,
            "recommendation_ar": self.recommendation_ar,
        }


@dataclass(frozen=True)
class ConflictAnalysis:
    has_conflicts: bool
    severity: str
    conflicts: tuple[Conflict, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "has_conflicts": bool(self.has_conflicts),
            "severity":      self.severity,
            "conflicts":     [c.as_dict() for c in self.conflicts],
        }


# ─── Peer policy shape ───────────────────────────────────────


@dataclass(frozen=True)
class PeerPolicy:
    """Compact projection of another policy's row + its
    children. Callers build this from the per-service repo."""
    service: str
    id: int
    name: str
    slug: str
    router_id: int
    enabled: bool
    hotspot_profile: str = ""
    children: tuple[dict, ...] = ()  # targets/entries


# ─── Detection ───────────────────────────────────────────────


def _normalize(v: str) -> str:
    return (v or "").strip().lower()


def _block_vs_allow(
    *, current_service: str, current_router: int,
    current_children: Iterable[dict],
    peers: Iterable[PeerPolicy],
) -> list[Conflict]:
    """Same host appears as web_block target AND walled_garden
    allow entry on the same router — operator almost certainly
    doesn't want both."""
    out: list[Conflict] = []
    current_values = {
        _normalize(c.get("normalized_value")
                   or c.get("value") or "")
        for c in current_children
        if (c.get("status") in (None, "active"))
        and (c.get("normalized_value") or c.get("value"))
    }
    if not current_values:
        return out
    for p in peers:
        if p.router_id != current_router:
            continue
        if not p.enabled:
            continue
        # Pair must be a block + an allow (any direction).
        if p.service == "web_block" and current_service == "walled_garden":
            target_kind = "حظر"
            peer_kind   = "إستثناء"
        elif p.service == "walled_garden" and current_service == "web_block":
            target_kind = "إستثناء"
            peer_kind   = "حظر"
        else:
            continue
        peer_values = {
            _normalize(c.get("normalized_value")
                       or c.get("value") or "")
            for c in (p.children or ())
            if c.get("status") in (None, "active")
        }
        clash = current_values & peer_values
        if not clash:
            continue
        sample = sorted(clash)[:3]
        out.append(Conflict(
            kind="block_vs_allow",
            policy_id=p.id, policy_name=p.name, service=p.service,
            severity=SEV_HIGH,
            reason_ar=(
                f"الوجهات {sample} مُدرجة في «{peer_kind}» "
                f"في السياسة الأخرى وفي «{target_kind}» هنا — "
                "تعارض مباشر سيُلغي أحدهما الآخر."
            ),
            recommendation_ar=(
                "احذف الوجهات المشتركة من إحدى السياستين قبل "
                "التطبيق."
            ),
        ))
    return out


def _duplicate_policy(
    *, current_service: str, current_router: int,
    current_slug: str, current_id: int,
    peers: Iterable[PeerPolicy],
) -> list[Conflict]:
    out: list[Conflict] = []
    if not current_slug:
        return out
    for p in peers:
        if p.id == current_id:
            continue
        if p.service != current_service:
            continue
        if p.router_id != current_router:
            continue
        if (p.slug or "").strip().lower() == \
                current_slug.strip().lower():
            out.append(Conflict(
                kind="duplicate_policy",
                policy_id=p.id, policy_name=p.name, service=p.service,
                severity=SEV_HIGH,
                reason_ar=(
                    f"سياسة أخرى بنفس المعرّف ({p.slug}) "
                    "موجودة على نفس الراوتر."
                ),
                recommendation_ar=(
                    "أعد تسمية إحدى السياستين قبل التطبيق."
                ),
            ))
    return out


def _overlapping_router(
    *, current_service: str, current_router: int,
    current_id: int, peers: Iterable[PeerPolicy],
) -> list[Conflict]:
    out: list[Conflict] = []
    for p in peers:
        if p.id == current_id:
            continue
        if p.service != current_service:
            continue
        if p.router_id != current_router:
            continue
        if not p.enabled:
            continue
        out.append(Conflict(
            kind="overlapping_router",
            policy_id=p.id, policy_name=p.name, service=p.service,
            severity=SEV_MEDIUM,
            reason_ar=(
                "سياسة أخرى من نفس النوع تعمل على نفس الراوتر — "
                "قد يتراكم تأثيرها."
            ),
            recommendation_ar=(
                "راجع السياسة الأخرى وتأكَّد من أن السلوك المركَّب "
                "هو ما تريده."
            ),
        ))
    return out


def _overlapping_targets(
    *, current_service: str, current_router: int,
    current_id: int, current_children: Iterable[dict],
    peers: Iterable[PeerPolicy],
) -> list[Conflict]:
    """Same destination already managed by another policy of
    the same service on the same router. Low severity — repo
    idempotency keeps the wire-level state consistent — but
    operator deserves to know."""
    out: list[Conflict] = []
    if current_service not in {"web_block", "walled_garden"}:
        return out
    current_values = {
        _normalize(c.get("normalized_value")
                   or c.get("value") or "")
        for c in current_children
        if c.get("status") in (None, "active")
    }
    if not current_values:
        return out
    for p in peers:
        if p.id == current_id:
            continue
        if p.service != current_service:
            continue
        if p.router_id != current_router:
            continue
        peer_values = {
            _normalize(c.get("normalized_value")
                       or c.get("value") or "")
            for c in (p.children or ())
            if c.get("status") in (None, "active")
        }
        overlap = current_values & peer_values
        if not overlap:
            continue
        sample = sorted(overlap)[:3]
        kind = ("overlapping_target"
                if current_service == "web_block"
                else "overlapping_entry")
        out.append(Conflict(
            kind=kind,
            policy_id=p.id, policy_name=p.name, service=p.service,
            severity=SEV_LOW,
            reason_ar=(
                f"الوجهات {sample} مدرجة أيضاً في السياسة الأخرى."
            ),
            recommendation_ar=(
                "تأكَّد أن التكرار مقصود — أو احتفظ بمصدر واحد للحقيقة."
            ),
        ))
    return out


def _hotspot_profile_overlap(
    *, current_service: str, current_router: int,
    current_id: int, current_hotspot_profile: str,
    peers: Iterable[PeerPolicy],
) -> list[Conflict]:
    out: list[Conflict] = []
    if current_service != "walled_garden":
        return out
    p_profile = (current_hotspot_profile or "").strip()
    if not p_profile:
        return out
    for p in peers:
        if p.id == current_id:
            continue
        if p.service != "walled_garden":
            continue
        if p.router_id != current_router:
            continue
        if (p.hotspot_profile or "").strip() == p_profile:
            out.append(Conflict(
                kind="hotspot_profile",
                policy_id=p.id, policy_name=p.name,
                service=p.service,
                severity=SEV_MEDIUM,
                reason_ar=(
                    f"سياسة walled-garden أخرى تستهدف نفس "
                    f"profile الـ Hotspot ({p_profile})."
                ),
                recommendation_ar=(
                    "ادمج السياستين أو ميِّز الـ profile لكل سياسة."
                ),
            ))
    return out


# ─── Public API ──────────────────────────────────────────────


def analyze(
    *,
    current_service: str,
    current_policy: dict,
    current_children: Iterable[dict] = (),
    peers: Iterable[PeerPolicy] = (),
) -> ConflictAnalysis:
    """Compute the conflict report for one policy against its
    peers. Pure — no IO."""
    peers = tuple(peers)
    current_children = tuple(current_children)
    current_id = int(current_policy.get("id") or 0)
    current_router = int(current_policy.get("router_id") or 0)
    current_slug = str(current_policy.get("slug") or "")
    current_hp = str(current_policy.get("hotspot_profile") or "")

    conflicts: list[Conflict] = []
    conflicts.extend(_block_vs_allow(
        current_service=current_service,
        current_router=current_router,
        current_children=current_children,
        peers=peers,
    ))
    conflicts.extend(_duplicate_policy(
        current_service=current_service,
        current_router=current_router,
        current_slug=current_slug,
        current_id=current_id,
        peers=peers,
    ))
    conflicts.extend(_overlapping_router(
        current_service=current_service,
        current_router=current_router,
        current_id=current_id,
        peers=peers,
    ))
    conflicts.extend(_overlapping_targets(
        current_service=current_service,
        current_router=current_router,
        current_id=current_id,
        current_children=current_children,
        peers=peers,
    ))
    conflicts.extend(_hotspot_profile_overlap(
        current_service=current_service,
        current_router=current_router,
        current_id=current_id,
        current_hotspot_profile=current_hp,
        peers=peers,
    ))

    if not conflicts:
        return ConflictAnalysis(
            has_conflicts=False, severity=SEV_LOW,
            conflicts=(),
        )
    overall = _max_sev(*(c.severity for c in conflicts))
    return ConflictAnalysis(
        has_conflicts=True, severity=overall,
        conflicts=tuple(conflicts),
    )


__all__ = [
    "SEV_LOW", "SEV_MEDIUM", "SEV_HIGH",
    "Conflict", "ConflictAnalysis",
    "PeerPolicy",
    "analyze",
]
