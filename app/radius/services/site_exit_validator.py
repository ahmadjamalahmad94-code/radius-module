"""site_exit_validator — VX2 input validation (pure).

Decides whether an operator-supplied target is acceptable as a
destination for selected-sites VPS exit routing. The contract:
**a value that returns valid=True is safe to hand to the planner;
a value that returns valid=False must never reach RouterOS.**

This is intentionally strict by default. Three explicit refusals
the feature was designed around:

  - 0.0.0.0/0  — would route everything through the VPS. Never
                  allowed, even in advanced mode.
  - *.example  — wildcard syntax. We use the include_subdomains
                  flag on the policy/target instead, so a literal
                  `*` is just noise that confuses the planner.
  - https://x  — URLs with a scheme/path/query. We don't silently
                  strip them because that's an attractive vector
                  for "I pasted google.com/maps and now Maps
                  exits through VPS." If the operator wants the
                  hostname they must paste only the hostname.

Private/reserved IPv4 ranges (10/8, 172.16/12, 192.168/16,
127/8, 169.254/16, multicast 224/4, reserved 240/4) are rejected
by default — they cannot be a "destination on the internet" by
definition. Set `advanced_mode=True` to opt in for lab setups.

IPv6 is out of scope for VX2 and is rejected as `unsupported`.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass


# Public — keep these strings stable, repos and UI key on them.
TARGET_TYPE_DOMAIN = "domain"
TARGET_TYPE_IP     = "ip"
TARGET_TYPE_CIDR   = "cidr"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    target_type: str       # "domain" | "ip" | "cidr" | ""
    normalized: str        # canonical form (lowercase, no trailing dot)
    original: str          # what the operator pasted, untouched
    reason: str = ""       # rejection reason when invalid


# Hard rejections — these never pass even in advanced_mode.
_HARD_REJECT_CIDRS = ("0.0.0.0/0",)

# RFC-1035-ish domain validator. Allows ASCII labels of 1-63
# chars (letters/digits/hyphen, not starting/ending with hyphen),
# 2+ labels, TLD must contain at least one letter (so numeric-
# only TLDs like ".42" are refused even though they parse).
_LABEL_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$"
)
_TLD_HAS_LETTER_RE = re.compile(r"[A-Za-z]")

# Wildcard prefix used by some Mikrotik config dumps. The
# feature uses the include_subdomains flag instead.
_WILDCARD_PREFIX = "*."

# URL schemes we reject outright — operators must paste host.
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://")


def _strip_inline_comment(value: str) -> str:
    """Strip a trailing space-then-comment (common in operator
    pastes from Mikrotik exports)."""
    if not value:
        return value
    for token in (" #", "\t#"):
        idx = value.find(token)
        if idx >= 0:
            value = value[:idx]
    return value.strip()


def _looks_like_cidr(value: str) -> bool:
    return "/" in value and not value.endswith("/")


def _looks_like_ipv4(value: str) -> bool:
    """Rough check before handing to ipaddress (avoids treating
    `1.2.3` or random shapes as IPv4 just because they parse)."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            return False
    return True


def _validate_ipv4(
    value: str, *, advanced_mode: bool,
) -> ValidationResult:
    try:
        ip = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        return ValidationResult(
            False, "", "", value,
            reason=f"invalid IPv4 address: {exc}",
        )
    if not advanced_mode and (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    ):
        return ValidationResult(
            False, "", "", value,
            reason=(
                "private/reserved IPv4 is rejected by default — "
                "enable advanced_mode if this is intentional"
            ),
        )
    return ValidationResult(
        True, TARGET_TYPE_IP, str(ip), value, reason="",
    )


def _validate_cidr(
    value: str, *, advanced_mode: bool,
) -> ValidationResult:
    if value in _HARD_REJECT_CIDRS:
        return ValidationResult(
            False, "", "", value,
            reason=(
                "0.0.0.0/0 is never accepted — VX2 must NEVER "
                "route all traffic through the VPS"
            ),
        )
    try:
        # strict=False lets `1.2.3.4/24` normalize to `1.2.3.0/24`
        # instead of throwing on host bits — operator-friendly.
        net = ipaddress.IPv4Network(value, strict=False)
    except (ipaddress.AddressValueError,
             ipaddress.NetmaskValueError, ValueError) as exc:
        return ValidationResult(
            False, "", "", value,
            reason=f"invalid CIDR: {exc}",
        )
    # 0.0.0.0/0 again after normalisation (belt + braces).
    if net.prefixlen == 0:
        return ValidationResult(
            False, "", "", value,
            reason="catch-all 0.0.0.0/0 is never accepted",
        )
    if not advanced_mode and (
        net.is_private or net.is_loopback or net.is_link_local
        or net.is_multicast or net.is_reserved or net.is_unspecified
    ):
        return ValidationResult(
            False, "", "", value,
            reason=(
                "private/reserved CIDR is rejected by default — "
                "enable advanced_mode if this is intentional"
            ),
        )
    return ValidationResult(
        True, TARGET_TYPE_CIDR, str(net), value, reason="",
    )


