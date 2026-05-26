"""Setup Wizard — unified diagnostic codes catalogue.

Single source of truth for every failure code the wizard can
return. Every code carries:

  * `code`            — stable machine identifier (snake_case)
  * `phase`           — which wizard phase it can fire from
  * `ar_explanation`  — one-sentence Arabic for the operator
  * `cause`           — likely root cause, English (logs)
  * `fix`             — what the operator should try, English
  * `inspect_command` — optional RouterOS command for more info
  * `severity`        — info | warning | error | critical

The catalogue is consulted at three places:

  1. Planner code raises `WizardDiagnostic(code=...)` instead
     of a bare string; the catalogue resolves it.
  2. Verification services return `code` and the UI maps it
     to `ar_explanation` for display.
  3. Tests assert that every code emitted by a planner is
     registered in the catalogue (no orphan codes).

This module is intentionally pure data + a few lookup
helpers. No DB, no Flask, no network. The 18 safety rules in
docs/setup_wizard/MIKROTIK_SCRIPT_SAFETY_RULES.md require
that diagnostics are structured — this module enforces that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ─── Phase identifiers ──────────────────────────────────────


PHASE_INTERNET           = "internet"
PHASE_VPN_RADIUS         = "vpn_radius"
PHASE_HOTSPOT            = "hotspot"
PHASE_BROADBAND          = "broadband"
PHASE_ADDED_SERVICES     = "added_services"
PHASE_VERIFICATION       = "verification"
PHASE_PROVISIONING       = "provisioning"
PHASE_REGISTRATION       = "registration"


ALL_PHASES = (
    PHASE_INTERNET, PHASE_VPN_RADIUS, PHASE_HOTSPOT,
    PHASE_BROADBAND, PHASE_ADDED_SERVICES,
    PHASE_VERIFICATION, PHASE_PROVISIONING,
    PHASE_REGISTRATION,
)


SEVERITY_INFO     = "info"
SEVERITY_WARNING  = "warning"
SEVERITY_ERROR    = "error"
SEVERITY_CRITICAL = "critical"


# ─── Diagnostic dataclass ───────────────────────────────────


@dataclass(frozen=True)
class Diagnostic:
    code:            str
    phase:           str
    ar_explanation:  str
    cause:           str
    fix:             str
    inspect_command: str = ""
    severity:        str = SEVERITY_ERROR

    def as_dict(self) -> dict:
        return {
            "code":            self.code,
            "phase":           self.phase,
            "ar_explanation":  self.ar_explanation,
            "cause":           self.cause,
            "fix":             self.fix,
            "inspect_command": self.inspect_command,
            "severity":        self.severity,
        }


class WizardDiagnosticError(Exception):
    """Raised when planner / verification code wants to abort
    with a catalogued diagnostic. The route layer catches this
    and returns a structured JSON response."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(
            f"{code}: {detail}" if detail else code
        )

    def diagnostic(self) -> Diagnostic:
        return get(self.code)

    def as_dict(self) -> dict:
        d = get(self.code).as_dict()
        if self.detail:
            d["detail"] = self.detail
        return d


# ─── Catalogue ──────────────────────────────────────────────


_CATALOGUE: dict[str, Diagnostic] = {}


def _add(d: Diagnostic) -> Diagnostic:
    if d.code in _CATALOGUE:
        raise RuntimeError(
            f"duplicate diagnostic code: {d.code}"
        )
    if d.phase not in ALL_PHASES:
        raise RuntimeError(
            f"unknown phase {d.phase!r} on code {d.code}"
        )
    _CATALOGUE[d.code] = d
    return d


# ─── Internet uplink phase ──────────────────────────────────


_add(Diagnostic(
    code="internet_source_missing",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "نوع مصدر الإنترنت غير محدَّد. اختر VLAN أو Static أو "
        "DHCP أو PPPoE قبل المتابعة."
    ),
    cause="operator submitted phase without choosing a source",
    fix="select an internet source type and retry",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="internet_interface_missing",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "اسم الواجهة (مثل ether1) مطلوب لإكمال السكربت."
    ),
    cause="operator left selected_wan_interface empty",
    fix="enter the WAN interface name from the router",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="internet_static_ip_invalid",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "عنوان IP الثابت غير صحيح. الصيغة المطلوبة: "
        "x.x.x.x/y (مثل 192.168.1.10/24)."
    ),
    cause="static IP failed CIDR validation",
    fix="enter address in CIDR notation",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="internet_pppoe_credentials_missing",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "اسم مستخدم PPPoE وكلمة المرور مطلوبان من مزوّد "
        "الإنترنت."
    ),
    cause="PPPoE username or password not provided",
    fix="enter both credentials from the ISP",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="internet_default_route_conflict",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "سيغيّر هذا السكربت المسار الافتراضي الحالي. تأكّد "
        "أنّك لست متّصلاً بالراوتر عبر هذا المسار قبل "
        "المتابعة."
    ),
    cause="proposed default route conflicts with the active management route",
    fix="connect via console / Winbox over LAN before applying",
    inspect_command='/ip/route/print where dst-address="0.0.0.0/0" active=yes',
    severity=SEVERITY_CRITICAL,
))


