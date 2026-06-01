"""RouterOS version → capability matrix (single source of truth).

The product talks to MikroTik over the legacy binary API (port 8728/8729),
which works on RouterOS **6.43+** and all 7.x. Most feature scripts
(Hotspot, PPPoE, RADIUS, CoA, IP pools) are identical across v6 and v7.

The ONE capability that differs and matters for provisioning is **WireGuard**,
which is RouterOS **7-only**. A v6 router therefore cannot use the automatic
"VPN mode" (WireGuard tunnel for routers behind NAT) and must be reached by a
direct address (public IP / port-forward) or use the DHCP-push fallback.

This module centralises that knowledge so routes, the provisioner, and the
setup wizard all agree on what a given version can do — and so a live router's
reported version (e.g. from ``/system/resource``) can be parsed and verified
against the operator's selection.

Pure functions only — no DB, no I/O. Trivially unit-testable.
"""
from __future__ import annotations

import re

# Binary API auth (plain name+password in one sentence) needs 6.43+.
MIN_SUPPORTED_VERSION = "6.43"
WIREGUARD_MIN_MAJOR = 7
# SSTP and L2TP/IPsec exist on both v6 and v7; for HobeRadius they are the
# v6 strategy (SSTP = management, L2TP/IPsec = optional traffic).
SSTP_MIN_MAJOR = 6
L2TP_IPSEC_MIN_MAJOR = 6
# PPTP exists on v6/v7 but is INSECURE (MS-CHAPv2 is broken). It is offered
# only as an explicit Legacy traffic option — never recommended, never default.
PPTP_MIN_MAJOR = 6

# Tunnel-type vocabularies (stored per router; see migration 092).
MANAGEMENT_TUNNEL_TYPES = ("wireguard", "sstp_mgmt", "direct", "none")
TRAFFIC_TUNNEL_TYPES = (
    "wireguard_traffic", "l2tp_ipsec_traffic", "pptp_traffic", "none",
)
# Traffic protocols the operator can pick for a v6 router's traffic tunnel.
# L2TP/IPsec is recommended; PPTP is Legacy/insecure (opt-in only).
TRAFFIC_PROTOCOLS = ("l2tp_ipsec", "pptp")
TRAFFIC_MODES = (
    "disabled", "full_tunnel", "policy_routing",
    "selected_pool", "selected_subscribers",
)


def parse_major(version: object) -> int | None:
    """Extract the major version number from any RouterOS version string.

    Accepts the values RouterOS reports (``"6.49.7"``, ``"7.11.2 (stable)"``)
    as well as the bare major the UI uses (``"6"``, ``"7"``, ``6``, ``7``).
    Returns the integer major, or ``None`` if nothing parseable is found.

    >>> parse_major("6.49.7")
    6
    >>> parse_major("7.11.2 (stable)")
    7
    >>> parse_major("7")
    7
    >>> parse_major(6)
    6
    >>> parse_major("") is None
    True
    """
    if version is None:
        return None
    if isinstance(version, int):
        return version if version > 0 else None
    text = str(version).strip()
    match = re.search(r"\d+", text)
    if not match:
        return None
    return int(match.group(0))


def supports_wireguard(version: object) -> bool:
    """Whether this RouterOS version can run WireGuard (7+ only).

    Unknown/unparseable versions are treated conservatively as **not**
    supporting WireGuard, so callers never render a v7-only block for a
    router whose version they cannot confirm.
    """
    major = parse_major(version)
    return major is not None and major >= WIREGUARD_MIN_MAJOR


def requires_direct_address(version: object) -> bool:
    """Whether the router must be reached by a direct address.

    True for v6 (and unknown): no WireGuard means no automatic NAT-bypass, so
    the operator must supply a reachable address or use the DHCP-push fallback.
    """
    return not supports_wireguard(version)


def connection_modes(version: object) -> list[str]:
    """The connection modes available for this version, best first.

    - v7: ``vpn`` (WireGuard, works behind NAT) + ``direct`` + ``dhcp_push``
    - v6/unknown: ``direct`` + ``dhcp_push`` only (no ``vpn``)
    """
    if supports_wireguard(version):
        return ["vpn", "direct", "dhcp_push"]
    return ["direct", "dhcp_push"]


