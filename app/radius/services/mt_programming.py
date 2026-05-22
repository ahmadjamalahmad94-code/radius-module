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
class PppoeProgrammingSpec:
    """PPPoE-server programming spec.

    PPPoE auth flows through RADIUS (the wizard already configures
    /radius), so this spec only owns the layer-3 side: pool, profile,
    and the PPPoE-server listener. Secrets are NOT touched here —
    those come from radcheck via FreeRADIUS.
    """
    interface: str
    cidr: str            # network the pool draws from
    profile_name: str    # name of the /ppp/profile we create
    service_name: str    # PPPoE service-name advertised on the wire
    pool_start: str = ""
    pool_end: str = ""
    local_address: str = ""
    dns_servers: str = "8.8.8.8,1.1.1.1"

    def validate(self) -> "ValidatedPppoe":
        return _validate_pppoe(self)


@dataclass
class ValidatedPppoe:
    interface: str
    network: ipaddress.IPv4Network
    local_address: ipaddress.IPv4Address
    pool_start: ipaddress.IPv4Address
    pool_end: ipaddress.IPv4Address
    profile_name: str
    service_name: str
    dns_servers: list[str]


@dataclass
class Command:
    """One structured RouterOS API call.

    `path` is the full command (e.g. "/ip/pool/add"); `attrs` are
    the key=value pairs RouterOS expects. The renderer turns
    `(path, attrs)` into a /import-style line for the script view,
    and Q2 hands the same `(path, attrs)` directly to the API
    client via `client.run(path, attrs=attrs)`.

    Keeping the data structured means the apply path never has to
    parse the rendered script — Q1 and Q2 share the same source
    of truth.
    """
    path: str
    attrs: dict[str, str]


@dataclass
class Plan:
    """What the route hands back to the template."""
    kind: str
    script: str
    summary: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)


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


_PPP_PROFILE_RE = re.compile(r"^[A-Za-z0-9\-_]{1,24}$")
_PPP_SERVICE_RE = re.compile(r"^[A-Za-z0-9\-_]{1,24}$")


def _validate_pppoe(spec: PppoeProgrammingSpec) -> ValidatedPppoe:
    iface = (spec.interface or "").strip()
    if not _INTERFACE_NAME_RE.match(iface):
        raise ValueError("اسم الواجهة غير صالح.")

    pname = (spec.profile_name or "").strip()
    if not _PPP_PROFILE_RE.match(pname):
        raise ValueError("اسم الـ profile غير صالح.")
    sname = (spec.service_name or "").strip()
    if not _PPP_SERVICE_RE.match(sname):
        raise ValueError("اسم الخدمة (service-name) غير صالح.")

    try:
        net = ipaddress.IPv4Network((spec.cidr or "").strip(), strict=False)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError,
            ValueError):
        raise ValueError("CIDR غير صالح.")
    if net.prefixlen >= 31 or net.is_loopback or net.is_link_local:
        raise ValueError("نطاق العنوان لا يصلح لـ PPPoE.")

    hosts = list(net.hosts())
    local_raw = (spec.local_address or "").strip()
    if local_raw:
        try:
            la = ipaddress.IPv4Address(local_raw)
        except (ipaddress.AddressValueError, ValueError):
            raise ValueError("local-address غير صالح.")
        if la not in net:
            raise ValueError("local-address خارج CIDR.")
    else:
        la = hosts[0]

    pstart_default = hosts[min(9, len(hosts) - 1)]
    pend_default   = hosts[-1]
    try:
        ps = ipaddress.IPv4Address(spec.pool_start) if spec.pool_start else pstart_default
        pe = ipaddress.IPv4Address(spec.pool_end)   if spec.pool_end   else pend_default
    except (ipaddress.AddressValueError, ValueError):
        raise ValueError("بداية/نهاية الـ pool غير صالحة.")
    if ps not in net or pe not in net:
        raise ValueError("نطاق الـ pool خارج CIDR.")
    if int(ps) > int(pe):
        raise ValueError("بداية الـ pool بعد نهايتها.")
    if int(ps) <= int(la) <= int(pe):
        raise ValueError("local-address داخل الـ pool.")

    dns_list = [s.strip() for s in (spec.dns_servers or "").split(",")
                if s.strip()]
    for s in dns_list:
        try:
            ipaddress.IPv4Address(s)
        except (ipaddress.AddressValueError, ValueError):
            raise ValueError(f"DNS غير صالح: {s}")
    if not dns_list:
        dns_list = ["8.8.8.8", "1.1.1.1"]

    return ValidatedPppoe(
        interface=iface, network=net, local_address=la,
        pool_start=ps, pool_end=pe,
        profile_name=pname, service_name=sname,
        dns_servers=dns_list,
    )


