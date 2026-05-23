"""npc_remote_access — Remote MikroTik Access foundation.

Pure module. No DB, no Flask, no MikroTik client. The actual
apply step (Phase 5) layers on top of this with credentials
+ network IO; everything here is text-and-data analysis the
planner / renderer in Phase 2 will consume.

What this module owns:
  * Catalogue of admin services (winbox / ssh / api / ...) we
    can toggle on the input chain.
  * Validation for an `expires_at` timestamp (must be in the
    future, must parse, must be a reasonable horizon).
  * Source-allowlist validator — refuses to apply a no-source-
    list + no-expiry policy because that's a permanent open
    admin port to the internet.
  * Risk assessment dataclass so the UI can render a calm-
    /caution-/danger- pill before the operator hits apply.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Iterable, Optional


# ─── Service catalogue ───────────────────────────────────────


@dataclass(frozen=True)
class AdminService:
    key: str        # stable identifier (matches DB column)
    label_ar: str   # operator-facing label
    port: int
    protocol: str   # 'tcp' or 'udp'
    risk: str       # 'low' | 'medium' | 'high'
    notes_ar: str = ""


# The five surfaces NPC can open. Risk ratings are intentional:
# winbox + api over plain TCP are the loudest red-flag protocols
# when exposed without a source allowlist; HTTPS webfig is the
# least objectionable because TLS at least encrypts the auth
# leg. SSH is medium because key-only ssh is uncommonly
# configured on customer MikroTik boxes — we treat it as
# password-auth-by-default.
SERVICES: tuple[AdminService, ...] = (
    AdminService("winbox",        "ويـن‌بـوكس",   8291, "tcp", "high",
                 "بروتوكول خاص بـ MikroTik؛ مقفل افتراضياً."),
    AdminService("ssh",            "SSH",         22,   "tcp", "medium",
                 "كلمة سرّ MikroTik القياسية تنطبق."),
    AdminService("api",            "API",         8728, "tcp", "high",
                 "غير مشفّر — لا ينصح إلا داخل WG."),
    AdminService("api_ssl",        "API (SSL)",   8729, "tcp", "medium"),
    AdminService("webfig_http",    "WebFig HTTP", 80,   "tcp", "high",
                 "غير مشفّر — لا ينصح."),
    AdminService("webfig_https",   "WebFig HTTPS",443,  "tcp", "low"),
)

# Quick lookup by key.
_SERVICES_BY_KEY: dict[str, AdminService] = {
    s.key: s for s in SERVICES
}


def get_service(key: str) -> Optional[AdminService]:
    """Look up an admin-service descriptor by its stable key.
    Returns None if unknown — callers decide whether unknown
    is an error (typically yes)."""
    return _SERVICES_BY_KEY.get(key)


def list_services() -> tuple[AdminService, ...]:
    """Catalogue in stable UI ordering."""
    return SERVICES


# ─── expires_at validation ───────────────────────────────────


# Maximum allowed expiry horizon. Operators can extend by
# renewing; the cap keeps "I'll fix it later" remote-access
# rules from becoming permanent open doors.
MAX_EXPIRY_HOURS = 24 * 30   # 30 days
MIN_EXPIRY_MINUTES = 5       # anything shorter is friction


@dataclass(frozen=True)
class ExpiryValidation:
    ok: bool
    reason: str = ""
    parsed: Optional[datetime] = None
    ttl_minutes: int = 0


_ISO_TZ_SUFFIX = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse a permissive ISO-8601. Accepts a trailing `Z` and
    falls back to UTC if no timezone is supplied (the form
    posts naive ISO from `<input type="datetime-local">`)."""
    if not value:
        return None
    raw = str(value).strip()
    try:
        # Python's fromisoformat dislikes `Z`; substitute first.
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate_expires_at(
    raw: str, *, now: Optional[datetime] = None,
) -> ExpiryValidation:
    """Pure validator for the operator's chosen expiry time.

    `now` is injectable so tests don't have to monkeypatch
    `datetime.utcnow()`. Returns a dataclass with the parsed
    datetime + remaining ttl in minutes, or an Arabic reason
    on failure."""
    if raw is None or str(raw).strip() == "":
        return ExpiryValidation(
            ok=False,
            reason=(
                "حدّد وقت انتهاء للوصول، أو فعّل قائمة عناوين "
                "مصدر مقيّدة."
            ),
        )
    dt = _parse_iso(str(raw))
    if dt is None:
        return ExpiryValidation(
            ok=False,
            reason="صيغة الوقت غير صالحة (ISO-8601 مطلوب).",
        )
    now_utc = (now or datetime.now(timezone.utc))
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    delta = dt - now_utc
    minutes = int(delta.total_seconds() // 60)
    if minutes < MIN_EXPIRY_MINUTES:
        return ExpiryValidation(
            ok=False,
            reason=(
                f"الانتهاء يجب أن يكون بعد "
                f"{MIN_EXPIRY_MINUTES} دقائق على الأقل."
            ),
            parsed=dt,
            ttl_minutes=minutes,
        )
    if delta > timedelta(hours=MAX_EXPIRY_HOURS):
        return ExpiryValidation(
            ok=False,
            reason=(
                f"الانتهاء لا يمكن أن يتجاوز "
                f"{MAX_EXPIRY_HOURS // 24} يوماً."
            ),
            parsed=dt,
            ttl_minutes=minutes,
        )
    return ExpiryValidation(
        ok=True, parsed=dt, ttl_minutes=minutes,
    )


# ─── source allowlist validation ─────────────────────────────


# Address-list name — RouterOS allows letters, digits, dashes,
# underscores. We cap length at 60.
_ADDR_LIST_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


@dataclass(frozen=True)
class SourceValidation:
    ok: bool
    reason: str = ""
    cleaned: str = ""


def validate_source_address_list(raw: str) -> SourceValidation:
    """Validate the optional source-IP allowlist name.

    Empty string is allowed (no source restriction) — the
    overall policy validator will then insist on an expiry.
    """
    if raw is None or str(raw).strip() == "":
        return SourceValidation(ok=True, cleaned="")
    cleaned = str(raw).strip()
    if not _ADDR_LIST_NAME_RE.match(cleaned):
        return SourceValidation(
            ok=False,
            reason=(
                "اسم قائمة العناوين يجب أن يحوي أحرفاً "
                "إنجليزية أو أرقاماً أو شرطات فقط."
            ),
        )
    return SourceValidation(ok=True, cleaned=cleaned)


# ─── Risk assessment ─────────────────────────────────────────


RISK_LOW    = "low"
RISK_MEDIUM = "medium"
RISK_HIGH   = "high"

# Ordering — used by `_max_risk` to pick the highest.
_RISK_ORDER = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2}