def detect_major_from_resource(resource: object) -> int | None:
    """Parse the major version from a ``/system/resource`` reply.

    RouterOS returns the running version in the ``version`` field of
    ``/system/resource/print`` (e.g. ``{"version": "6.49.7 (stable)"}``).
    Accepts a dict (single row) or a list of dict rows. Returns the major or
    ``None`` if absent — this is the live-detection primitive the wizard can
    use to verify the operator's selection against the real router.
    """
    row = None
    if isinstance(resource, dict):
        row = resource
    elif isinstance(resource, (list, tuple)) and resource:
        first = resource[0]
        row = first if isinstance(first, dict) else None
    if not row:
        return None
    return parse_major(row.get("version"))


def summary(version: object) -> dict:
    """UI-friendly capability summary for a version (for badges/hints)."""
    major = parse_major(version)
    wg = supports_wireguard(version)
    if wg:
        note = "RouterOS 7: يدعم WireGuard — يمكن إضافته خلف NAT تلقائيًا (وضع VPN)."
    else:
        note = (
            "RouterOS 6: لا يدعم WireGuard. استخدم عنوانًا مباشرًا "
            "(IP عام / منفذ موجّه) أو وضع دفع DHCP للوصول خلف NAT."
        )
    return {
        "major": major,
        "wireguard": wg,
        "modes": connection_modes(version),
        "requires_direct_address": requires_direct_address(version),
        "note_ar": note,
    }


# ─────────────────────────────────────────────────────────────────────────
# v6 SSTP-management + L2TP/IPsec-traffic strategy
#
# Decision (see docs/router_vpn/ROUTEROS_V6_VPN_STRATEGY.md):
#   v7 → WireGuard management (unchanged).
#   v6 → SSTP management tunnel (always-on, management ONLY, never default
#        route) + OPTIONAL L2TP/IPsec traffic tunnel (IP-change / selected
#        routing). The two coexist; only one tunnel may own the default route.
#   PPTP is never recommended.
# ─────────────────────────────────────────────────────────────────────────


def parse_routeros_major(version: object) -> str | None:
    """Major version as a **string** ("6"/"7"/None).

    The spec and several existing string comparisons (``in ("6", "7")``) use
    the bare major as text, while :func:`parse_major` returns an int. This is
    the string-typed companion so call sites can stay consistent without
    sprinkling ``str(...)`` everywhere.

    >>> parse_routeros_major("6.49.7")
    '6'
    >>> parse_routeros_major("7.11.2 (stable)")
    '7'
    >>> parse_routeros_major("7.15rc") is None
    False
    >>> parse_routeros_major("invalid") is None
    True
    """
    major = parse_major(version)
    return str(major) if major is not None else None


def supports_sstp_mgmt(version: object) -> bool:
    """Whether SSTP can be used as the management tunnel (6.x and 7.x).

    Unknown/unparseable versions return False — SSTP is only offered for a
    version we can confirm (the operator may still pick a legacy-compatible
    mode explicitly elsewhere).
    """
    major = parse_major(version)
    return major is not None and major >= SSTP_MIN_MAJOR


def supports_l2tp_ipsec_traffic(version: object) -> bool:
    """Whether L2TP/IPsec can be used as the traffic tunnel (6.x and 7.x)."""
    major = parse_major(version)
    return major is not None and major >= L2TP_IPSEC_MIN_MAJOR


def supports_pptp_traffic(version: object) -> bool:
    """Whether PPTP can be used as a (Legacy/insecure) traffic tunnel.

    Available on 6.x/7.x, but PPTP encryption is broken — callers MUST treat
    it as opt-in only and surface the insecurity warning. Never recommend it.
    """
    major = parse_major(version)
    return major is not None and major >= PPTP_MIN_MAJOR


def recommended_management_tunnel(version: object) -> str:
    """The recommended management tunnel for a version.

    v7 → ``wireguard`` · v6 → ``sstp_mgmt`` · unknown → ``manual_review``.
    """
    if supports_wireguard(version):
        return "wireguard"
    if supports_sstp_mgmt(version):
        return "sstp_mgmt"
    return "manual_review"


