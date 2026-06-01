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


__all__ = [
    "MIN_SUPPORTED_VERSION",
    "WIREGUARD_MIN_MAJOR",
    "parse_major",
    "supports_wireguard",
    "requires_direct_address",
    "connection_modes",
    "detect_major_from_resource",
    "summary",
]