def _max_risk(*risks: str) -> str:
    if not risks:
        return RISK_LOW
    return max(risks, key=lambda r: _RISK_ORDER.get(r, 0))


@dataclass(frozen=True)
class AccessAssessment:
    risk: str                  # RISK_*
    enabled_services: tuple[str, ...]
    warnings_ar: tuple[str, ...] = field(default_factory=tuple)
    blockers_ar: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_applicable(self) -> bool:
        """`True` when no blocker is set. Warnings are
        operator-visible but don't prevent apply."""
        return not self.blockers_ar


def assess_policy(
    *,
    allow_winbox: bool = False,
    allow_ssh: bool = False,
    allow_api: bool = False,
    allow_api_ssl: bool = False,
    allow_webfig_http: bool = False,
    allow_webfig_https: bool = False,
    source_address_list: str = "",
    expires_at: str = "",
    now: Optional[datetime] = None,
) -> AccessAssessment:
    """Roll-up risk assessment for a proposed remote-access
    policy. Pure — no IO. Returns warnings (apply OK) and
    blockers (apply refused) separately so the UI can render
    distinct affordances."""
    toggles = {
        "winbox":        bool(allow_winbox),
        "ssh":           bool(allow_ssh),
        "api":           bool(allow_api),
        "api_ssl":       bool(allow_api_ssl),
        "webfig_http":   bool(allow_webfig_http),
        "webfig_https":  bool(allow_webfig_https),
    }
    enabled = tuple(k for k, v in toggles.items() if v)
    warnings: list[str] = []
    blockers: list[str] = []

    if not enabled:
        blockers.append("يجب اختيار خدمة واحدة على الأقل لفتحها.")

    # Stack risks from each enabled service.
    risks: list[str] = []
    for k in enabled:
        svc = get_service(k)
        if svc is not None:
            risks.append(svc.risk)

    src = validate_source_address_list(source_address_list)
    if not src.ok:
        blockers.append(src.reason)

    exp = validate_expires_at(expires_at, now=now)
    has_source = bool(src.cleaned)
    has_expiry = exp.ok

    # The big rule: no source allowlist AND no expiry = no go.
    if not has_source and not has_expiry:
        blockers.append(
            "يجب تحديد قائمة عناوين مصدر، أو وقت انتهاء، "
            "أو كليهما."
        )

    # Soft warning: SSH + plain API on a public-source policy.
    if (allow_api or allow_webfig_http) and not has_source:
        warnings.append(
            "تفعيل API أو WebFig HTTP بدون قائمة مصدر "
            "يفتح المنفذ للإنترنت كاملاً."
        )

    # Source allowlist alone is OK but flag long expiries.
    if has_source and not has_expiry:
        warnings.append(
            "لا يوجد وقت انتهاء — لن تُحذف القاعدة تلقائياً."
        )

    risk = _max_risk(*risks) if risks else RISK_LOW
    if not has_source and risk == RISK_LOW:
        # No source list elevates risk — even a low-risk
        # service deserves a notch when the world can reach it.
        risk = RISK_MEDIUM

    return AccessAssessment(
        risk=risk,
        enabled_services=enabled,
        warnings_ar=tuple(warnings),
        blockers_ar=tuple(blockers),
    )


