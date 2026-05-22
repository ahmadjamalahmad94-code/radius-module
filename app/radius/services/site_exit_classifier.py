"""site_exit_classifier — VX2 group classification (pure).

Maps a normalized target to exactly one of the 7 VX2 groups:

    speedtest_measurement   speed/bandwidth probes (default ENABLED)
    public_ip_checkers      "what's my IP" services (default ENABLED)
    raw_ip_targets          bare IPs and CIDRs (default ENABLED)
    vpn_provider_pages      VPN vendor sites (default DISABLED)
    network_diagnostics     mxtoolbox/ultratools/etc (default DISABLED)
    general_probe_sites     google/wolframalpha/etc (default DISABLED)
    manual_review           anything we couldn't confidently place

Deterministic and pure: same input → same output, no I/O.

Precedence (first match wins):
    1. raw IP/CIDR → raw_ip_targets
    2. speedtest_measurement (exact set + substring rules)
    3. public_ip_checkers   (exact set + substring rules)
    4. vpn_provider_pages   (exact set only — narrow vendor list)
    5. network_diagnostics  (exact set only)
    6. general_probe_sites  (exact set only)
    7. fallback             → manual_review

The substring rules for groups 2-3 follow the VX2 spec verbatim:
they widen the net for variants like `c.speedtest.net` or
`whatsmyip.org`. They intentionally do NOT include the shorter
brand "fast" — `fast.com` and `api.fast.com` are caught by the
exact set instead, which avoids `fastcompany.com` over-matching.
"""
from __future__ import annotations


# ─── Group constants — match site_exit_targets_repo ──────────


GROUP_SPEEDTEST_MEASUREMENT = "speedtest_measurement"
GROUP_PUBLIC_IP_CHECKERS    = "public_ip_checkers"
GROUP_VPN_PROVIDER_PAGES    = "vpn_provider_pages"
GROUP_NETWORK_DIAGNOSTICS   = "network_diagnostics"
GROUP_GENERAL_PROBE_SITES   = "general_probe_sites"
GROUP_RAW_IP_TARGETS        = "raw_ip_targets"
GROUP_MANUAL_REVIEW         = "manual_review"


# ─── Tables ──────────────────────────────────────────────────


_SPEEDTEST_EXACT = frozenset({
    "speedtest.net", "www.speedtest.net", "c.speedtest.net",
    "ooklaserver.net", "speedtestcustom.com",
    "fast.com", "api.fast.com",
    "test-ipv6.com", "pingtest.net", "webtest.net",
})
# Substring rules per the VX2 spec — case-insensitive.
# "fast" deliberately excluded (handled by exact set above) to
# avoid matching innocent hosts like `fastcompany.com`.
_SPEEDTEST_SUBSTRINGS = (
    "speedtest", "ookla", "iperf",
    "bwtest", "stest", "testspeed",
)


_PUBLIC_IP_CHECKERS_EXACT = frozenset({
    "ifconfig.co", "iplocation.net", "ipinfo.info",
    "whoer.net", "ipchicken.com", "ipcow.com",
    "xmyip.com", "wtfismyip.com", "bearsmyip.com",
    "check-host.net", "whatismybrowser.com",
    "whatismycountry.com",
})
# Substrings: cover whatismyip*, whatsmyip*, myip* variants.
_PUBLIC_IP_CHECKERS_SUBSTRINGS = (
    "whatismyip", "whatsmyip", "myip",
)


_VPN_PROVIDER_PAGES_EXACT = frozenset({
    "expressvpn.com", "www.expressvpn.com",
    "nordvpn.com", "www.nordvpn.com",
    "purevpn.com", "www.purevpn.com",
    "privateinternetaccess.com",
    "www.privateinternetaccess.com",
    "astrill.com", "www.astrill.com",
    "hide.me", "www.hide.me",
    "perfect-privacy.com", "www.perfect-privacy.com",
    "zenmate.com", "www.zenmate.com",
    "cactusvpn.com", "www.cactusvpn.com",
    "overplay.net", "www.overplay.net",
    "goldenfrog.com", "www.goldenfrog.com",
    "ipburger.com", "www.ipburger.com",
})


_NETWORK_DIAGNOSTICS_EXACT = frozenset({
    "mxtoolbox.com", "www.mxtoolbox.com",
    "ultratools.com", "www.ultratools.com",
    "yougetsignal.com", "www.yougetsignal.com",
})


_GENERAL_PROBE_SITES_EXACT = frozenset({
    "google.com", "www.google.com",
    "google.ps", "www.google.ps",
    "wolframalpha.com", "www.wolframalpha.com",
    "m.wolframalpha.com",
})


# ─── Public API ──────────────────────────────────────────────


def classify(normalized_value: str, target_type: str) -> str:
    """Return one of the GROUP_* constants. Always returns a
    valid group — never raises and never returns an empty
    string. Unknown / ambiguous inputs land in manual_review."""
    if target_type in {"ip", "cidr"}:
        return GROUP_RAW_IP_TARGETS

    if not normalized_value:
        return GROUP_MANUAL_REVIEW

    host = normalized_value.lower().strip(".")
    if not host:
        return GROUP_MANUAL_REVIEW

    # 2. Speedtest — exact then substring.
    if host in _SPEEDTEST_EXACT:
        return GROUP_SPEEDTEST_MEASUREMENT
    for needle in _SPEEDTEST_SUBSTRINGS:
        if needle in host:
            return GROUP_SPEEDTEST_MEASUREMENT

    # 3. Public IP checkers — exact then substring.
    if host in _PUBLIC_IP_CHECKERS_EXACT:
        return GROUP_PUBLIC_IP_CHECKERS
    for needle in _PUBLIC_IP_CHECKERS_SUBSTRINGS:
        if needle in host:
            return GROUP_PUBLIC_IP_CHECKERS

    # 4. VPN provider pages — exact list only (narrow brand).
    if host in _VPN_PROVIDER_PAGES_EXACT:
        return GROUP_VPN_PROVIDER_PAGES

    # 5. Network diagnostics — exact list.
    if host in _NETWORK_DIAGNOSTICS_EXACT:
        return GROUP_NETWORK_DIAGNOSTICS

    # 6. General probe sites — exact list.
    if host in _GENERAL_PROBE_SITES_EXACT:
        return GROUP_GENERAL_PROBE_SITES

    # 7. Fallback.
    return GROUP_MANUAL_REVIEW


# Default-enabled groups (matches the VX2 UI spec). Exported so
# the importer and UI agree on what's "safe to enable by
# default" vs "requires explicit confirmation".
DEFAULT_ENABLED_GROUPS = frozenset({
    GROUP_SPEEDTEST_MEASUREMENT,
    GROUP_PUBLIC_IP_CHECKERS,
    GROUP_RAW_IP_TARGETS,
})


DEFAULT_DISABLED_GROUPS = frozenset({
    GROUP_VPN_PROVIDER_PAGES,
    GROUP_NETWORK_DIAGNOSTICS,
    GROUP_GENERAL_PROBE_SITES,
    GROUP_MANUAL_REVIEW,
})


__all__ = [
    "GROUP_SPEEDTEST_MEASUREMENT", "GROUP_PUBLIC_IP_CHECKERS",
    "GROUP_VPN_PROVIDER_PAGES", "GROUP_NETWORK_DIAGNOSTICS",
    "GROUP_GENERAL_PROBE_SITES", "GROUP_RAW_IP_TARGETS",
    "GROUP_MANUAL_REVIEW",
    "DEFAULT_ENABLED_GROUPS", "DEFAULT_DISABLED_GROUPS",
    "classify",
]
