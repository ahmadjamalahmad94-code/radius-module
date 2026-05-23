"""npc_domain_analyzer — pure analysis of operator-supplied
destinations for the Network Policy Center.

Pure module. No DNS lookup, no socket, no Flask. Tests assert
this.

What it does:
  * Take a free-form operator input (one line per
    destination) and classify each one as DOMAIN, IPv4, CIDR,
    URL (which we then strip-and-classify), or INVALID.
  * Normalize each accepted entry (lowercase, no scheme, no
    trailing dot, no port, no path) so de-dup at the repo
    layer is straightforward.
  * Reject inputs the operator clearly did not mean to apply
    at the router firewall layer: IPv6 (we don't emit v6 rules
    yet), private RFC-1918 ranges that would self-block the
    LAN, the obvious blackhole `0.0.0.0/0`, and any wildcard
    glob that RouterOS can't address.

Three sub-services (remote_access / web_block / walled_garden)
share this analyzer because the input format is identical —
they only differ in how the planner translates an analyzed
entry into RouterOS verbs. The classifier here is the single
chokepoint where bad inputs get rejected with operator-facing
Arabic reasons.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Iterable


# Stable enum for the analyzer's verdicts. Mirrors VX2's
# `target_type` but adds INVALID + URL since the analyzer is
# the layer that strips schemes and rejects junk.
KIND_DOMAIN  = "domain"
KIND_IP      = "ip"
KIND_CIDR    = "cidr"
KIND_INVALID = "invalid"


# Operator-facing Arabic reason codes — used by the UI to
# display "why was this line rejected?" next to the input.
REASON_EMPTY              = "السطر فارغ."
REASON_HAS_WILDCARD       = "العلامة النجمية غير مدعومة هنا."
REASON_IPV6_UNSUPPORTED   = "العناوين IPv6 غير مدعومة بعد."
REASON_PRIVATE_RFC1918    = (
    "هذا العنوان من شبكة محلية خاصة "
    "(يحجبه قد يقطع الشبكة الداخلية)."
)
REASON_BLACKHOLE_CIDR     = "حظر 0.0.0.0/0 محظور — يقطع الإنترنت كاملاً."
REASON_BAD_DOMAIN         = "ليس عنواناً صالحاً (نطاق/IP/CIDR)."
REASON_OK                 = ""


@dataclass(frozen=True)
class AnalyzedEntry:
    raw: str
    kind: str                  # one of KIND_*
    normalized: str = ""       # the value we'd persist
    reason: str = ""           # Arabic; empty when kind != INVALID
    note: str = ""             # free-form aid (e.g. "stripped scheme")


@dataclass(frozen=True)
class AnalysisResult:
    accepted: tuple[AnalyzedEntry, ...] = field(default_factory=tuple)
    rejected: tuple[AnalyzedEntry, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.accepted) + len(self.rejected)

    def kind_counts(self) -> dict[str, int]:
        out = {KIND_DOMAIN: 0, KIND_IP: 0,
                KIND_CIDR: 0, KIND_INVALID: 0}
        for e in self.accepted:
            out[e.kind] = out.get(e.kind, 0) + 1
        for e in self.rejected:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out


# ─── Regexes ─────────────────────────────────────────────────


# A conservative DNS hostname — labels separated by dots, each
# 1-63 chars, allowed [a-z0-9-]. Underscores are intentionally
# excluded: RouterOS accepts them, but they're a strong signal
# the operator pasted a tracking id by mistake.
_HOSTNAME_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(
    rf"^{_HOSTNAME_LABEL}(\.{_HOSTNAME_LABEL})+$"
)

# URL/scheme detection so the analyzer can strip it before
# re-classifying. `https://example.com/path?x=1` becomes
# `example.com`.
_URL_RE = re.compile(
    r"^(?:https?|ftp)://"
    r"(?P<host>[^/:?\s]+)"
    r"(?::\d+)?(?:/.*)?$",
    re.IGNORECASE,
)

# Wildcards — RouterOS firewall address-lists don't accept `*`
# in dst-address; for hostnames the wildcard would need a regex
# at /walled-garden, which is opt-in elsewhere. For now reject
# them clearly so the operator chooses an alternative.
_HAS_WILDCARD_RE = re.compile(r"[*?]")


# ─── Helpers ─────────────────────────────────────────────────


def _strip_inline_comment(line: str) -> str:
    """Allow `# comment` at end of lines; returns the cleaned
    text without the comment. `;` is intentionally NOT a
    comment marker — RouterOS uses it as a statement
    separator."""
    if "#" in line:
        line = line.split("#", 1)[0]
    return line


def _normalize_domain(host: str) -> str:
    """Strip trailing dot, lowercase, strip leading `www.` is
    NOT done here — operators control subdomain handling at
    policy level (per VX2.6's `include_subdomains`)."""
    out = host.strip().lower().rstrip(".")
    return out


def _is_private_ip(addr: ipaddress.IPv4Address) -> bool:
    """RFC-1918 + RFC-3927 link-local + loopback + multicast.
    Includes the broadcast 255.255.255.255. The classifier
    treats these as INVALID because blocking them at the
    firewall would brick LAN reachability."""
    return (
        addr.is_private or addr.is_loopback
        or addr.is_link_local or addr.is_multicast
        or addr.is_unspecified or addr.is_reserved
        or addr.is_global is False
    )


def _classify_one(raw: str) -> AnalyzedEntry:
    """Single-entry classifier. Always returns an AnalyzedEntry;
    INVALID kind carries the Arabic reason."""
    line = _strip_inline_comment(raw or "").strip()
    if not line:
        return AnalyzedEntry(
            raw=raw or "", kind=KIND_INVALID,
            reason=REASON_EMPTY,
        )

    # Strip a URL scheme if present — recurse with the host.
    m_url = _URL_RE.match(line)
    if m_url:
        host = m_url.group("host")
        inner = _classify_one(host)
        if inner.kind != KIND_INVALID:
            return AnalyzedEntry(
                raw=raw, kind=inner.kind,
                normalized=inner.normalized,
                reason=REASON_OK,
                note="stripped scheme",
            )
        return AnalyzedEntry(
            raw=raw, kind=KIND_INVALID,
            reason=inner.reason or REASON_BAD_DOMAIN,
        )

    if _HAS_WILDCARD_RE.search(line):
        return AnalyzedEntry(
            raw=raw, kind=KIND_INVALID,
            reason=REASON_HAS_WILDCARD,
        )

    # IPv6 detection — explicit so the reason is precise.
    if ":" in line and not line.replace(":", "").isdigit():
        try:
            ipaddress.IPv6Address(line)
            return AnalyzedEntry(
                raw=raw, kind=KIND_INVALID,
                reason=REASON_IPV6_UNSUPPORTED,
            )
        except ipaddress.AddressValueError:
            pass  # not a v6 — fall through to the rest.

    # CIDR?
    if "/" in line:
        try:
            net = ipaddress.IPv4Network(line, strict=False)
        except (ipaddress.AddressValueError, ValueError):
            return AnalyzedEntry(
                raw=raw, kind=KIND_INVALID,
                reason=REASON_BAD_DOMAIN,
            )
        if net.prefixlen == 0:
            return AnalyzedEntry(
                raw=raw, kind=KIND_INVALID,
                reason=REASON_BLACKHOLE_CIDR,
            )
        if _is_private_ip(net.network_address):
            return AnalyzedEntry(
                raw=raw, kind=KIND_INVALID,
                reason=REASON_PRIVATE_RFC1918,
            )
        return AnalyzedEntry(
            raw=raw, kind=KIND_CIDR,
            normalized=str(net),
            reason=REASON_OK,
        )

    # Bare IPv4?
    try:
        addr = ipaddress.IPv4Address(line)
        if _is_private_ip(addr):
            return AnalyzedEntry(
                raw=raw, kind=KIND_INVALID,
                reason=REASON_PRIVATE_RFC1918,
            )
        return AnalyzedEntry(
            raw=raw, kind=KIND_IP,
            normalized=str(addr),
            reason=REASON_OK,
        )
    except ipaddress.AddressValueError:
        pass

    # Hostname?
    lower = line.lower().rstrip(".")
    if _HOSTNAME_RE.match(lower):
        return AnalyzedEntry(
            raw=raw, kind=KIND_DOMAIN,
            normalized=_normalize_domain(lower),
            reason=REASON_OK,
        )

    return AnalyzedEntry(
        raw=raw, kind=KIND_INVALID,
        reason=REASON_BAD_DOMAIN,
    )


# ─── Public API ──────────────────────────────────────────────


def analyze_line(raw: str) -> AnalyzedEntry:
    """Classify exactly one input line. Convenience for routes
    that validate single-field forms."""
    return _classify_one(raw)


def analyze_lines(lines: Iterable[str]) -> AnalysisResult:
    """Classify a batch — operator pasted N lines into a
    textarea. Empty lines and inline-comment-only lines are
    silently dropped (REASON_EMPTY rejects, but the UI can
    filter those out).

    Returns an AnalysisResult with two tuples: accepted +
    rejected, both ordered to match the input.
    """
    accepted: list[AnalyzedEntry] = []
    rejected: list[AnalyzedEntry] = []
    for ln in lines:
        # Skip blank lines silently — no operator-visible
        # rejection for whitespace.
        if not (ln or "").strip():
            continue
        entry = _classify_one(ln)
        if entry.kind == KIND_INVALID:
            rejected.append(entry)
        else:
            accepted.append(entry)
    return AnalysisResult(
        accepted=tuple(accepted),
        rejected=tuple(rejected),
    )


def analyze_text(text: str) -> AnalysisResult:
    """Same as `analyze_lines` but takes a single block of
    text and splits on `\\n` first. Useful for textarea form
    fields."""
    return analyze_lines((text or "").splitlines())


__all__ = [
    "KIND_DOMAIN", "KIND_IP", "KIND_CIDR", "KIND_INVALID",
    "REASON_EMPTY", "REASON_HAS_WILDCARD",
    "REASON_IPV6_UNSUPPORTED", "REASON_PRIVATE_RFC1918",
    "REASON_BLACKHOLE_CIDR", "REASON_BAD_DOMAIN",
    "AnalyzedEntry", "AnalysisResult",
    "analyze_line", "analyze_lines", "analyze_text",
]