def _validate_domain(value: str) -> ValidationResult:
    # Strip trailing dot (FQDN form). Keep IDN as-is for now —
    # an explicit phase will add punycode normalization.
    host = value.strip(".")
    if not host:
        return ValidationResult(
            False, "", "", value, reason="empty hostname")
    if host.startswith(_WILDCARD_PREFIX) or "*" in host:
        return ValidationResult(
            False, "", "", value,
            reason=(
                "wildcard syntax is not accepted — "
                "use include_subdomains=True instead"
            ),
        )
    if len(host) > 253:
        return ValidationResult(
            False, "", "", value,
            reason=f"hostname too long ({len(host)} > 253)",
        )
    labels = host.split(".")
    if len(labels) < 2:
        return ValidationResult(
            False, "", "", value,
            reason=("single-label hostname (no TLD) — "
                     "VX2 routes only fully-qualified names"),
        )
    for lbl in labels:
        if not _LABEL_RE.match(lbl):
            return ValidationResult(
                False, "", "", value,
                reason=f"invalid label segment: {lbl!r}",
            )
    if not _TLD_HAS_LETTER_RE.search(labels[-1]):
        return ValidationResult(
            False, "", "", value,
            reason=f"TLD must contain a letter: {labels[-1]!r}",
        )
    return ValidationResult(
        True, TARGET_TYPE_DOMAIN, host.lower(), value, reason="",
    )


# ─── Public API ──────────────────────────────────────────────


def validate(
    value: str, *, advanced_mode: bool = False,
) -> ValidationResult:
    """Single entry point. Returns ValidationResult — never
    raises for bad operator input."""
    if value is None:
        return ValidationResult(
            False, "", "", "", reason="empty input")
    raw = value
    cleaned = _strip_inline_comment(str(value)).strip()
    if not cleaned:
        return ValidationResult(
            False, "", "", raw, reason="empty input")

    # URL with scheme/path — refuse loudly. Operator must paste
    # only the hostname (the planner needs only the host anyway).
    if _URL_SCHEME_RE.search(cleaned):
        return ValidationResult(
            False, "", "", raw,
            reason=(
                "URLs with scheme/path are rejected — paste the "
                "bare hostname instead"
            ),
        )
    if "/" in cleaned and not _looks_like_cidr(cleaned):
        # Path-like remainder after hostname — also rejected.
        return ValidationResult(
            False, "", "", raw,
            reason=(
                "value contains a path — paste only hostname, "
                "IP, or CIDR"
            ),
        )
    if "?" in cleaned or "#" in cleaned or " " in cleaned:
        return ValidationResult(
            False, "", "", raw,
            reason=(
                "value contains URL query/fragment/whitespace — "
                "paste only hostname, IP, or CIDR"
            ),
        )

    # Plain refusals before dispatch.
    if cleaned in _HARD_REJECT_CIDRS:
        return ValidationResult(
            False, "", "", raw,
            reason="0.0.0.0/0 is never accepted",
        )

    # IPv6 — out of scope for VX2.
    if ":" in cleaned and not cleaned.replace(":", "").isalnum():
        # Defensive — proper IPv6 detection below.
        pass
    if ":" in cleaned:
        try:
            ipaddress.IPv6Address(cleaned.split("/")[0])
            return ValidationResult(
                False, "", "", raw,
                reason="IPv6 is unsupported in VX2",
            )
        except (ipaddress.AddressValueError, ValueError):
            pass  # fall through to other validators

    if _looks_like_cidr(cleaned):
        return _validate_cidr(cleaned, advanced_mode=advanced_mode)
    if _looks_like_ipv4(cleaned):
        return _validate_ipv4(cleaned, advanced_mode=advanced_mode)
    return _validate_domain(cleaned)


__all__ = [
    "TARGET_TYPE_DOMAIN", "TARGET_TYPE_IP", "TARGET_TYPE_CIDR",
    "ValidationResult", "validate",
]