def build_pppoe_commands(v: ValidatedPppoe) -> list[Command]:
    """Structured Command list for PPPoE-server setup. Carries
    `PPPOE_COMMENT` on every row so Q4 unprogram can find them
    without colliding with hotspot objects."""
    comment = PPPOE_COMMENT
    pool_name = f"{v.profile_name}-pool"
    return [
        Command("/ip/pool/add", {
            "name": pool_name,
            "ranges": f"{v.pool_start}-{v.pool_end}",
            "comment": comment,
        }),
        Command("/ppp/profile/add", {
            "name": v.profile_name,
            "local-address": str(v.local_address),
            "remote-address": pool_name,
            "dns-server": ",".join(v.dns_servers),
            "use-encryption": "default",
            "comment": comment,
        }),
        Command("/interface/pppoe-server/server/add", {
            "service-name": v.service_name,
            "interface": v.interface,
            "default-profile": v.profile_name,
            "authentication": "pap,chap,mschap1,mschap2",
            "disabled": "no",
            "comment": comment,
        }),
    ]


def render_pppoe_script(v: ValidatedPppoe) -> str:
    lines = [
        "# === Hoberadius PPPoE-server programming script ===",
        f"# Interface {v.interface}, profile {v.profile_name}, service {v.service_name}.",
        f"# Every object carries comment={PPPOE_COMMENT}.",
        "",
    ]
    for cmd in build_pppoe_commands(v):
        lines.append(_command_to_script_line(cmd))
    lines.append("")
    return "\n".join(lines)


def _pppoe_summary(v: ValidatedPppoe) -> list[str]:
    return [
        f"إعداد PPPoE-server باسم profile «{v.profile_name}» على "
        f"الواجهة {v.interface}.",
        f"الـ service-name: {v.service_name}.",
        f"الـ pool: {v.pool_start} → {v.pool_end} داخل {v.network}.",
        f"local-address: {v.local_address}.",
        f"خوادم DNS: {', '.join(v.dns_servers)}.",
        f"كل أمر يحمل comment={PPPOE_COMMENT}.",
    ]