_add(Diagnostic(
    code="internet_subnet_overlap",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "الشبكة المختارة تتداخل مع شبكة موجودة على الراوتر."
    ),
    cause="proposed CIDR overlaps an existing /ip/address entry",
    fix="pick a non-overlapping subnet",
    inspect_command="/ip/address/print",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="internet_ping_failed",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "تعذّر اختبار ping إلى 8.8.8.8. تحقّق من السكربت "
        "وأعد تشغيله، أو تواصل مع مزوّد الإنترنت."
    ),
    cause="paste-back shows ping 8.8.8.8 timed out",
    fix="verify the script ran cleanly; check uplink cable",
    inspect_command="/tool/ping 8.8.8.8 count=5",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="internet_dns_unresolved",
    phase=PHASE_INTERNET,
    ar_explanation=(
        "الـ IP يصل لكن DNS لا يحلّ الأسماء. تحقّق من إعدادات "
        "DNS."
    ),
    cause="ping by hostname fails while ping by IP succeeds",
    fix="set use-peer-dns or configure /ip/dns servers",
    inspect_command="/ip/dns/print",
    severity=SEVERITY_WARNING,
))


# ─── VPN + RADIUS phase ─────────────────────────────────────


_add(Diagnostic(
    code="vpn_not_handshaking",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "WireGuard لم يكمل أوّل handshake بعد. انتظر بضع ثوانٍ "
        "ثمّ أعد الفحص."
    ),
    cause="router peer has rx=0 / never-handshake",
    fix="wait 10-30s after the script runs; check VPS firewall on UDP 51820",
    inspect_command="/interface/wireguard/peers/print detail",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="wrong_public_endpoint",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "عنوان VPS العامّ في السكربت لا يطابق ما يحاول "
        "الراوتر الاتصال به."
    ),
    cause="endpoint-address in /interface/wireguard/peers doesn't match VPS public IP",
    fix="regenerate the script with HOBERADIUS_PUBLIC_HOST set correctly",
    inspect_command="/interface/wireguard/peers/print where comment~\"HOBERADIUS\"",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="firewall_blocking_udp",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "جدار ناري بين الراوتر و VPS يحجب UDP/51820."
    ),
    cause="UDP 51820 from router's WAN to VPS public IP is blocked",
    fix="ask ISP / corporate firewall to allow outbound UDP 51820",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="wrong_allowed_address",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "إعداد allowed-address على peer غير صحيح، يجب أن يكون "
        "10.10.0.1/32 للوصول إلى VPS."
    ),
    cause="peer allowed-address misconfigured on the router",
    fix='set allowed-address="10.10.0.1/32" on the VPS peer',
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="route_missing",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "الراوتر يفتقد المسار إلى 10.10.0.1 عبر واجهة hr-wg."
    ),
    cause="/ip/route entry for 10.10.0.1/32 via hr-wg not present",
    fix="re-run the bootstrap script (it adds this route)",
    inspect_command='/ip/route/print where dst-address="10.10.0.1/32"',
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="radius_secret_mismatch",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "السرّ المشترك بين الراوتر والـ RADIUS لا يطابق ما هو "
        "مسجّل على الخادم."
    ),
    cause="/radius secret on router != freeradius shared secret",
    fix="regenerate the script and re-apply with the same secret",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="radius_server_unreachable",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "RADIUS على VPS لا يستجيب لمنفذ UDP/1812."
    ),
    cause="UDP 1812 from router VPN IP to VPS VPN IP unreachable",
    fix="check freeradius is running on VPS; verify wg-reload picked up the peer file",
    inspect_command='/tool/ping 10.10.0.1',
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="api_login_failed",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "تعذّر تسجيل الدخول عبر API على الراوتر. تحقّق من "
        "اسم المستخدم وكلمة المرور."
    ),
    cause="MikroTik API login returned auth failure",
    fix="verify the api_user + password from the bootstrap script",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="router_time_or_dns_issue",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "وقت الراوتر غير صحيح أو DNS لا يعمل. ضع NTP وتأكّد "
        "من DNS."
    ),
    cause="router clock skew or DNS misconfiguration breaks crypto/cert checks",
    fix="set /system/ntp/client and /ip/dns servers",
    inspect_command="/system/clock/print",
    severity=SEVERITY_WARNING,
))