def recommended_traffic_tunnel(version: object) -> str:
    """The recommended OPTIONAL traffic tunnel for a version.

    v7 → ``wireguard_traffic`` (WireGuard already carries traffic) ·
    v6 → ``l2tp_ipsec_traffic`` · unknown → ``manual_review``.
    Traffic tunnels are always opt-in; this is only the default offered.
    """
    if supports_wireguard(version):
        return "wireguard_traffic"
    if supports_l2tp_ipsec_traffic(version):
        return "l2tp_ipsec_traffic"
    return "manual_review"


def connection_modes_for_version(version: object) -> list[str]:
    """Connection modes offered for a version (richer than connection_modes).

    - v7: ``vpn`` (WireGuard) + ``direct`` + ``dhcp_push``
    - v6: ``sstp_mgmt`` + ``l2tp_ipsec_traffic`` + ``direct`` + ``dhcp_push``
          (NO ``vpn`` — WireGuard is v7-only)
    - unknown: ``direct`` + ``dhcp_push``
    """
    if supports_wireguard(version):
        return ["vpn", "direct", "dhcp_push"]
    if supports_sstp_mgmt(version):
        return ["sstp_mgmt", "l2tp_ipsec_traffic", "direct", "dhcp_push"]
    return ["direct", "dhcp_push"]


def _allowed_management(version: object) -> set[str]:
    allowed = {"direct", "none"}
    if supports_wireguard(version):
        allowed.add("wireguard")
    if supports_sstp_mgmt(version):
        allowed.add("sstp_mgmt")
    return allowed


def _allowed_traffic(version: object) -> set[str]:
    allowed = {"none"}
    if supports_wireguard(version):
        allowed.add("wireguard_traffic")
    if supports_l2tp_ipsec_traffic(version):
        allowed.add("l2tp_ipsec_traffic")
    if supports_pptp_traffic(version):
        allowed.add("pptp_traffic")  # Legacy/insecure — allowed but warned
    return allowed


