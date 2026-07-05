"""SSRF guard — reject outbound URLs that point at private / internal hosts.

Tenant/customer-supplied outbound URLs (SMS ``send_url_template``, webhook
``target_url``, HTTP channel URLs) are attacker-controllable. Without a guard
they enable SSRF: fetch ``http://169.254.169.254/…`` (cloud metadata /
credentials), ``http://127.0.0.1:<port>`` / internal ``10.x`` hosts (internal
service access + port scanning), with the response body leaked back to the
tenant.

``assert_public_url`` resolves the host and rejects any address that is
loopback / link-local (covers 169.254.169.254 + fe80::) / private / reserved /
multicast / unspecified. Use it for endpoints that MUST be public (SMS
gateways, webhooks). Do NOT use it for legitimately-private targets like a
customer's MikroTik router on a LAN/VPN IP.

Pure stdlib (socket + ipaddress). Fails CLOSED — any resolution error blocks.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse

_ALLOWED_SCHEMES = ("http", "https")


class SSRFBlocked(Exception):
    """Raised when a URL resolves to a non-public / disallowed address."""


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # is_global is the cleanest single check, but be explicit + defensive:
    if (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified):
        return False
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) — unwrap and re-check.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return _ip_is_public(str(addr.ipv4_mapped))
    return True


def assert_public_url(url: str, *, schemes: tuple[str, ...] = _ALLOWED_SCHEMES) -> None:
    """Raise :class:`SSRFBlocked` unless ``url`` is a well-formed http(s) URL
    whose host resolves ONLY to public IP addresses. Fails closed."""
    if not url or not isinstance(url, str):
        raise SSRFBlocked("empty url")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.lower() not in schemes:
        raise SSRFBlocked(f"scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise SSRFBlocked("missing host")
    # A bare IP literal in the URL is checked directly (no DNS).
    try:
        ipaddress.ip_address(host)
        candidates = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or None,
                                       proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SSRFBlocked(f"cannot resolve host: {host}") from exc
        candidates = [str(info[4][0]) for info in infos]
        if not candidates:
            raise SSRFBlocked(f"host resolved to nothing: {host}")
    for ip in candidates:
        if not _ip_is_public(ip):
            raise SSRFBlocked(f"host {host} resolves to non-public address {ip}")


def is_public_url(url: str, *, schemes: tuple[str, ...] = _ALLOWED_SCHEMES) -> bool:
    """Boolean convenience wrapper around :func:`assert_public_url`."""
    try:
        assert_public_url(url, schemes=schemes)
        return True
    except SSRFBlocked:
        return False


__all__ = ["SSRFBlocked", "assert_public_url", "is_public_url"]