def _pppoe_conflicts(
    v: ValidatedPppoe,
    addresses: list[dict],
    interfaces: list[dict],
    routes: list[dict] | None = None,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    risks: list[str] = []
    iface_row = next(
        (r for r in interfaces if (r.get("name") or "") == v.interface),
        None,
    )
    if iface_row is None:
        risks.append(
            f"لم نجد الواجهة «{v.interface}» — البرمجة ستفشل.")
        return warnings, risks

    # S4.2 — same safety check as hotspot.
    from . import mt_interface_safety as safety
    verdict = safety.classify_interface(
        iface_row,
        routes=routes or (),
        addresses=addresses or (),
    )
    if verdict.risk == safety.RISK_BLOCKED:
        risks.append(
            f"الواجهة «{v.interface}» محظورة للبرمجة: "
            + " · ".join(verdict.reasons))
    elif verdict.risk == safety.RISK_HIGH:
        risks.append(
            f"الواجهة «{v.interface}» مصنّفة عالية الخطورة: "
            + " · ".join(verdict.reasons))
    elif verdict.risk == safety.RISK_MEDIUM:
        warnings.extend(verdict.reasons)
    return warnings, risks


def plan_pppoe(
    nas: Mapping[str, Any],
    spec: PppoeProgrammingSpec,
    *,
    existing_addresses: list[dict] | None = None,
    existing_interfaces: list[dict] | None = None,
    existing_routes: list[dict] | None = None,
) -> Plan:
    v = spec.validate()
    cmds = build_pppoe_commands(v)
    script = render_pppoe_script(v)
    summary = _pppoe_summary(v)
    warnings, risks = _pppoe_conflicts(
        v,
        existing_addresses or [],
        existing_interfaces or [],
        existing_routes or [],
    )
    return Plan(
        kind="pppoe",
        script=script,
        summary=summary,
        warnings=warnings,
        risks=risks,
        commands=cmds,
    )


def _q(s: str) -> str:
    """Quote a value for RouterOS script. Single-quote anything
    that needs it; otherwise leave bare so the script reads
    cleanly. RouterOS doesn't allow `"` inside identifiers, so
    we keep the whitelist tight in the validators above."""
    # Identifiers we generate are already whitelisted, so the
    # quote is for readability with hyphens/dots in names.
    return s


def build_hotspot_commands(v: ValidatedHotspot) -> list[Command]:
    """Structured Command list — the canonical source of truth.

    Both the readable script (operator preview) and the Q2 apply
    path are derived from this list, so the two views can never
    drift.
    """
    comment = HOTSPOT_COMMENT
    hs = v.hotspot_name
    pool_name = f"{hs}-pool"
    dhcp_name = f"{hs}-dhcp"
    prof_name = f"{hs}-prof"
    user_prof = f"{hs}-uprof"
    dns_csv = ",".join(v.dns_servers)

    cmds: list[Command] = [
        Command("/ip/pool/add", {
            "name": pool_name,
            "ranges": f"{v.pool_start}-{v.pool_end}",
            "comment": comment,
        }),
        Command("/ip/address/add", {
            "address": f"{v.gateway}/{v.network.prefixlen}",
            "interface": v.interface,
            "comment": comment,
        }),
        Command("/ip/dhcp-server/network/add", {
            "address": str(v.network),
            "gateway": str(v.gateway),
            "dns-server": dns_csv,
            "comment": comment,
        }),
        Command("/ip/dhcp-server/add", {
            "name": dhcp_name,
            "interface": v.interface,
            "address-pool": pool_name,
            "lease-time": v.lease_time,
            "disabled": "no",
            "comment": comment,
        }),
        Command("/ip/hotspot/profile/add", {
            "name": prof_name,
            "hotspot-address": str(v.gateway),
            "dns-name": f"{hs}.local",
            "html-directory": "hotspot",
            "use-radius": "yes",
            "comment": comment,
        }),
        Command("/ip/hotspot/add", {
            "name": hs,
            "interface": v.interface,
            "address-pool": pool_name,
            "profile": prof_name,
            "disabled": "no",
            "comment": comment,
        }),
    ]
    if v.rate_limit:
        cmds.append(Command("/ip/hotspot/user/profile/add", {
            "name": user_prof,
            "rate-limit": v.rate_limit,
            "comment": comment,
        }))
    for dns in v.dns_servers:
        cmds.append(Command("/ip/hotspot/walled-garden/ip/add", {
            "dst-host": dns,
            "action": "accept",
            "comment": comment,
        }))
    return cmds


def _command_to_script_line(cmd: Command) -> str:
    """Render one Command as a /import-style line. Used only for
    the human-readable preview — apply uses the Command directly."""
    # `/ip/pool/add` → `/ip pool add`
    parts = cmd.path.strip("/").split("/")
    head = "/" + " ".join(parts)
    attrs_str = " ".join(f"{k}={v}" for k, v in cmd.attrs.items())
    return f"{head} {attrs_str}"


def render_hotspot_script(v: ValidatedHotspot) -> str:
    """Build the /import-ready RouterOS script for hotspot."""
    comment = HOTSPOT_COMMENT
    lines = [
        "# === Hoberadius hotspot programming script ===",
        f"# Generated for interface {v.interface}, network {v.network}.",
        f"# Every object carries comment={comment} — unprogram looks",
        "# for that exact string.",
        "",
    ]
    for cmd in build_hotspot_commands(v):
        lines.append(_command_to_script_line(cmd))
    lines.append("")
    return "\n".join(lines)


# ─── Public API ────────────────────────────────────────────────


def plan_hotspot(
    nas: Mapping[str, Any],
    spec: HotspotProgrammingSpec,
    *,
    existing_addresses: list[dict] | None = None,
    existing_interfaces: list[dict] | None = None,
    existing_routes: list[dict] | None = None,
) -> Plan:
    """Build the full plan for a hotspot setup.

    `existing_addresses`, `existing_interfaces`, and
    `existing_routes` are passed in by the caller (the route
    fetches them via the K4 readers); leaving them out makes the
    planner stateless + testable without a router. The plan
    still works, it just doesn't surface the S4.1 safety
    classifier signals when routes are missing.
    """
    v = spec.validate()
    cmds   = build_hotspot_commands(v)
    script = render_hotspot_script(v)
    summary = _hotspot_summary(v)
    warnings, risks = _hotspot_conflicts(
        v,
        existing_addresses or [],
        existing_interfaces or [],
        existing_routes or [],
    )
    return Plan(
        kind="hotspot",
        script=script,
        summary=summary,
        warnings=warnings,
        risks=risks,
        commands=cmds,
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
    routes: list[dict] | None = None,
) -> tuple[list[str], list[str]]:
    """Surface any obvious clashes between the plan and the
    router's current state. `routes` is optional but if the
    caller has them, the S4.1 safety classifier uses them to
    catch WAN / WG mistakes before they reach the wire."""
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

        # S4.2 — feed S4.1's classifier with whatever we have.
        # Missing routes is OK; the classifier just won't fire
        # the WAN signal.
        from . import mt_interface_safety as safety  # avoid cycle
        verdict = safety.classify_interface(
            iface_row,
            routes=routes or (),
            addresses=addresses or (),
        )
        if verdict.risk == safety.RISK_BLOCKED:
            risks.append(
                f"الواجهة «{v.interface}» محظورة للبرمجة: "
                + " · ".join(verdict.reasons))
        elif verdict.risk == safety.RISK_HIGH:
            risks.append(
                f"الواجهة «{v.interface}» مصنّفة عالية الخطورة: "
                + " · ".join(verdict.reasons))
        elif verdict.risk == safety.RISK_MEDIUM:
            warnings.extend(verdict.reasons)
        # LOW + UNKNOWN: no extra reasons — operator already sees
        # the rest of the plan.

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


# ─── Q2 — Apply executor ───────────────────────────────────────


@dataclass
class StepResult:
    """One row in the apply report. `ok=True` means RouterOS
    accepted the command (or rejected it with a benign reason
    like "already exists"); `ok=False` means real failure."""
    path: str
    attrs: dict[str, str]
    ok: bool
    error: str = ""
    skipped: str = ""  # non-empty when the step was idempotent-skipped


@dataclass
class ApplyResult:
    ok: bool
    steps: list[StepResult]
    error: str = ""

    def summary(self) -> dict:
        return {
            "applied": sum(1 for s in self.steps
                            if s.ok and not s.skipped),
            "skipped": sum(1 for s in self.steps if s.skipped),
            "failed":  sum(1 for s in self.steps if not s.ok),
        }


# RouterOS error texts that mean "object already exists with these
# attrs" — treating them as idempotent successes lets the operator
# re-apply a plan without first cleaning up the half-applied state.
_BENIGN_ALREADY_EXISTS = (
    "already have",          # /ip/address — "already have such address"
    "already exists",        # /ip/pool, /ip/hotspot — generic
    "already have such",     # variants on the above
    "duplicate",             # walled-garden duplicate
)


def _is_benign_already_exists(err: str) -> bool:
    low = (err or "").lower()
    return any(s in low for s in _BENIGN_ALREADY_EXISTS)


def apply_commands(
    client: Any,
    commands: list[Command],
) -> ApplyResult:
    """Run each command via the wire client. Stop on first hard
    failure (returns ok=False); idempotent "already exists" rejects
    are recorded as skipped and the loop continues.

    `client` is anything with `.run(path, attrs=...)` — the test
    suite passes a fake client that just records the calls; prod
    code passes an open MikrotikClient.
    """
    steps: list[StepResult] = []
    for cmd in commands:
        try:
            client.run(cmd.path, attrs=dict(cmd.attrs))
            steps.append(StepResult(
                path=cmd.path, attrs=cmd.attrs, ok=True,
            ))
        except Exception as e:  # noqa: BLE001
            err = str(e)
            if _is_benign_already_exists(err):
                steps.append(StepResult(
                    path=cmd.path, attrs=cmd.attrs, ok=True,
                    skipped="already_exists",
                ))
                continue
            steps.append(StepResult(
                path=cmd.path, attrs=cmd.attrs, ok=False,
                error=err,
            ))
            return ApplyResult(ok=False, steps=steps, error=err)
    return ApplyResult(ok=True, steps=steps)


# ─── Q4 — Unprogram / rollback ─────────────────────────────────


# Resource paths to scan + remove for each kind.
#
# Order matters: RouterOS refuses to delete a /ip/address while a
# /ip/dhcp-server still references the interface, refuses to delete
# a /ip/pool while a /ip/dhcp-server still names it, etc. So we
# walk from leaf (walled-garden entries) to root (pool / address)
# — dependencies first.
_HOTSPOT_RESOURCE_ORDER = [
    "/ip/hotspot/walled-garden/ip",
    "/ip/hotspot",
    "/ip/hotspot/user/profile",
    "/ip/hotspot/profile",
    "/ip/dhcp-server",
    "/ip/dhcp-server/network",
    "/ip/address",
    "/ip/pool",
]
_PPPOE_RESOURCE_ORDER = [
    "/interface/pppoe-server/server",
    "/ppp/profile",
    "/ip/pool",
]


@dataclass
class UnprogramStep:
    path: str
    id: str
    ok: bool
    error: str = ""


@dataclass
class UnprogramResult:
    ok: bool
    steps: list[UnprogramStep]
    skipped_paths: list[str]   # paths that returned zero matching rows
    error: str = ""

    def summary(self) -> dict:
        return {
            "removed": sum(1 for s in self.steps if s.ok),
            "failed":  sum(1 for s in self.steps if not s.ok),
            "scanned": len(self.steps),
        }


def _print_path(path: str) -> str:
    """Translate `/ip/pool` into `/ip/pool/print`. We pass `path`
    as the canonical resource (matching what the apply path uses)
    so the two are always consistent."""
    return path + "/print"


def _remove_path(path: str) -> str:
    return path + "/remove"


def unprogram(
    client: Any, kind: str, *,
    only_comment: str | None = None,
) -> UnprogramResult:
    """Remove every object that carries the hoberadius:<kind>
    comment from the router behind `client`. Pure side-effect; no
    DB writes here — the route owns the audit-log entry.

    `only_comment` lets a future caller force a specific marker
    (e.g. for an aborted half-applied plan with a one-off
    comment). Default is the kind's canonical comment.
    """
    kind = (kind or "").lower()
    if kind == "hotspot":
        order   = _HOTSPOT_RESOURCE_ORDER
        comment = only_comment or HOTSPOT_COMMENT
    elif kind == "pppoe":
        order   = _PPPOE_RESOURCE_ORDER
        comment = only_comment or PPPOE_COMMENT
    else:
        return UnprogramResult(
            ok=False, steps=[], skipped_paths=[],
            error=f"unknown kind: {kind!r}",
        )

    steps: list[UnprogramStep] = []
    skipped: list[str] = []
    for path in order:
        # Plain /print, no server-side filter — comment matching
        # happens client-side. That keeps the wire interaction
        # uniform across RouterOS versions (some builds reject
        # `?comment=` query syntax on certain resources).
        try:
            rows = client.run(_print_path(path), attrs=None)
        except Exception:  # noqa: BLE001
            # The resource might not exist on this RouterOS build
            # (e.g. /ip/hotspot/user/profile on a CHR without
            # hotspot package). Skip and move on.
            skipped.append(path)
            continue
        ids = [r.get(".id") or r.get("id") for r in rows
               if (r.get("comment") or "") == comment]
        ids = [i for i in ids if i]
        if not ids:
            skipped.append(path)
            continue
        for rid in ids:
            try:
                client.run(_remove_path(path), attrs={".id": rid})
                steps.append(UnprogramStep(path=path, id=rid, ok=True))
            except Exception as e:  # noqa: BLE001
                steps.append(UnprogramStep(
                    path=path, id=rid, ok=False, error=str(e)))
    failed = sum(1 for s in steps if not s.ok)
    return UnprogramResult(
        ok=(failed == 0),
        steps=steps,
        skipped_paths=skipped,
    )


__all__ = [
    "HOBERADIUS_COMMENT_PREFIX",
    "HOTSPOT_COMMENT",
    "PPPOE_COMMENT",
    "HotspotProgrammingSpec",
    "ValidatedHotspot",
    "PppoeProgrammingSpec",
    "ValidatedPppoe",
    "Command",
    "Plan",
    "StepResult",
    "ApplyResult",
    "UnprogramStep",
    "UnprogramResult",
    "plan_hotspot",
    "plan_pppoe",
    "render_hotspot_script",
    "render_pppoe_script",
    "build_hotspot_commands",
    "build_pppoe_commands",
    "apply_commands",
    "unprogram",
]
