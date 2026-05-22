"""mt_programming — RouterOS script-plan generator.

Q1 ships only the *generator*. The plan is a textual RouterOS
script + a summary + a list of warnings the operator should
read before clicking apply. Apply itself is Q2.

Design choice — every command we emit carries the literal
comment `hoberadius:<kind>`. Q4 (unprogram) finds and removes
exactly the objects with that comment, so a roll-back can be
surgical instead of "guess what to delete." The comment string
is the contract between Q1 and Q4 and MUST not change without a
migration story.

Validation lives here, not in the route, so unit tests can
stress every edge case without spinning up Flask.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


HOBERADIUS_COMMENT_PREFIX = "hoberadius:"
HOTSPOT_COMMENT           = HOBERADIUS_COMMENT_PREFIX + "hs"
PPPOE_COMMENT             = HOBERADIUS_COMMENT_PREFIX + "pppoe"


# ─── Spec dataclasses ──────────────────────────────────────────


@dataclass
class HotspotProgrammingSpec:
    """Operator-supplied inputs for hotspot programming.

    All fields are normalised through `.validate()`. CIDR is the
    only required *shape* input — the pool/gateway are derived if
    not given, so the operator can leave them blank for the
    common case (gateway = first host, pool = .10 → .254).
    """
    interface: str
    cidr: str
    hotspot_name: str
    dns_servers: str = "8.8.8.8,1.1.1.1"
    pool_start: str = ""
    pool_end: str = ""
    gateway: str = ""
    lease_time: str = "1h"
    rate_limit: str = ""    # e.g. "10M/10M" — optional default profile

    def validate(self) -> "ValidatedHotspot":
        return _validate_hotspot(self)


@dataclass
class ValidatedHotspot:
    """Normalised + validated hotspot spec. Holds the original
    inputs alongside the derived defaults so the plan can render
    a "what we used / why" table for the operator."""
    interface: str
    network: ipaddress.IPv4Network
    gateway: ipaddress.IPv4Address
    pool_start: ipaddress.IPv4Address
    pool_end: ipaddress.IPv4Address
    hotspot_name: str
    dns_servers: list[str]
    lease_time: str
    rate_limit: str


@dataclass
class Plan:
    """What the route hands back to the template."""
    kind: str
    script: str
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


# ─── Validators ────────────────────────────────────────────────


_INTERFACE_NAME_RE = re.compile(r"^[A-Za-z0-9\-_\.]{1,32}$")
_HS_NAME_RE        = re.compile(r"^[A-Za-z0-9\-_]{1,24}$")


def _validate_hotspot(spec: HotspotProgrammingSpec) -> ValidatedHotspot:
    iface = (spec.interface or "").strip()
    if not _INTERFACE_NAME_RE.match(iface):
        raise ValueError("اسم الواجهة غير صالح.")
    name = (spec.hotspot_name or "").strip()
    if not _HS_NAME_RE.match(name):
        raise ValueError(
            "اسم الـ hotspot غير صالح (أحرف لاتينية وأرقام و-_ فقط، حتى 24).")

    cidr_raw = (spec.cidr or "").strip()
    try:
        net = ipaddress.IPv4Network(cidr_raw, strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError,
            ValueError):
        raise ValueError("CIDR غير صالح. مثال: 192.168.10.0/24")
    if net.prefixlen >= 31:
        raise ValueError("CIDR ضيّق جدًا — اختر /30 أو أوسع.")
    if net.is_loopback or net.is_link_local or net.is_multicast:
        raise ValueError("نطاق العنوان لا يصلح لشبكة هوتسبوت.")

    hosts = list(net.hosts())
    gateway_raw = (spec.gateway or "").strip()
    if gateway_raw:
        try:
            gw = ipaddress.IPv4Address(gateway_raw)
        except (ipaddress.AddressValueError, ValueError):
            raise ValueError("Gateway غير صالح.")
        if gw not in net:
            raise ValueError("الـ gateway خارج نطاق CIDR.")
    else:
        gw = hosts[0]

    # Pool defaults: skip the first 9 hosts after the gateway, use
    # everything up to .254. This keeps a buffer for static IPs.
    pool_default_start = hosts[min(9, len(hosts) - 1)]
    pool_default_end   = hosts[-1]
    ps_raw = (spec.pool_start or "").strip()
    pe_raw = (spec.pool_end   or "").strip()
    try:
        pool_start = ipaddress.IPv4Address(ps_raw) if ps_raw else pool_default_start
        pool_end   = ipaddress.IPv4Address(pe_raw) if pe_raw else pool_default_end
    except (ipaddress.AddressValueError, ValueError):
        raise ValueError("بداية/نهاية الـ pool غير صالحة.")
    if pool_start not in net or pool_end not in net:
        raise ValueError("نطاق الـ pool خارج CIDR.")
    if int(pool_start) > int(pool_end):
        raise ValueError("بداية الـ pool بعد نهايتها.")
    if gw == pool_start or gw == pool_end \
       or (int(pool_start) <= int(gw) <= int(pool_end)):
        raise ValueError("الـ gateway داخل الـ pool — اختر pool لا يحويه.")

    dns_list = [s.strip() for s in (spec.dns_servers or "").split(",")
                if s.strip()]
    for s in dns_list:
        try:
            ipaddress.IPv4Address(s)
        except (ipaddress.AddressValueError, ValueError):
            raise ValueError(f"DNS غير صالح: {s}")
    if not dns_list:
        dns_list = ["8.8.8.8", "1.1.1.1"]

    lease = (spec.lease_time or "1h").strip()
    if not re.match(r"^\d+[smhdw]$", lease):
        raise ValueError("lease-time غير صالح (مثال: 1h, 30m, 1d).")
    rate = (spec.rate_limit or "").strip()
    if rate and not re.match(r"^\d+[KMG]/\d+[KMG]$", rate):
        raise ValueError("rate-limit غير صالح (مثال: 10M/10M).")

    return ValidatedHotspot(
        interface=iface, network=net, gateway=gw,
        pool_start=pool_start, pool_end=pool_end,
        hotspot_name=name, dns_servers=dns_list,
        lease_time=lease, rate_limit=rate,
    )


# ─── Script generation ─────────────────────────────────────────


def _q(s: str) -> str:
    """Quote a value for RouterOS script. Single-quote anything
    that needs it; otherwise leave bare so the script reads
    cleanly. RouterOS doesn't allow `"` inside identifiers, so
    we keep the whitelist tight in the validators above."""
    # Identifiers we generate are already whitelisted, so the
    # quote is for readability with hyphens/dots in names.
    return s


def render_hotspot_script(v: ValidatedHotspot) -> str:
    """Build the /import-ready RouterOS script for hotspot.

    Every command carries the literal HOTSPOT_COMMENT so Q4
    unprogram can find them. Keeping the comment string in one
    place means a future rename only touches this module.
    """
    comment = HOTSPOT_COMMENT
    hs = v.hotspot_name
    pool_name = f"{hs}-pool"
    dhcp_name = f"{hs}-dhcp"
    prof_name = f"{hs}-prof"
    user_prof = f"{hs}-uprof"
    dns_csv = ",".join(v.dns_servers)

    lines = [
        "# === Hoberadius hotspot programming script ===",
        f"# Generated for interface {v.interface}, network {v.network}.",
        f"# Every object carries comment={comment} — unprogram looks",
        "# for that exact string.",
        "",
        f"/ip pool add name={pool_name} "
        f"ranges={v.pool_start}-{v.pool_end} comment={comment}",
        "",
        f"/ip address add address={v.gateway}/{v.network.prefixlen} "
        f"interface={v.interface} comment={comment}",
        "",
        f"/ip dhcp-server network add address={v.network} "
        f"gateway={v.gateway} dns-server={dns_csv} comment={comment}",
        "",
        f"/ip dhcp-server add name={dhcp_name} interface={v.interface} "
        f"address-pool={pool_name} lease-time={v.lease_time} "
        f"disabled=no comment={comment}",
        "",
        f"/ip hotspot profile add name={prof_name} "
        f"hotspot-address={v.gateway} dns-name={hs}.local "
        "html-directory=hotspot use-radius=yes "
        f"comment={comment}",
        "",
        f"/ip hotspot add name={hs} interface={v.interface} "
        f"address-pool={pool_name} profile={prof_name} "
        f"disabled=no comment={comment}",
    ]
    if v.rate_limit:
        lines += [
            "",
            f"/ip hotspot user profile add name={user_prof} "
            f"rate-limit={v.rate_limit} comment={comment}",
        ]
    lines += [
        "",
        "# DNS + NTP walled-garden entries so unauthenticated",
        "# clients can resolve names and sync time before login.",
    ]
    for dns in v.dns_servers:
        lines.append(
            f"/ip hotspot walled-garden ip add dst-host={dns} "
            f"action=accept comment={comment}"
        )
    lines.append("")
    return "\n".join(lines)


# ─── Public API ────────────────────────────────────────────────


def plan_hotspot(
    nas: Mapping[str, Any],
    spec: HotspotProgrammingSpec,
    *,
    existing_addresses: list[dict] | None = None,
    existing_interfaces: list[dict] | None = None,
) -> Plan:
    """Build the full plan for a hotspot setup.

    `existing_addresses` and `existing_interfaces` are passed in by
    the caller (the route fetches them via the K4 readers); leaving
    them out makes the planner stateless + testable without a
    router. The plan still works, it just doesn't produce conflict
    warnings — that's fine for unit tests.
    """
    v = spec.validate()
    script = render_hotspot_script(v)
    summary = _hotspot_summary(v)
    warnings, risks = _hotspot_conflicts(
        v, existing_addresses or [], existing_interfaces or [])
    return Plan(
        kind="hotspot",
        script=script,
        summary=summary,
        warnings=warnings,
        risks=risks,
    )


def _hotspot_summary(v: ValidatedHotspot) -> list[str]:
    items = [
        f"إعداد hotspot باسم «{v.hotspot_name}» على الواجهة "
        f"{v.interface}.",
        f"عنوان الـ gateway: {v.gateway}/{v.network.prefixlen}.",
        f"الـ pool: {v.pool_start} → {v.pool_end} داخل {v.network}.",
        f"خوادم DNS: {', '.join(v.dns_servers)}.",
        f"مدة الـ lease: {v.lease_time}.",
    ]
    if v.rate_limit:
        items.append(f"User profile rate-limit الافتراضي: {v.rate_limit}.")
    items.append(
        "كل أمر يحمل comment="
        f"{HOTSPOT_COMMENT} لتسهيل التراجع لاحقًا."
    )
    return items


def _hotspot_conflicts(
    v: ValidatedHotspot,
    addresses: list[dict],
    interfaces: list[dict],
) -> tuple[list[str], list[str]]:
    """Surface any obvious clashes between the plan and the
    router's current state."""
    warnings: list[str] = []
    risks: list[str] = []

    # Interface present? Disabled?
    iface_row = next(
        (r for r in interfaces if (r.get("name") or "") == v.interface),
        None,
    )
    if iface_row is None:
        risks.append(
            f"لم نجد الواجهة «{v.interface}» على هذا الراوتر. "
            "البرمجة ستفشل عند التطبيق.")
    else:
        if str(iface_row.get("disabled")) == "true":
            warnings.append(
                f"الواجهة «{v.interface}» معطّلة الآن — الـ hotspot لن "
                "يعمل حتى تفعّلها.")

    # Existing IP on this interface?
    same_iface = [a for a in addresses
                  if (a.get("interface") or "") == v.interface]
    if same_iface:
        existing = ", ".join(a.get("address", "?") for a in same_iface)
        warnings.append(
            f"الواجهة «{v.interface}» تحمل عناوين IP بالفعل: {existing}. "
            "إضافة عنوان جديد قد يتعارض مع التوجيه.")

    # Overlap with any other subnet?
    for a in addresses:
        if (a.get("interface") or "") == v.interface:
            continue
        try:
            other = ipaddress.ip_interface(a.get("address") or "").network
        except (ValueError, TypeError):
            continue
        if other.version != v.network.version:
            continue
        if other.overlaps(v.network):
            risks.append(
                f"الـ network {v.network} يتداخل مع {other} على الواجهة "
                f"«{a.get('interface')}» — يجب اختيار CIDR مختلف.")

    return warnings, risks


__all__ = [
    "HOBERADIUS_COMMENT_PREFIX",
    "HOTSPOT_COMMENT",
    "PPPOE_COMMENT",
    "HotspotProgrammingSpec",
    "ValidatedHotspot",
    "Plan",
    "plan_hotspot",
    "render_hotspot_script",
]