_add(Diagnostic(
    code="duplicate_config_conflict",
    phase=PHASE_VPN_RADIUS,
    ar_explanation=(
        "إعدادات سابقة على الراوتر تتعارض مع ما يحاول المعالج "
        "إنشاءه. أعد تشغيل السكربت — يحوي كتلة تنظيف."
    ),
    cause="pre-existing hr-wg / radius / api-user found without HOBERADIUS_SETUP tag",
    fix="re-run the bootstrap script; cleanup block scoped to comments handles HobeRadius-owned rows",
    inspect_command='/interface/wireguard/print where name="hr-wg"',
    severity=SEVERITY_WARNING,
))


# ─── Provisioning / server-side ─────────────────────────────


_add(Diagnostic(
    code="peers_dir_unwritable",
    phase=PHASE_PROVISIONING,
    ar_explanation=(
        "مجلّد wg-peers.d على الخادم غير قابل للكتابة. راجع "
        "الصلاحيات على /etc/hoberadius/wg-peers.d."
    ),
    cause="hoberadius container can't write to the shared peers.d volume",
    fix="chmod 1777 /etc/hoberadius/wg-peers.d on the host",
    severity=SEVERITY_CRITICAL,
))


_add(Diagnostic(
    code="peer_file_write_failed",
    phase=PHASE_PROVISIONING,
    ar_explanation=(
        "تعذّرت كتابة ملف إعدادات peer على الخادم."
    ),
    cause="OSError raised during atomic write to peers.d/router-N.conf",
    fix="check disk space; verify the streams.d volume is mounted",
    severity=SEVERITY_CRITICAL,
))


_add(Diagnostic(
    code="public_key_not_found",
    phase=PHASE_PROVISIONING,
    ar_explanation=(
        "تعذّر العثور على WireGuard public-key في النص الملصق. "
        "ألصق مخرجات /interface wireguard print detail كاملةً."
    ),
    cause="regex extractor couldn't find a 44-char base64 key in pasted text",
    fix="paste the whole output, including the public-key=... line",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="missing_peer_input",
    phase=PHASE_PROVISIONING,
    ar_explanation=(
        "بيانات peer غير مكتملة — أعد تشغيل السكربت على "
        "الراوتر ثمّ ألصق المخرجات."
    ),
    cause="prepared_peer row is missing public_key or vpn_ip",
    fix="re-run the bootstrap script and re-paste the output",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="wireguard_interface_not_found_on_router",
    phase=PHASE_PROVISIONING,
    ar_explanation=(
        "واجهة WireGuard `hr-wg` غير موجودة على الراوتر. "
        "شغّل سكربت المعالج عليه أوّلاً."
    ),
    cause="MikroTik API responded but no interface named hr-wg",
    fix="paste the bootstrap script in the router's Terminal first",
    severity=SEVERITY_ERROR,
))


# ─── Hotspot phase ──────────────────────────────────────────


_add(Diagnostic(
    code="hotspot_no_interface_selected",
    phase=PHASE_HOTSPOT,
    ar_explanation=(
        "لم يتمّ اختيار واجهة لـ Hotspot. اختر واحدة أو أكثر."
    ),
    cause="hotspot phase advanced without selected interfaces",
    fix="pick at least one interface that is NOT the WAN",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="hotspot_subnet_conflict",
    phase=PHASE_HOTSPOT,
    ar_explanation=(
        "نطاق Hotspot يتعارض مع WAN أو VPN أو شبكة موجودة."
    ),
    cause="hotspot CIDR overlaps WAN/VPN/existing pool",
    fix="pick a different CIDR; smart-mode auto-suggests safe ranges",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="hotspot_bridge_name_taken",
    phase=PHASE_HOTSPOT,
    ar_explanation=(
        "اسم الجسر مأخوذ — اختر اسماً آخر."
    ),
    cause="proposed bridge name collides with existing /interface/bridge",
    fix="rename the bridge",
    inspect_command="/interface/bridge/print",
    severity=SEVERITY_ERROR,
))


# ─── Broadband / PPPoE phase ────────────────────────────────