# ─── Tiny helpers for the planner (Phase 2) ──────────────────


def selected_ports(
    *,
    allow_winbox: bool = False,
    allow_ssh: bool = False,
    allow_api: bool = False,
    allow_api_ssl: bool = False,
    allow_webfig_http: bool = False,
    allow_webfig_https: bool = False,
) -> tuple[tuple[str, int, str], ...]:
    """Returns a stable tuple of (service_key, port, protocol)
    triples for the enabled toggles. The renderer turns each
    into one `/ip/firewall/filter add ...` line."""
    raw = (
        ("winbox",       allow_winbox),
        ("ssh",          allow_ssh),
        ("api",          allow_api),
        ("api_ssl",      allow_api_ssl),
        ("webfig_http",  allow_webfig_http),
        ("webfig_https", allow_webfig_https),
    )
    out: list[tuple[str, int, str]] = []
    for key, on in raw:
        if not on:
            continue
        svc = get_service(key)
        if svc is None:
            continue
        out.append((svc.key, svc.port, svc.protocol))
    return tuple(out)


def is_safe_source_cidr(value: str) -> bool:
    """`True` if a single string looks like a sane operator
    source — non-empty, non-RFC1918, non-blackhole IPv4 CIDR
    OR a bare IPv4."""
    s = (value or "").strip()
    if not s:
        return False
    try:
        if "/" in s:
            net = ipaddress.IPv4Network(s, strict=False)
            if net.prefixlen == 0:
                return False
            return not net.network_address.is_private
        addr = ipaddress.IPv4Address(s)
        return not addr.is_private
    except (ipaddress.AddressValueError, ValueError):
        return False


def safe_source_cidrs(values: Iterable[str]) -> tuple[str, ...]:
    """Filter an iterable down to entries that pass
    `is_safe_source_cidr`."""
    return tuple(v.strip() for v in values
                 if is_safe_source_cidr(v))


__all__ = [
    "AdminService", "SERVICES",
    "get_service", "list_services",
    "MAX_EXPIRY_HOURS", "MIN_EXPIRY_MINUTES",
    "ExpiryValidation", "validate_expires_at",
    "SourceValidation", "validate_source_address_list",
    "RISK_LOW", "RISK_MEDIUM", "RISK_HIGH",
    "AccessAssessment", "assess_policy",
    "selected_ports",
    "is_safe_source_cidr", "safe_source_cidrs",
]