def validate_connection_plan(
    version: object,
    management_tunnel: str = "none",
    traffic_tunnel: str = "none",
    *,
    sstp_sets_default_route: bool = False,
    traffic_owns_default_route: bool = False,
    traffic_mode: str | None = None,
    full_tunnel_confirmed: bool = False,
    selected_pool: object = None,
) -> dict:
    """Validate a router's VPN connection plan against the version matrix.

    Returns ``{"valid": bool, "errors": [...], "warnings": [...]}`` where each
    entry is ``{"code": str, "message_ar": str}``. ``errors`` are blocking;
    ``warnings`` are advisory. This is the pure, version-aware gate; the
    per-router :func:`analyze_tunnel_conflicts` (script-builder layer) wraps it
    with live-router checks (interface clashes, existing marks, …).

    Enforced rules:
      - v6 + WireGuard (management or traffic) is blocked.
      - management / traffic tunnel must be valid for the version.
      - a traffic tunnel requires a management tunnel (never traffic-only).
      - SSTP management must NOT own the default route.
      - only ONE tunnel may own the default route.
      - full_tunnel traffic requires explicit confirmation.
      - selected_pool traffic requires a pool.
      - SSTP on v7 is allowed but discouraged (WireGuard is preferred).
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(code: str, msg: str) -> None:
        errors.append({"code": code, "message_ar": msg})

    def warn(code: str, msg: str) -> None:
        warnings.append({"code": code, "message_ar": msg})

    mgmt = (management_tunnel or "none").strip() or "none"
    traffic = (traffic_tunnel or "none").strip() or "none"
    wg = supports_wireguard(version)

    # WireGuard on v6 — blocking for either role.
    if mgmt == "wireguard" and not wg:
        err("routeros_v6_wireguard_not_supported",
            "RouterOS 6 لا يدعم WireGuard. استخدم SSTP للإدارة.")
    if traffic == "wireguard_traffic" and not wg:
        err("routeros_v6_wireguard_not_supported",
            "نفق ترافيك WireGuard غير مدعوم على RouterOS 6.")

    # Type / version validity.
    if mgmt not in MANAGEMENT_TUNNEL_TYPES:
        err("invalid_management_tunnel", f"نوع نفق إدارة غير معروف: {mgmt}")
    elif mgmt not in _allowed_management(version):
        err("management_not_supported_for_version",
            f"نفق الإدارة «{mgmt}» غير مدعوم على هذا الإصدار.")
    if traffic not in TRAFFIC_TUNNEL_TYPES:
        err("invalid_traffic_tunnel", f"نوع نفق ترافيك غير معروف: {traffic}")
    elif traffic not in _allowed_traffic(version):
        err("traffic_not_supported_for_version",
            f"نفق الترافيك «{traffic}» غير مدعوم على هذا الإصدار.")

    # A traffic tunnel can never be the only tunnel — management must stay up.
    if traffic != "none" and mgmt == "none":
        err("management_tunnel_would_be_lost",
            "لا يمكن تفعيل نفق ترافيك دون نفق إدارة فعّال.")

    # SSTP management must never own the default route.
    if mgmt == "sstp_mgmt" and sstp_sets_default_route:
        err("sstp_must_not_own_default_route",
            "نفق SSTP للإدارة فقط — لا يجوز ضبط Default Route عليه.")

    # Only one tunnel may own the default route.
    if sstp_sets_default_route and traffic_owns_default_route:
        err("default_route_conflict",
            "لا يمكن أن يملك SSTP و L2TP/IPsec الـ Default Route في آن واحد.")

    # Traffic-mode specific gates.
    if traffic_mode is not None and traffic_mode not in TRAFFIC_MODES:
        err("invalid_traffic_mode", f"وضع ترافيك غير معروف: {traffic_mode}")
    if traffic_mode == "full_tunnel" and not full_tunnel_confirmed:
        err("full_tunnel_requires_confirmation",
            "وضع تمرير كل الترافيك يتطلب تأكيدًا صريحًا من المشغّل.")
    if traffic_mode == "selected_pool" and not selected_pool:
        err("missing_selected_pool",
            "وضع التجمّع المحدد يتطلب اختيار IP Pool.")

    # SSTP on v7 works but WireGuard is the better management choice.
    if mgmt == "sstp_mgmt" and wg:
        warn("sstp_on_v7_not_recommended",
             "RouterOS 7 يُفضّل WireGuard للإدارة بدل SSTP.")

    # PPTP is allowed only as an explicit Legacy choice — never silently. It
    # is valid (not blocking) but always carries the insecurity warning.
    if traffic == "pptp_traffic":
        warn("pptp_insecure_legacy",
             "PPTP غير آمن (تشفير MS-CHAPv2 مخترَق) — للاستخدام الاضطراري فقط. "
             "يُفضّل L2TP/IPsec.")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def tunnel_capabilities(version: object) -> dict:
    """Full per-version capability matrix (for UI + planners)."""
    return {
        "major": parse_major(version),
        "supports_wireguard": supports_wireguard(version),
        "supports_sstp_mgmt": supports_sstp_mgmt(version),
        "supports_l2tp_ipsec_traffic": supports_l2tp_ipsec_traffic(version),
        "supports_pptp_traffic": supports_pptp_traffic(version),
        "recommended_management_tunnel": recommended_management_tunnel(version),
        "recommended_traffic_tunnel": recommended_traffic_tunnel(version),
        "connection_modes": connection_modes_for_version(version),
    }


__all__ = [
    "MIN_SUPPORTED_VERSION",
    "WIREGUARD_MIN_MAJOR",
    "SSTP_MIN_MAJOR",
    "L2TP_IPSEC_MIN_MAJOR",
    "PPTP_MIN_MAJOR",
    "MANAGEMENT_TUNNEL_TYPES",
    "TRAFFIC_TUNNEL_TYPES",
    "TRAFFIC_PROTOCOLS",
    "TRAFFIC_MODES",
    "parse_major",
    "parse_routeros_major",
    "supports_wireguard",
    "supports_sstp_mgmt",
    "supports_l2tp_ipsec_traffic",
    "supports_pptp_traffic",
    "requires_direct_address",
    "connection_modes",
    "connection_modes_for_version",
    "recommended_management_tunnel",
    "recommended_traffic_tunnel",
    "validate_connection_plan",
    "tunnel_capabilities",
    "detect_major_from_resource",
    "summary",
]