_add(Diagnostic(
    code="broadband_no_interface_selected",
    phase=PHASE_BROADBAND,
    ar_explanation=(
        "لم يتمّ اختيار واجهة لـ PPPoE."
    ),
    cause="broadband phase advanced without selected interfaces",
    fix="select at least one interface that is NOT the WAN or Hotspot bridge",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="broadband_pool_conflict",
    phase=PHASE_BROADBAND,
    ar_explanation=(
        "نطاق PPPoE يتعارض مع WAN أو VPN أو Hotspot."
    ),
    cause="broadband pool CIDR overlaps another configured pool",
    fix="pick a non-overlapping CIDR",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="broadband_profile_name_taken",
    phase=PHASE_BROADBAND,
    ar_explanation=(
        "اسم بروفايل PPPoE مأخوذ."
    ),
    cause="/ppp/profile name collision",
    fix="rename the profile",
    inspect_command="/ppp/profile/print",
    severity=SEVERITY_ERROR,
))


# ─── Verification umbrella ──────────────────────────────────


_add(Diagnostic(
    code="verification_paste_empty",
    phase=PHASE_VERIFICATION,
    ar_explanation=(
        "نتيجة الفحص فارغة. الصق مخرجات أوامر التحقّق من "
        "الراوتر."
    ),
    cause="paste-back input was empty or whitespace",
    fix="run the validation block on MikroTik and paste the output",
    severity=SEVERITY_ERROR,
))


_add(Diagnostic(
    code="verification_unrecognized_format",
    phase=PHASE_VERIFICATION,
    ar_explanation=(
        "صيغة المخرجات غير مفهومة. تأكّد أنّ النصّ من Terminal "
        "MikroTik وليس من برنامج آخر."
    ),
    cause="paste-back didn't match any expected RouterOS line pattern",
    fix="copy the output directly from MikroTik Terminal",
    severity=SEVERITY_WARNING,
))


# ─── Registration phase ─────────────────────────────────────


_add(Diagnostic(
    code="nas_insert_failed",
    phase=PHASE_REGISTRATION,
    ar_explanation=(
        "تعذّر تسجيل الراوتر في قاعدة بيانات NAS."
    ),
    cause="DB insert into nas_devices failed (constraint / disk)",
    fix="check tenant_id; verify unique constraints; retry",
    severity=SEVERITY_CRITICAL,
))


_add(Diagnostic(
    code="missing_registration_input",
    phase=PHASE_REGISTRATION,
    ar_explanation=(
        "بيانات تسجيل الراوتر غير مكتملة."
    ),
    cause="run is missing router_name or router_vpn_ip in state_json",
    fix="rewind to the COLLECTING phase and re-fill",
    severity=SEVERITY_ERROR,
))


# ─── Added services phase ───────────────────────────────────


_add(Diagnostic(
    code="added_services_module_not_available",
    phase=PHASE_ADDED_SERVICES,
    ar_explanation=(
        "الخدمة المختارة غير متوفّرة في هذا الإصدار."
    ),
    cause="selected added-service module isn't registered in the integrator",
    fix="upgrade HobeRadius or pick another service",
    severity=SEVERITY_WARNING,
))


# ─── Lookup API ─────────────────────────────────────────────


def get(code: str) -> Diagnostic:
    """Lookup a diagnostic by code. Raises KeyError if absent —
    that's intentional, so a planner that emits an unknown
    code fails loudly in tests."""
    d = _CATALOGUE.get(code)
    if d is None:
        raise KeyError(f"unknown diagnostic code: {code}")
    return d


def has(code: str) -> bool:
    return code in _CATALOGUE


def all_codes() -> list[str]:
    return sorted(_CATALOGUE.keys())


def by_phase(phase: str) -> list[Diagnostic]:
    return sorted(
        (d for d in _CATALOGUE.values() if d.phase == phase),
        key=lambda d: d.code,
    )


def render_for_ui(code: str, *, detail: str = "") -> dict:
    """Shape the diagnostic for the wizard's JSON responses.
    Adds an optional `detail` field for runtime context (e.g.
    the exact OS error string from a file write)."""
    d = get(code).as_dict()
    if detail:
        d["detail"] = detail
    return d


__all__ = [
    "PHASE_INTERNET", "PHASE_VPN_RADIUS", "PHASE_HOTSPOT",
    "PHASE_BROADBAND", "PHASE_ADDED_SERVICES",
    "PHASE_VERIFICATION", "PHASE_PROVISIONING",
    "PHASE_REGISTRATION", "ALL_PHASES",
    "SEVERITY_INFO", "SEVERITY_WARNING",
    "SEVERITY_ERROR", "SEVERITY_CRITICAL",
    "Diagnostic", "WizardDiagnosticError",
    "get", "has", "all_codes", "by_phase",
    "render_for_ui",
]
