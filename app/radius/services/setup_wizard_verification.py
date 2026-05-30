"""Wave B: setup wizard read-only verification engine."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Protocol

from .setup_wizard_server_wg_readiness import SafeCommandRunner, build_server_wg_command_runner


VERIFICATION_KEYS = (
    "vpn_tunnel",
    "vps_ping",
    "router_ping",
    "radius_reachable",
    "api_login",
    "hotspot_ready",
    "broadband_ready",
)

VERIFICATION_STATUSES = {"pending", "success", "failed", "blocked"}

VERIFY_STATUS_SUCCESS = "success"
VERIFY_STATUS_FAILED = "failed"
VERIFY_STATUS_BLOCKED = "blocked"
VERIFY_STATUS_PARTIAL = "partial"

CHECK_SUCCESS = "success"
CHECK_FAILED = "failed"
CHECK_BLOCKED = "blocked"
CHECK_SKIPPED = "skipped"

_READ_ONLY_BLOCK_TOKENS = (
    " add",
    "\nadd",
    " set",
    "\nset",
    " remove",
    "\nremove",
    " disable",
    "\ndisable",
    " enable",
    "\nenable",
    " reset",
    "\nreset",
    " export",
    "\nexport",
    " import",
    "\nimport",
    "system script",
    "tool fetch",
    "password",
    "user add",
    "radius add",
    "ip route add",
    "ip firewall add",
)


class ProbeUnavailableError(RuntimeError):
    """Raised when a read-only probe is not configured."""


class ReadOnlyCommandRejected(RuntimeError):
    """Raised when a command is not allowed for read-only probe."""


@dataclass(frozen=True)
class VerificationCard:
    key: str
    status: str
    title_ar: str
    details_ar: str

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "status": self.status,
            "title_ar": self.title_ar,
            "details_ar": self.details_ar,
        }


@dataclass
class VerificationResult:
    overall_status: str
    checks: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    raw_observations: dict[str, Any]
    duration_ms: int
    next_action_ar: str
    gate_unlocked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "checks": self.checks,
            "diagnostics": self.diagnostics,
            "raw_observations": self.raw_observations,
            "duration_ms": self.duration_ms,
            "next_action_ar": self.next_action_ar,
            "gate_unlocked": self.gate_unlocked,
        }


class RouterReadOnlyAdapter(Protocol):
    def get_identity(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...
    def get_interfaces(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]: ...
    def get_ip_addresses(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]: ...
    def get_routes(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]: ...
    def get_dns(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...
    def get_radius_servers(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]: ...
    def get_hotspot_servers(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]: ...
    def get_pppoe_servers(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]: ...
    def get_nat_rules(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]: ...
    def ping(self, target: str, *, count: int = 3, timeout_seconds: float = 2.0) -> dict[str, Any]: ...
    def run_read_only_command(self, command: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...


class VpsProbeAdapter(Protocol):
    def ping_router_vpn_ip(self, ip: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...
    def inspect_wireguard_peer(self, peer_identifier: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...
    def check_udp_port_hint(self, host: str, port: int, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...


class RadiusProbeAdapter(Protocol):
    def inspect_radius_config(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...
    def test_auth(self, username: str, password: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]: ...


class RouterReadOnlyProbe:
    def __init__(self, adapter: RouterReadOnlyAdapter | None = None) -> None:
        self._adapter = adapter

    def _adapter_or_raise(self) -> RouterReadOnlyAdapter:
        if self._adapter is None:
            raise ProbeUnavailableError("router probe unavailable")
        return self._adapter

    def get_identity(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().get_identity(timeout_seconds=timeout_seconds)

    def get_interfaces(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
        return self._adapter_or_raise().get_interfaces(timeout_seconds=timeout_seconds)

    def get_ip_addresses(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
        return self._adapter_or_raise().get_ip_addresses(timeout_seconds=timeout_seconds)

    def get_routes(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
        return self._adapter_or_raise().get_routes(timeout_seconds=timeout_seconds)

    def get_dns(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().get_dns(timeout_seconds=timeout_seconds)

    def get_radius_servers(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
        return self._adapter_or_raise().get_radius_servers(timeout_seconds=timeout_seconds)

    def get_hotspot_servers(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
        return self._adapter_or_raise().get_hotspot_servers(timeout_seconds=timeout_seconds)

    def get_pppoe_servers(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
        return self._adapter_or_raise().get_pppoe_servers(timeout_seconds=timeout_seconds)

    def get_nat_rules(self, *, timeout_seconds: float = 2.0) -> list[dict[str, Any]]:
        return self._adapter_or_raise().get_nat_rules(timeout_seconds=timeout_seconds)

    def ping(self, target: str, *, count: int = 3, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().ping(target, count=count, timeout_seconds=timeout_seconds)

    def run_read_only_command(self, command: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        normalized = f" {str(command or '').strip().lower()} "
        if any(token in normalized for token in _READ_ONLY_BLOCK_TOKENS):
            raise ReadOnlyCommandRejected("read-only command rejected")
        return self._adapter_or_raise().run_read_only_command(
            command,
            timeout_seconds=timeout_seconds,
        )


class VpsNetworkProbe:
    def __init__(self, adapter: VpsProbeAdapter | None = None) -> None:
        self._adapter = adapter

    def _adapter_or_raise(self) -> VpsProbeAdapter:
        if self._adapter is None:
            raise ProbeUnavailableError("vps probe unavailable")
        return self._adapter

    def ping_router_vpn_ip(self, ip: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().ping_router_vpn_ip(ip, timeout_seconds=timeout_seconds)

    def inspect_wireguard_peer(self, peer_identifier: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().inspect_wireguard_peer(
            peer_identifier,
            timeout_seconds=timeout_seconds,
        )

    def check_udp_port_hint(self, host: str, port: int, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().check_udp_port_hint(
            host,
            port,
            timeout_seconds=timeout_seconds,
        )


class ServerPingVpsProbeAdapter:
    """Read-only VPS-side probe used by the setup wizard lab flow."""

    def __init__(self, runner: SafeCommandRunner | None = None) -> None:
        self.runner = runner or build_server_wg_command_runner()

    def ping_router_vpn_ip(self, ip: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        target = str(ip_address(str(ip).strip()))
        result = self.runner.execute_read_only(f"ping -c 3 {target}", timeout_seconds=timeout_seconds)
        stdout = str(result.get("stdout") or "")
        ok = bool(result.get("ok")) or _has_ping_success(stdout, target)
        return {
            "ok": ok,
            "target": target,
            "stdout": stdout,
            "stderr": str(result.get("stderr") or ""),
            "blocked": bool(result.get("blocked")),
            "code": result.get("code") or "",
            "command_args": result.get("command_args") or [],
        }

    def inspect_wireguard_peer(self, peer_identifier: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        public_key = str(peer_identifier or "").strip()
        interface = os.environ.get("HOBERADIUS_WG_INTERFACE") or "wg0"
        result = self.runner.execute_read_only(f"wg show {interface}", timeout_seconds=timeout_seconds)
        stdout = str(result.get("stdout") or "")
        peers = _parse_wg_show_peers(stdout)
        matched = _find_peer_by_public_key(peers, public_key)
        if matched:
            return {
                "ok": True,
                "blocked": False,
                "interface": interface,
                "public_key": public_key,
                "allowed_ips": matched.get("allowed_ips") or "",
                "latest_handshake": matched.get("latest_handshake") or "",
                "peers_count": len(peers),
            }
        return {
            "ok": False,
            "blocked": bool(result.get("blocked")),
            "code": result.get("code") or "wg_peer_not_found",
            "interface": interface,
            "public_key": public_key,
            "peers_count": len(peers),
            "stdout": stdout if not result.get("blocked") else "",
        }

    def check_udp_port_hint(self, host: str, port: int, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        _ = host, port, timeout_seconds
        return {"ok": False, "blocked": True, "code": "udp_hint_not_configured"}


class RadiusReadOnlyProbe:
    def __init__(self, adapter: RadiusProbeAdapter | None = None) -> None:
        self._adapter = adapter

    def _adapter_or_raise(self) -> RadiusProbeAdapter:
        if self._adapter is None:
            raise ProbeUnavailableError("radius probe unavailable")
        return self._adapter

    def inspect_radius_config(self, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().inspect_radius_config(timeout_seconds=timeout_seconds)

    def test_auth(self, username: str, password: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        return self._adapter_or_raise().test_auth(
            username,
            password,
            timeout_seconds=timeout_seconds,
        )


class SetupDiagnosticsService:
    _MAP: dict[str, dict[str, Any]] = {
        "internet_ping_failed": {
            "arabic_title": "فشل اختبار الإنترنت",
            "explanation_ar": "الراوتر لم يتمكن من الوصول إلى 8.8.8.8.",
            "likely_causes": ["المسار الافتراضي غير صحيح", "واجهة WAN غير مفعلة", "مزود الخدمة لا يمرر الحركة"],
            "suggested_fixes": ["تحقق من default route", "تحقق من عنوان WAN والبوابة", "نفذ ping من الراوتر يدويًا"],
            "commands_to_inspect": ["/ip route print detail", "/tool ping 8.8.8.8 count=5"],
        },
        "dns_failed": {
            "arabic_title": "فشل DNS",
            "explanation_ar": "الراوتر لا يحل أسماء النطاقات كما هو متوقع.",
            "likely_causes": ["DNS غير مضبوط", "DNS مزود الخدمة لا يستجيب"],
            "suggested_fixes": ["حدد DNS موثوق مثل 1.1.1.1 و 8.8.8.8", "أعد الفحص بعد ضبط DNS"],
            "commands_to_inspect": ["/ip dns print detail", "/tool ping cloudflare.com count=5"],
        },
        "default_route_missing": {
            "arabic_title": "المسار الافتراضي غير موجود",
            "explanation_ar": "لم يتم العثور على default route مع البوابة المطلوبة.",
            "likely_causes": ["لم يُطبق جزء route من السكربت", "gateway غير صحيح"],
            "suggested_fixes": ["راجع سكربت الإنترنت", "تحقق من gateway وdistance"],
            "commands_to_inspect": ["/ip route print detail where dst-address=0.0.0.0/0"],
        },
        "nat_missing": {
            "arabic_title": "قاعدة NAT غير موجودة",
            "explanation_ar": "لم يتم العثور على قاعدة masquerade مخصصة لواجهة الرفع.",
            "likely_causes": ["تعطيل NAT في النموذج", "فشل تطبيق القاعدة"],
            "suggested_fixes": ["فعّل NAT في النموذج أو أعد تطبيق السكربت"],
            "commands_to_inspect": ["/ip firewall nat print detail"],
        },
        "uplink_interface_missing": {
            "arabic_title": "واجهة الرفع غير موجودة",
            "explanation_ar": "الواجهة المحددة في الإعدادات غير موجودة على الراوتر.",
            "likely_causes": ["اسم واجهة خاطئ", "تغير اسم الواجهة"],
            "suggested_fixes": ["استخدم نفس اسم الواجهة من /interface print"],
            "commands_to_inspect": ["/interface print detail"],
        },
        "probe_unavailable": {
            "arabic_title": "وضع الفحص المباشر غير متاح",
            "explanation_ar": "لا يوجد موصل قراءة مباشر للراوتر/الخادم في هذا البيئة.",
            "likely_causes": ["لم يتم تفعيل probe adapter", "بيئة الاختبار لا تملك اتصالاً حيًا"],
            "suggested_fixes": ["استخدم وضع تحليل المخرجات الملصقة", "هيئ probe adapter في بيئة التشغيل"],
            "commands_to_inspect": [],
        },
        "vpn_not_handshaking": {
            "arabic_title": "نفق VPN لا يصافح",
            "explanation_ar": "لا توجد مصافحة WireGuard حديثة بين الراوتر والخادم.",
            "likely_causes": ["Public key أو endpoint خاطئ", "UDP محجوب"],
            "suggested_fixes": ["راجع peer keys", "تحقق من منفذ UDP في الجدار الناري"],
            "commands_to_inspect": ["wg show", "/interface wireguard peers print detail"],
        },
        "wrong_public_endpoint": {
            "arabic_title": "عنوان endpoint غير صحيح",
            "explanation_ar": "عنوان الخادم العام أو المنفذ لا يطابق الخطة.",
            "likely_causes": ["IP VPS تغير", "المنفذ غير مطابق"],
            "suggested_fixes": ["حدث endpoint في الراوتر", "أكد المنفذ على الطرفين"],
            "commands_to_inspect": ["/interface wireguard peers print detail", "wg show"],
        },
        "firewall_blocking_udp": {
            "arabic_title": "الجدار الناري يحجب UDP",
            "explanation_ar": "حزم WireGuard لا تمر بسبب قواعد الحماية.",
            "likely_causes": ["قاعدة DROP قبل allow", "مزود الخدمة يحجب المنفذ"],
            "suggested_fixes": ["أضف allow للمنفذ", "اختبر منفذ UDP بديل"],
            "commands_to_inspect": ["/ip firewall filter print stats", "iptables -S"],
        },
        "wrong_allowed_address": {
            "arabic_title": "Allowed Address غير مطابق",
            "explanation_ar": "شبكات allowed-address لا تطابق شبكة النفق المقصودة.",
            "likely_causes": ["قيمة too narrow", "CIDR غير صحيح"],
            "suggested_fixes": ["طابق allowed-address مع خطة النفق"],
            "commands_to_inspect": ["/interface wireguard peers print detail"],
        },
        "server_allowed_ip_mismatch": {
            "arabic_title": "عنوان الراوتر على الخادم غير مطابق",
            "explanation_ar": "تم رصد WireGuard peer على الخادم، لكن allowed-ips لا يساوي عنوان الراوتر المحجوز في HobeRadius.",
            "likely_causes": ["إعادة محاولة قديمة استخدمت IP سابق", "Peer موجود على الخادم بقيمة allowed-ips قديمة"],
            "suggested_fixes": ["اضغط إصلاح الربط على الخادم", "تأكد أن allowed-ips يساوي عنوان الراوتر المحجوز /32"],
            "commands_to_inspect": ["wg show"],
        },
        "router_vpn_ip_mismatch": {
            "arabic_title": "عنوان الراوتر في MikroTik غير مطابق للحجز",
            "explanation_ar": "مخرجات MikroTik تعرض عنوان ربط خاص مختلفًا عن العنوان المحجوز لهذا الراوتر في HobeRadius.",
            "likely_causes": ["سكربت قديم تم نسخه", "إعداد WireGuard سابق لم يتم تحديثه"],
            "suggested_fixes": ["أعد توليد سكربت الربط من نفس الجولة", "راجع address على واجهة WireGuard"],
            "commands_to_inspect": ["/ip address print detail where interface=hr-wg"],
        },
        "route_missing": {
            "arabic_title": "مسار مطلوب غير موجود",
            "explanation_ar": "مسار الشبكات المطلوبة للنفق أو RADIUS غير موجود.",
            "likely_causes": ["route لم يُطبق", "gateway غير صحيح"],
            "suggested_fixes": ["أعد تطبيق جزء route", "تحقق من gateway on-link"],
            "commands_to_inspect": ["/ip route print detail"],
        },
        "radius_secret_mismatch": {
            "arabic_title": "سر RADIUS غير متطابق",
            "explanation_ar": "secret على الراوتر لا يطابق secret في الخادم.",
            "likely_causes": ["نسخ secret خاطئ", "وجود إعداد أقدم"],
            "suggested_fixes": ["طابق secret بدقة", "استخدم تعليق HOBERADIUS_SETUP لتحديد الإدخال الصحيح"],
            "commands_to_inspect": ["/radius print detail"],
        },
        "radius_server_unreachable": {
            "arabic_title": "خادم RADIUS غير قابل للوصول",
            "explanation_ar": "الراوتر لا يصل لخادم RADIUS عبر المسار الحالي.",
            "likely_causes": ["VPN غير فعال", "المنافذ 1812/1813 محجوبة"],
            "suggested_fixes": ["تحقق من ping بين النفق", "افحص الجدار الناري على VPS"],
            "commands_to_inspect": ["/tool ping 10.10.0.1 count=5", "ss -ulpn | grep 1812"],
        },
        "api_login_failed": {
            "arabic_title": "فشل تسجيل دخول API",
            "explanation_ar": "تعذر الدخول إلى MikroTik API بالمستخدم المخطط.",
            "likely_causes": ["بيانات دخول خاطئة", "الخدمة مغلقة", "صلاحيات غير كافية"],
            "suggested_fixes": ["تحقق من user/service api", "استخدم مستخدم API مخصص للمعالج"],
            "commands_to_inspect": ["/ip service print", "/user print detail"],
        },
        "api_user_missing": {
            "arabic_title": "مستخدم API غير موجود",
            "explanation_ar": "لم يظهر المستخدم المخطط ضمن قائمة المستخدمين.",
            "likely_causes": ["لم يُنشأ المستخدم", "اسم مختلف عن المخطط"],
            "suggested_fixes": ["راجع مخرجات /user print", "أعد تنفيذ جزء API من السكربت"],
            "commands_to_inspect": ["/user print detail"],
        },
        "router_dns_issue": {
            "arabic_title": "مشكلة DNS على الراوتر",
            "explanation_ar": "الراوتر لا يحل النطاقات بالشكل الصحيح.",
            "likely_causes": ["إعداد DNS ناقص", "خوادم DNS غير متاحة"],
            "suggested_fixes": ["ضبط DNS موثوق", "تحقق من allow-remote-requests عند الحاجة"],
            "commands_to_inspect": ["/ip dns print", "/tool ping cloudflare.com count=3"],
        },
        "router_time_issue": {
            "arabic_title": "وقت الراوتر غير صحيح",
            "explanation_ar": "انحراف وقت النظام قد يسبب أخطاء اتصال/مصادقة.",
            "likely_causes": ["NTP غير مفعّل", "timezone خاطئة"],
            "suggested_fixes": ["فعّل NTP", "تأكد من timezone الصحيحة"],
            "commands_to_inspect": ["/system clock print", "/system ntp client print"],
        },
        "duplicate_config_conflict": {
            "arabic_title": "تعارض إعدادات مكررة",
            "explanation_ar": "تم العثور على إعدادات أقدم قد تتعارض مع خطة المعالج.",
            "likely_causes": ["بقايا إعدادات قديمة", "تعليقات غير موحدة"],
            "suggested_fixes": ["راجع العناصر المتكررة قبل المتابعة"],
            "commands_to_inspect": ["/interface wireguard print detail", "/radius print detail"],
        },
        "management_interface_conflict": {
            "arabic_title": "تعارض مع واجهة الإدارة",
            "explanation_ar": "الواجهة المحددة قد تكون واجهة دخول الإدارة الحالية.",
            "likely_causes": ["اختيار interface إدارة كـ uplink"],
            "suggested_fixes": ["استخدم منفذ WAN الصحيح", "نفذ التعديلات من جلسة محلية آمنة"],
            "commands_to_inspect": ["/ip address print", "/interface print detail"],
        },
        "hotspot_server_missing": {
            "arabic_title": "خادم Hotspot غير موجود",
            "explanation_ar": "لم يتم العثور على hotspot server متوقع في الراوتر.",
            "likely_causes": ["فشل تطبيق سكربت Hotspot", "اسم الخادم مختلف"],
            "suggested_fixes": ["تحقق من أسماء الخوادم", "أعد تطبيق سكربت Hotspot"],
            "commands_to_inspect": ["/ip hotspot print detail"],
        },
        "hotspot_radius_disabled": {
            "arabic_title": "RADIUS غير مفعّل على Hotspot",
            "explanation_ar": "خادم Hotspot موجود لكن use-radius غير مفعّل.",
            "likely_causes": ["تم تعطيل use-radius", "تطبيق ناقص للسكربت"],
            "suggested_fixes": ["فعّل use-radius على السيرفر"],
            "commands_to_inspect": ["/ip hotspot profile print detail"],
        },
        "hotspot_pool_missing": {
            "arabic_title": "Pool الـ Hotspot غير موجود",
            "explanation_ar": "لم يتم العثور على pool مستخدم من إعدادات Hotspot.",
            "likely_causes": ["اسم pool خاطئ", "pool غير مُنشأ"],
            "suggested_fixes": ["راجع pool name في السكربت"],
            "commands_to_inspect": ["/ip pool print detail"],
        },
        "hotspot_nat_missing": {
            "arabic_title": "NAT Hotspot غير موجود",
            "explanation_ar": "لا توجد قاعدة NAT متوقعة لشبكة Hotspot.",
            "likely_causes": ["nat_enabled=false", "القاعدة لم تُطبق"],
            "suggested_fixes": ["أضف/طبّق قاعدة NAT المخصصة للشبكة"],
            "commands_to_inspect": ["/ip firewall nat print detail"],
        },
        "hotspot_interface_missing": {
            "arabic_title": "واجهة Hotspot غير متوفرة",
            "explanation_ar": "الواجهة المختارة لـ Hotspot غير موجودة أو غير فعالة.",
            "likely_causes": ["واجهة خاطئة", "لم يتم إضافتها للـ bridge"],
            "suggested_fixes": ["تحقق من bridge ports والواجهة"],
            "commands_to_inspect": ["/interface print detail", "/interface bridge port print detail"],
        },
        "pppoe_service_missing": {
            "arabic_title": "خدمة PPPoE غير موجودة",
            "explanation_ar": "لم يتم العثور على pppoe-server service متوقع.",
            "likely_causes": ["لم يُطبق سكربت Broadband", "اسم الخدمة مختلف"],
            "suggested_fixes": ["راجع service name", "أعد تطبيق السكربت"],
            "commands_to_inspect": ["/interface pppoe-server server print detail"],
        },
        "ppp_profile_missing": {
            "arabic_title": "PPP Profile غير موجود",
            "explanation_ar": "لم يتم العثور على profile المطلوب لخدمة PPPoE.",
            "likely_causes": ["profile name خاطئ", "فشل إنشاء profile"],
            "suggested_fixes": ["تحقق من profile name في السكربت"],
            "commands_to_inspect": ["/ppp profile print detail"],
        },
        "broadband_pool_missing": {
            "arabic_title": "Pool الـ Broadband غير موجود",
            "explanation_ar": "لم يتم العثور على remote pool لخدمة PPPoE.",
            "likely_causes": ["pool غير موجود", "اسم pool مختلف"],
            "suggested_fixes": ["راجع pool settings وأعد الفحص"],
            "commands_to_inspect": ["/ip pool print detail"],
        },
        "broadband_nat_missing": {
            "arabic_title": "NAT Broadband غير موجود",
            "explanation_ar": "لا توجد قاعدة NAT مخصصة لحركة شبكة Broadband.",
            "likely_causes": ["nat_enabled=false", "فشل إضافة القاعدة"],
            "suggested_fixes": ["أعد تطبيق قاعدة NAT المقيدة بالشبكة"],
            "commands_to_inspect": ["/ip firewall nat print detail"],
        },
        "ppp_radius_disabled": {
            "arabic_title": "RADIUS غير مفعّل لخدمة PPP",
            "explanation_ar": "خدمات PPP موجودة لكن خيار use-radius غير مفعّل.",
            "likely_causes": ["إعداد PPP AAA غير مكتمل"],
            "suggested_fixes": ["فعّل use-radius في PPP AAA"],
            "commands_to_inspect": ["/ppp aaa print detail"],
        },
    }

    def get_diagnostic(self, code: str) -> dict[str, Any]:
        payload = self._MAP.get(code)
        if not payload:
            return {
                "code": code,
                "arabic_title": "تشخيص غير معروف",
                "explanation_ar": "لم يتم العثور على وصف لهذا الخطأ.",
                "likely_causes": ["بيانات غير كافية"],
                "suggested_fixes": ["أعد الفحص مع مخرجات أوضح"],
                "commands_to_inspect": [],
            }
        return {"code": code, **payload}

    def list_all(self) -> dict[str, dict[str, Any]]:
        return {key: self.get_diagnostic(key) for key in self._MAP}


def _details_for_status(status: str) -> str:
    if status == "success":
        return "تم التحقق بنجاح."
    if status == "failed":
        return "فشل الفحص، راجع التشخيص."
    if status == "blocked":
        return "هذا الفحص محجوب حتى استكمال الخطوات السابقة."
    return "بانتظار التنفيذ والفحص."


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "ok", "success"}


def _has_ping_success(output: str, target_hint: str | None = None) -> bool:
    text = str(output or "").lower()
    if target_hint and target_hint.lower() not in text:
        # Don't hard-fail by target hint only; continue heuristics.
        pass
    if "packet-loss=0" in text:
        return True
    if re.search(r"received=\d+", text) and "packet-loss=100" not in text:
        return True
    if "0% packet loss" in text:
        return True
    if re.search(r"\b\d+\s+packets transmitted,\s*\d+\s+(packets\s+)?received\b", text):
        return "0 received" not in text
    if "timeout" in text or "no route to host" in text:
        return False
    return False


def _parse_wg_show_peers(output: str) -> list[dict[str, Any]]:
    text = str(output or "")
    peers: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("peer:"):
            if current:
                peers.append(current)
            current = {"public_key": line.split(":", 1)[1].strip()}
            continue
        if current is None and "\t" in line:
            cols = line.split("\t")
            if len(cols) >= 5 and re.fullmatch(r"[A-Za-z0-9+/]{43}=", cols[0].strip()):
                peers.append(
                    {
                        "public_key": cols[0].strip(),
                        "allowed_ips": cols[3].strip(),
                        "latest_handshake": cols[4].strip(),
                    }
                )
            continue
        if current is None:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        normalized = key.strip().lower().replace(" ", "_")
        current[normalized] = value.strip()
    if current:
        peers.append(current)
    return peers


def _find_peer_by_public_key(peers: list[dict[str, Any]], public_key: str) -> dict[str, Any] | None:
    key = str(public_key or "").strip()
    if not key:
        return None
    for peer in peers:
        if str(peer.get("public_key") or "").strip() == key:
            return peer
    return None


def _split_allowed_ips(value: Any) -> set[str]:
    raw = str(value or "").replace(",", " ")
    return {part.strip() for part in raw.split() if part.strip()}


def _allowed_ips_match(observed: Any, expected: str) -> bool:
    return str(expected or "").strip() in _split_allowed_ips(observed)


def _extract_allowed_ips(peer: dict[str, Any] | None) -> str:
    if not peer:
        return ""
    for key in ("allowed_ips", "allowed ips", "allowed-ips", "allowed_address"):
        value = peer.get(key)
        if value:
            return str(value)
    peers = peer.get("peers")
    if isinstance(peers, list):
        for item in peers:
            value = _extract_allowed_ips(item)
            if value:
                return value
    return ""


def _extract_router_interface_ips(output: str, interface: str) -> list[str]:
    iface = str(interface or "").strip()
    found: list[str] = []
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if iface and f"interface={iface}" not in line and f'interface="{iface}"' not in line:
            continue
        for match in re.finditer(r'address="?((?:\d{1,3}\.){3}\d{1,3})/\d+"?', line):
            found.append(match.group(1))
    return list(dict.fromkeys(found))


def _mask_public_value(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) < 12:
        return "***"
    return f"{text[:6]}...{text[-6:]}"


def _mask_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(token in key_l for token in ("password", "secret", "token")):
                masked[key] = "***"
            elif "public_key" in key_l:
                masked[key] = _mask_public_value(item)
            else:
                masked[key] = _mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [_mask_secrets(v) for v in value]
    return value


def _check(key: str, label_ar: str, status: str, details_ar: str = "") -> dict[str, Any]:
    return {"key": key, "label_ar": label_ar, "status": status, "details_ar": details_ar}


def _all_success(checks: list[dict[str, Any]], required: set[str]) -> bool:
    by_key = {item["key"]: item["status"] for item in checks}
    return all(by_key.get(key) == CHECK_SUCCESS for key in required)


class SetupVerificationService:
    """Read-only verification engine + status-card contract."""

    _TITLES = {
        "vpn_tunnel": "نفق VPN",
        "vps_ping": "Ping من الراوتر إلى VPS",
        "router_ping": "Ping من VPS إلى الراوتر",
        "radius_reachable": "وصول RADIUS",
        "api_login": "تسجيل API",
        "hotspot_ready": "جاهزية Hotspot",
        "broadband_ready": "جاهزية Broadband",
    }

    def __init__(
        self,
        *,
        router_probe: RouterReadOnlyProbe | None = None,
        vps_probe: VpsNetworkProbe | None = None,
        radius_probe: RadiusReadOnlyProbe | None = None,
        diagnostics: SetupDiagnosticsService | None = None,
    ) -> None:
        self.router_probe = router_probe or RouterReadOnlyProbe()
        self.vps_probe = vps_probe or VpsNetworkProbe(ServerPingVpsProbeAdapter())
        self.radius_probe = radius_probe or RadiusReadOnlyProbe()
        self.diagnostics = diagnostics or SetupDiagnosticsService()

    def build_contract(
        self,
        *,
        internet_verified: bool,
        vpn_verified: bool,
        statuses: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        status_map = statuses or {}
        cards: list[VerificationCard] = []
        for key in VERIFICATION_KEYS:
            status = status_map.get(key, "pending")
            if status not in VERIFICATION_STATUSES:
                status = "pending"
            if key in {"vpn_tunnel", "vps_ping", "router_ping", "radius_reachable", "api_login"} and not internet_verified:
                status = "blocked"
            if key in {"hotspot_ready", "broadband_ready"} and not vpn_verified:
                status = "blocked"
            cards.append(
                VerificationCard(
                    key=key,
                    status=status,
                    title_ar=self._TITLES[key],
                    details_ar=_details_for_status(status),
                )
            )
        return {"cards": [card.to_dict() for card in cards], "status_map": {c.key: c.status for c in cards}}

    def verify_internet(
        self,
        *,
        run: dict[str, Any],
        internet_input: dict[str, Any],
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> VerificationResult:
        started = time.perf_counter()
        payload = payload or {}
        checks: list[dict[str, Any]] = []
        diagnostic_codes: list[str] = []
        observations: dict[str, Any] = {"mode": mode}
        output = str(payload.get("output") or "")
        manual_checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
        nat_enabled = _as_bool(internet_input.get("nat_enabled"), False)
        selected_iface = str(run.get("selected_wan_interface") or "").strip()
        requires_dns = bool(internet_input.get("dns")) or _as_bool(internet_input.get("use_peer_dns"), False)
        expects_default_route = _as_bool(internet_input.get("add_default_route"), True) or str(run.get("internet_source_type") or "") == "static"

        if mode == "pasted_output":
            ping_ok = _has_ping_success(output, "8.8.8.8")
            checks.append(_check("ping_8_8_8_8", "Ping 8.8.8.8", CHECK_SUCCESS if ping_ok else CHECK_FAILED))
            if not ping_ok:
                diagnostic_codes.append("internet_ping_failed")
            if requires_dns:
                dns_ok = _has_ping_success(output, "cloudflare.com") or ("cloudflare.com" in output.lower() and "timeout" not in output.lower())
                checks.append(_check("dns_resolution", "فحص DNS", CHECK_SUCCESS if dns_ok else CHECK_FAILED))
                if not dns_ok:
                    diagnostic_codes.append("dns_failed")
            else:
                checks.append(_check("dns_resolution", "فحص DNS", CHECK_SKIPPED))
        elif mode == "manual_contract":
            ping_ok = _as_bool(manual_checks.get("ping_8_8_8_8"), False)
            checks.append(_check("ping_8_8_8_8", "Ping 8.8.8.8", CHECK_SUCCESS if ping_ok else CHECK_FAILED))
            if not ping_ok:
                diagnostic_codes.append("internet_ping_failed")
            if requires_dns:
                dns_ok = _as_bool(manual_checks.get("dns_resolution"), False)
                checks.append(_check("dns_resolution", "فحص DNS", CHECK_SUCCESS if dns_ok else CHECK_FAILED))
                if not dns_ok:
                    diagnostic_codes.append("dns_failed")
            else:
                checks.append(_check("dns_resolution", "فحص DNS", CHECK_SKIPPED))
        else:
            try:
                interfaces = self.router_probe.get_interfaces()
                routes = self.router_probe.get_routes()
                nat_rules = self.router_probe.get_nat_rules()
                ping = self.router_probe.ping("8.8.8.8", count=3)
                observations["router_probe"] = {
                    "interfaces_count": len(interfaces),
                    "routes_count": len(routes),
                    "nat_rules_count": len(nat_rules),
                    "ping_8_8_8_8": ping,
                }
                ping_ok = _as_bool(ping.get("ok"), False) or _as_bool(ping.get("success"), False) or _has_ping_success(str(ping))
                checks.append(_check("ping_8_8_8_8", "Ping 8.8.8.8", CHECK_SUCCESS if ping_ok else CHECK_FAILED))
                if not ping_ok:
                    diagnostic_codes.append("internet_ping_failed")

                iface_ok = True
                if selected_iface:
                    iface_ok = any(str(item.get("name") or "") == selected_iface for item in interfaces)
                checks.append(_check("selected_uplink_present", "وجود واجهة الرفع", CHECK_SUCCESS if iface_ok else CHECK_FAILED))
                if not iface_ok:
                    diagnostic_codes.append("uplink_interface_missing")

                if expects_default_route:
                    route_ok = any(str(item.get("dst-address") or item.get("dst_address") or "") in {"0.0.0.0/0", "::/0"} for item in routes)
                    checks.append(_check("default_route_present", "وجود default route", CHECK_SUCCESS if route_ok else CHECK_FAILED))
                    if not route_ok:
                        diagnostic_codes.append("default_route_missing")
                else:
                    checks.append(_check("default_route_present", "وجود default route", CHECK_SKIPPED))

                if nat_enabled:
                    nat_ok = any(
                        str(item.get("chain") or "").lower() == "srcnat"
                        and str(item.get("action") or "").lower() == "masquerade"
                        and (
                            not selected_iface
                            or str(item.get("out-interface") or item.get("out_interface") or "") == selected_iface
                        )
                        for item in nat_rules
                    )
                    checks.append(_check("nat_rule_present", "وجود NAT", CHECK_SUCCESS if nat_ok else CHECK_FAILED))
                    if not nat_ok:
                        diagnostic_codes.append("nat_missing")
                else:
                    checks.append(_check("nat_rule_present", "وجود NAT", CHECK_SKIPPED))

                if requires_dns:
                    dns_probe = self.router_probe.get_dns()
                    dns_ok = bool(dns_probe.get("servers") or dns_probe.get("server"))
                    checks.append(_check("dns_resolution", "فحص DNS", CHECK_SUCCESS if dns_ok else CHECK_FAILED))
                    if not dns_ok:
                        diagnostic_codes.append("dns_failed")
                else:
                    checks.append(_check("dns_resolution", "فحص DNS", CHECK_SKIPPED))
            except ProbeUnavailableError:
                checks.append(_check("probe", "فحص مباشر", CHECK_BLOCKED, "موصل الفحص غير متاح"))
                diagnostic_codes.append("probe_unavailable")
            except Exception as exc:  # pragma: no cover - safety net
                checks.append(_check("probe", "فحص مباشر", CHECK_FAILED, str(exc)))
                diagnostic_codes.append("probe_unavailable")

        required = {"ping_8_8_8_8"}
        gate_unlocked = _all_success(checks, required)
        overall_status = VERIFY_STATUS_SUCCESS if gate_unlocked else VERIFY_STATUS_FAILED
        if any(item["status"] == CHECK_BLOCKED for item in checks):
            overall_status = VERIFY_STATUS_BLOCKED
        elif any(item["status"] == CHECK_SKIPPED for item in checks) and not gate_unlocked:
            overall_status = VERIFY_STATUS_PARTIAL
        duration_ms = int((time.perf_counter() - started) * 1000)
        diagnostics = [self.diagnostics.get_diagnostic(code) for code in dict.fromkeys(diagnostic_codes)]
        next_action = "تم التحقق من الإنترنت بنجاح. يمكنك الانتقال لخطوة الربط والمصادقة." if gate_unlocked else "راجع التشخيص ثم أعد المحاولة أو استخدم وضع تحليل المخرجات."
        return VerificationResult(
            overall_status=overall_status,
            checks=checks,
            diagnostics=diagnostics,
            raw_observations=_mask_secrets(observations),
            duration_ms=duration_ms,
            next_action_ar=next_action,
            gate_unlocked=gate_unlocked,
        )

    def verify_vpn_radius(
        self,
        *,
        run: dict[str, Any],
        vpn_payload: dict[str, Any] | None,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> VerificationResult:
        started = time.perf_counter()
        payload = payload or {}
        vpn_payload = vpn_payload or {}
        checks: list[dict[str, Any]] = []
        diagnostic_codes: list[str] = []
        observations: dict[str, Any] = {"mode": mode}
        output = str(payload.get("output") or "")
        manual_checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
        router_vpn_ip = str(vpn_payload.get("router_vpn_ip") or "")
        vps_vpn_ip = str(vpn_payload.get("vps_vpn_ip") or "")
        peer_name = str(vpn_payload.get("peer_name") or "vps-peer")
        wg_interface = str(vpn_payload.get("wg_interface_name") or "hr-wg")
        router_public_key = str(vpn_payload.get("router_public_key") or "").strip()
        expected_allowed_ips = str(
            vpn_payload.get("expected_allowed_ips")
            or (f"{router_vpn_ip}/32" if router_vpn_ip else "")
        ).strip()

        if mode == "pasted_output":
            output_lower = output.lower()
            router_ping_ok = _has_ping_success(output, vps_vpn_ip or "10.10.0.1")
            vpn_text_ok = any(
                marker in output_lower
                for marker in (
                    "latest handshake",
                    "latest-handshake",
                    "last-handshake",
                    "wireguard",
                    "interface=hr-wg",
                    "public-key=",
                )
            )
            vpn_ok = router_ping_ok or vpn_text_ok
            observations["vpn_evidence"] = "router_ping_vps" if router_ping_ok else "pasted_wireguard_output"
            vps_ping_ok = ("vps_ping_router=ok" in output_lower) or ("vps->router ok" in output_lower)
            if not vps_ping_ok and router_vpn_ip and router_ping_ok:
                try:
                    back_ping = self.vps_probe.ping_router_vpn_ip(router_vpn_ip)
                    observations["vps_ping_router_probe"] = back_ping
                    vps_ping_ok = _as_bool(back_ping.get("ok"), False) or _has_ping_success(str(back_ping))
                except Exception as exc:
                    observations["vps_ping_router_probe"] = {
                        "ok": False,
                        "blocked": True,
                        "code": "vps_ping_probe_unavailable",
                        "error": str(exc),
                    }
            radius_ok = "/radius" in output.lower() and ("address=" in output.lower() or "service=" in output.lower())
            api_ok = "/user" in output.lower() and ("api" in output.lower() or "name=" in output.lower())

            checks.extend(
                [
                    _check("vpn_tunnel", "حالة نفق VPN", CHECK_SUCCESS if vpn_ok else CHECK_FAILED),
                    _check("router_ping_vps", "Ping الراوتر إلى VPS", CHECK_SUCCESS if router_ping_ok else CHECK_FAILED),
                    _check("vps_ping_router", "Ping VPS إلى الراوتر", CHECK_SUCCESS if vps_ping_ok else CHECK_FAILED),
                    _check("radius_reachable", "وصول RADIUS", CHECK_SUCCESS if radius_ok else CHECK_FAILED),
                    _check("api_login", "تسجيل API", CHECK_SUCCESS if api_ok else CHECK_FAILED),
                ]
            )
        elif mode == "manual_contract":
            checks.extend(
                [
                    _check("vpn_tunnel", "حالة نفق VPN", CHECK_SUCCESS if _as_bool(manual_checks.get("vpn_tunnel")) else CHECK_FAILED),
                    _check("router_ping_vps", "Ping الراوتر إلى VPS", CHECK_SUCCESS if _as_bool(manual_checks.get("router_ping_vps")) else CHECK_FAILED),
                    _check("vps_ping_router", "Ping VPS إلى الراوتر", CHECK_SUCCESS if _as_bool(manual_checks.get("vps_ping_router")) else CHECK_FAILED),
                    _check("radius_reachable", "وصول RADIUS", CHECK_SUCCESS if _as_bool(manual_checks.get("radius_reachable")) else CHECK_FAILED),
                    _check("api_login", "تسجيل API", CHECK_SUCCESS if _as_bool(manual_checks.get("api_login")) else CHECK_FAILED),
                ]
            )
        else:
            try:
                peer = self.vps_probe.inspect_wireguard_peer(peer_name)
                route_ping = self.router_probe.ping(vps_vpn_ip or "10.10.0.1", count=3)
                back_ping = self.vps_probe.ping_router_vpn_ip(router_vpn_ip or "10.10.0.3")
                radius_servers = self.router_probe.get_radius_servers()
                identity = self.router_probe.get_identity()
                observations["probe"] = {
                    "wireguard_peer": peer,
                    "router_ping_vps": route_ping,
                    "vps_ping_router": back_ping,
                    "radius_servers_count": len(radius_servers),
                    "identity": identity,
                }
                vpn_ok = _as_bool(peer.get("ok"), False) or _as_bool(peer.get("handshake_ok"), False) or bool(peer.get("latest_handshake"))
                router_ping_ok = _as_bool(route_ping.get("ok"), False) or _has_ping_success(str(route_ping))
                vps_ping_ok = _as_bool(back_ping.get("ok"), False) or _has_ping_success(str(back_ping))
                radius_ok = any(str(item.get("address") or "") == str(vpn_payload.get("radius_server_ip") or "") for item in radius_servers) if vpn_payload.get("radius_server_ip") else bool(radius_servers)
                api_ok = _as_bool(identity.get("api_login_ok"), False) if "api_login_ok" in identity else True
                api_user_present = _as_bool(identity.get("api_user_present"), False) if "api_user_present" in identity else True

                checks.extend(
                    [
                        _check("vpn_tunnel", "حالة نفق VPN", CHECK_SUCCESS if vpn_ok else CHECK_FAILED),
                        _check("router_ping_vps", "Ping الراوتر إلى VPS", CHECK_SUCCESS if router_ping_ok else CHECK_FAILED),
                        _check("vps_ping_router", "Ping VPS إلى الراوتر", CHECK_SUCCESS if vps_ping_ok else CHECK_FAILED),
                        _check("radius_reachable", "وصول RADIUS", CHECK_SUCCESS if radius_ok else CHECK_FAILED),
                        _check("api_login", "تسجيل API", CHECK_SUCCESS if api_ok else CHECK_FAILED),
                        _check("generated_api_user_present", "وجود مستخدم API المخطط", CHECK_SUCCESS if api_user_present else CHECK_FAILED),
                        _check("radius_entry_present", "وجود إدخال RADIUS", CHECK_SUCCESS if radius_ok else CHECK_FAILED),
                    ]
                )
            except ProbeUnavailableError:
                checks.append(_check("probe", "فحص مباشر", CHECK_BLOCKED, "موصل الفحص غير متاح"))
                diagnostic_codes.append("probe_unavailable")
            except Exception as exc:  # pragma: no cover
                checks.append(_check("probe", "فحص مباشر", CHECK_FAILED, str(exc)))
                diagnostic_codes.append("probe_unavailable")

        consistency_required: set[str] = set()
        if router_vpn_ip:
            observed_router_ips = _extract_router_interface_ips(output, wg_interface)
            observations["expected_router_vpn_ip"] = router_vpn_ip
            if observed_router_ips:
                observations["mikrotik_router_vpn_ips"] = observed_router_ips
                router_ip_ok = router_vpn_ip in observed_router_ips
                checks.append(
                    _check(
                        "router_vpn_ip_consistency",
                        "تطابق عنوان الراوتر المحجوز",
                        CHECK_SUCCESS if router_ip_ok else CHECK_FAILED,
                    )
                )
                consistency_required.add("router_vpn_ip_consistency")
                if not router_ip_ok:
                    diagnostic_codes.append("router_vpn_ip_mismatch")

        server_allowed_mismatch = False
        if expected_allowed_ips and router_public_key:
            pasted_peer = _find_peer_by_public_key(_parse_wg_show_peers(output), router_public_key)
            peer_probe: dict[str, Any] | None = pasted_peer
            if pasted_peer:
                observations["server_peer_allowed_ips_output"] = {
                    "allowed_ips": pasted_peer.get("allowed_ips") or "",
                    "latest_handshake": pasted_peer.get("latest_handshake") or "",
                }
            else:
                try:
                    peer_probe = self.vps_probe.inspect_wireguard_peer(router_public_key)
                    observations["server_peer_allowed_ips_probe"] = peer_probe
                except Exception as exc:
                    peer_probe = {
                        "ok": False,
                        "blocked": True,
                        "code": "server_peer_probe_unavailable",
                        "error": str(exc),
                    }
                    observations["server_peer_allowed_ips_probe"] = peer_probe
            observed_allowed_ips = _extract_allowed_ips(peer_probe)
            if observed_allowed_ips:
                server_allowed_ok = _allowed_ips_match(observed_allowed_ips, expected_allowed_ips)
                checks.append(
                    _check(
                        "server_allowed_ips_consistency",
                        "تطابق عنوان الراوتر على الخادم",
                        CHECK_SUCCESS if server_allowed_ok else CHECK_FAILED,
                    )
                )
                consistency_required.add("server_allowed_ips_consistency")
                if not server_allowed_ok:
                    server_allowed_mismatch = True
                    diagnostic_codes.append("server_allowed_ip_mismatch")
            elif peer_probe and peer_probe.get("blocked"):
                checks.append(
                    _check(
                        "server_allowed_ips_consistency",
                        "تطابق عنوان الراوتر على الخادم",
                        CHECK_SKIPPED,
                        str(peer_probe.get("code") or "server peer probe unavailable"),
                    )
                )

        by_key = {item["key"]: item["status"] for item in checks}
        if by_key.get("vpn_tunnel") != CHECK_SUCCESS:
            diagnostic_codes.append("vpn_not_handshaking")
        if by_key.get("router_ping_vps") != CHECK_SUCCESS or (mode != "pasted_output" and by_key.get("vps_ping_router") != CHECK_SUCCESS):
            diagnostic_codes.append("route_missing")
        if by_key.get("radius_reachable") != CHECK_SUCCESS and mode != "pasted_output":
            diagnostic_codes.append("radius_server_unreachable")
        if by_key.get("api_login") != CHECK_SUCCESS and mode != "pasted_output":
            diagnostic_codes.append("api_login_failed")
        if by_key.get("generated_api_user_present") == CHECK_FAILED:
            diagnostic_codes.append("api_user_missing")

        required = {"vpn_tunnel", "router_ping_vps"} if mode == "pasted_output" else {"vpn_tunnel", "router_ping_vps", "vps_ping_router", "radius_reachable", "api_login"}
        required |= consistency_required
        gate_unlocked = _all_success(checks, required)
        overall_status = VERIFY_STATUS_SUCCESS if gate_unlocked else VERIFY_STATUS_FAILED
        if any(item["status"] == CHECK_BLOCKED for item in checks):
            overall_status = VERIFY_STATUS_BLOCKED
        elif any(item["status"] == CHECK_SKIPPED for item in checks):
            overall_status = VERIFY_STATUS_PARTIAL if not gate_unlocked else VERIFY_STATUS_SUCCESS
        duration_ms = int((time.perf_counter() - started) * 1000)
        diagnostics = [self.diagnostics.get_diagnostic(code) for code in dict.fromkeys(diagnostic_codes)]
        if server_allowed_mismatch:
            next_action = "تم اكتشاف اتصال WireGuard، لكن عنوان الراوتر على الخادم غير مطابق. اضغط إصلاح الربط على الخادم."
        else:
            next_action = "تم تحقق الربط والمصادقة بنجاح. يمكنك الانتقال لواجهة اختيار الواجهات وخدمات الهوتسبوت أو البرودباند." if gate_unlocked else "راجع التشخيص. يمكنك لصق مخرجات الفحص من MikroTik للحصول على نتيجة أدق."
        return VerificationResult(
            overall_status=overall_status,
            checks=checks,
            diagnostics=diagnostics,
            raw_observations=_mask_secrets(observations),
            duration_ms=duration_ms,
            next_action_ar=next_action,
            gate_unlocked=gate_unlocked,
        )

    def verify_hotspot(
        self,
        *,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> VerificationResult:
        started = time.perf_counter()
        payload = payload or {}
        checks: list[dict[str, Any]] = []
        diagnostic_codes: list[str] = []
        observations: dict[str, Any] = {"mode": mode}
        output = str(payload.get("output") or "")
        manual_checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}

        if mode == "pasted_output":
            server_ok = "/ip hotspot" in output.lower() and "name=" in output.lower()
            radius_ok = "use-radius=yes" in output.lower() or "radius" in output.lower()
            pool_ok = "/ip pool" in output.lower()
            nat_ok = "/ip firewall nat" in output.lower() and "masquerade" in output.lower()
        elif mode == "manual_contract":
            server_ok = _as_bool(manual_checks.get("hotspot_server_present"))
            radius_ok = _as_bool(manual_checks.get("radius_enabled"))
            pool_ok = _as_bool(manual_checks.get("hotspot_pool_present"))
            nat_ok = _as_bool(manual_checks.get("hotspot_nat_present"))
        else:
            try:
                hs_servers = self.router_probe.get_hotspot_servers()
                nat_rules = self.router_probe.get_nat_rules()
                observations["probe"] = {
                    "hotspot_servers_count": len(hs_servers),
                    "nat_rules_count": len(nat_rules),
                }
                server_ok = bool(hs_servers)
                radius_ok = any(_as_bool(item.get("use-radius"), False) or _as_bool(item.get("use_radius"), False) for item in hs_servers)
                pool_ok = any(str(item.get("address-pool") or item.get("address_pool") or "").strip() for item in hs_servers)
                nat_ok = any(str(item.get("chain") or "").lower() == "srcnat" and str(item.get("action") or "").lower() == "masquerade" for item in nat_rules)
            except ProbeUnavailableError:
                checks.append(_check("probe", "فحص مباشر", CHECK_BLOCKED, "موصل الفحص غير متاح"))
                diagnostic_codes.append("probe_unavailable")
                server_ok = radius_ok = pool_ok = nat_ok = False
            except Exception as exc:  # pragma: no cover
                checks.append(_check("probe", "فحص مباشر", CHECK_FAILED, str(exc)))
                server_ok = radius_ok = pool_ok = nat_ok = False

        checks.extend(
            [
                _check("hotspot_server_present", "وجود Hotspot Server", CHECK_SUCCESS if server_ok else CHECK_FAILED),
                _check("hotspot_profile_present", "وجود Hotspot Profile", CHECK_SUCCESS if server_ok else CHECK_FAILED),
                _check("radius_enabled", "تفعيل RADIUS", CHECK_SUCCESS if radius_ok else CHECK_FAILED),
                _check("hotspot_pool_present", "وجود Pool", CHECK_SUCCESS if pool_ok else CHECK_FAILED),
                _check("hotspot_address_present", "وجود عنوان الشبكة", CHECK_SUCCESS if server_ok else CHECK_FAILED),
                _check("hotspot_nat_present", "وجود NAT", CHECK_SUCCESS if nat_ok else CHECK_FAILED),
            ]
        )
        if not server_ok:
            diagnostic_codes.append("hotspot_server_missing")
        if not radius_ok:
            diagnostic_codes.append("hotspot_radius_disabled")
        if not pool_ok:
            diagnostic_codes.append("hotspot_pool_missing")
        if not nat_ok:
            diagnostic_codes.append("hotspot_nat_missing")

        required = {"hotspot_server_present", "radius_enabled"}
        gate_unlocked = _all_success(checks, required)
        overall_status = VERIFY_STATUS_SUCCESS if gate_unlocked else VERIFY_STATUS_FAILED
        if any(item["status"] == CHECK_BLOCKED for item in checks):
            overall_status = VERIFY_STATUS_BLOCKED
        duration_ms = int((time.perf_counter() - started) * 1000)
        diagnostics = [self.diagnostics.get_diagnostic(code) for code in dict.fromkeys(diagnostic_codes)]
        next_action = "Hotspot جاهز بحسب نتائج الفحص." if gate_unlocked else "Hotspot غير مكتمل. راجع التشخيص وأعد الفحص."
        return VerificationResult(
            overall_status=overall_status,
            checks=checks,
            diagnostics=diagnostics,
            raw_observations=_mask_secrets(observations),
            duration_ms=duration_ms,
            next_action_ar=next_action,
            gate_unlocked=gate_unlocked,
        )

    def verify_broadband(
        self,
        *,
        mode: str,
        payload: dict[str, Any] | None = None,
    ) -> VerificationResult:
        started = time.perf_counter()
        payload = payload or {}
        checks: list[dict[str, Any]] = []
        diagnostic_codes: list[str] = []
        observations: dict[str, Any] = {"mode": mode}
        output = str(payload.get("output") or "")
        manual_checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}

        if mode == "pasted_output":
            service_ok = "pppoe" in output.lower() and "service" in output.lower()
            profile_ok = "/ppp profile" in output.lower() or "profile=" in output.lower()
            pool_ok = "/ip pool" in output.lower()
            radius_ok = "use-radius=yes" in output.lower() or "ppp aaa" in output.lower()
            nat_ok = "/ip firewall nat" in output.lower() and "masquerade" in output.lower()
        elif mode == "manual_contract":
            service_ok = _as_bool(manual_checks.get("pppoe_service_present"))
            profile_ok = _as_bool(manual_checks.get("ppp_profile_present"))
            pool_ok = _as_bool(manual_checks.get("remote_pool_present"))
            radius_ok = _as_bool(manual_checks.get("radius_enabled"))
            nat_ok = _as_bool(manual_checks.get("broadband_nat_present"))
        else:
            try:
                pppoe_servers = self.router_probe.get_pppoe_servers()
                nat_rules = self.router_probe.get_nat_rules()
                observations["probe"] = {
                    "pppoe_servers_count": len(pppoe_servers),
                    "nat_rules_count": len(nat_rules),
                }
                service_ok = bool(pppoe_servers)
                profile_ok = any(str(item.get("default-profile") or item.get("default_profile") or "").strip() for item in pppoe_servers)
                pool_ok = any(str(item.get("remote-address") or item.get("remote_address") or "").strip() for item in pppoe_servers)
                radius_ok = any(_as_bool(item.get("use-radius"), False) or _as_bool(item.get("use_radius"), False) for item in pppoe_servers)
                nat_ok = any(str(item.get("chain") or "").lower() == "srcnat" and str(item.get("action") or "").lower() == "masquerade" for item in nat_rules)
            except ProbeUnavailableError:
                checks.append(_check("probe", "فحص مباشر", CHECK_BLOCKED, "موصل الفحص غير متاح"))
                diagnostic_codes.append("probe_unavailable")
                service_ok = profile_ok = pool_ok = radius_ok = nat_ok = False
            except Exception as exc:  # pragma: no cover
                checks.append(_check("probe", "فحص مباشر", CHECK_FAILED, str(exc)))
                service_ok = profile_ok = pool_ok = radius_ok = nat_ok = False

        checks.extend(
            [
                _check("pppoe_service_present", "وجود خدمة PPPoE", CHECK_SUCCESS if service_ok else CHECK_FAILED),
                _check("ppp_profile_present", "وجود PPP Profile", CHECK_SUCCESS if profile_ok else CHECK_FAILED),
                _check("remote_pool_present", "وجود Remote Pool", CHECK_SUCCESS if pool_ok else CHECK_FAILED),
                _check("radius_enabled", "تفعيل RADIUS", CHECK_SUCCESS if radius_ok else CHECK_FAILED),
                _check("broadband_nat_present", "وجود NAT", CHECK_SUCCESS if nat_ok else CHECK_FAILED),
            ]
        )

        if not service_ok:
            diagnostic_codes.append("pppoe_service_missing")
        if not profile_ok:
            diagnostic_codes.append("ppp_profile_missing")
        if not pool_ok:
            diagnostic_codes.append("broadband_pool_missing")
        if not radius_ok:
            diagnostic_codes.append("ppp_radius_disabled")
        if not nat_ok:
            diagnostic_codes.append("broadband_nat_missing")

        required = {"pppoe_service_present", "radius_enabled", "broadband_nat_present"}
        gate_unlocked = _all_success(checks, required)
        overall_status = VERIFY_STATUS_SUCCESS if gate_unlocked else VERIFY_STATUS_FAILED
        if any(item["status"] == CHECK_BLOCKED for item in checks):
            overall_status = VERIFY_STATUS_BLOCKED
        duration_ms = int((time.perf_counter() - started) * 1000)
        diagnostics = [self.diagnostics.get_diagnostic(code) for code in dict.fromkeys(diagnostic_codes)]
        next_action = "Broadband جاهز بحسب نتائج الفحص." if gate_unlocked else "Broadband غير مكتمل. راجع التشخيص وأعد الفحص."
        return VerificationResult(
            overall_status=overall_status,
            checks=checks,
            diagnostics=diagnostics,
            raw_observations=_mask_secrets(observations),
            duration_ms=duration_ms,
            next_action_ar=next_action,
            gate_unlocked=gate_unlocked,
        )


class SetupVerificationEngine:
    """Coordinator facade used by setup wizard service/routes."""

    def __init__(self, verifier: SetupVerificationService | None = None) -> None:
        self._verifier = verifier or SetupVerificationService()

    def verify_internet(self, **kwargs: Any) -> dict[str, Any]:
        return self._verifier.verify_internet(**kwargs).to_dict()

    def verify_vpn_radius(self, **kwargs: Any) -> dict[str, Any]:
        return self._verifier.verify_vpn_radius(**kwargs).to_dict()

    def verify_hotspot(self, **kwargs: Any) -> dict[str, Any]:
        return self._verifier.verify_hotspot(**kwargs).to_dict()

    def verify_broadband(self, **kwargs: Any) -> dict[str, Any]:
        return self._verifier.verify_broadband(**kwargs).to_dict()
