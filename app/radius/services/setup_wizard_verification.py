"""SW3 verification contract + diagnostics mapping (preview-only)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


class SetupDiagnosticsService:
    _MAP: dict[str, dict[str, Any]] = {
        "vpn_not_handshaking": {
            "arabic_title": "نفق VPN لا يصافح",
            "explanation_ar": "لا توجد مصافحة WireGuard بين الراوتر والخادم.",
            "likely_causes": [
                "مفتاح peer غير صحيح",
                "المنفذ UDP محجوب",
                "endpoint خاطئ",
            ],
            "suggested_fixes": [
                "تحقق من public key وendpoint في الطرفين",
                "افتح منفذ UDP في الجدار الناري",
            ],
            "commands_to_inspect": [
                "wg show",
                "/interface wireguard peers print detail",
            ],
        },
        "wrong_public_endpoint": {
            "arabic_title": "عنوان Endpoint غير صحيح",
            "explanation_ar": "الراوتر يشير إلى IP/Port لا يطابق عنوان الخادم.",
            "likely_causes": ["IP عام قديم", "منفذ WireGuard غير صحيح"],
            "suggested_fixes": ["حدّث endpoint إلى عنوان VPS الفعلي"],
            "commands_to_inspect": ["wg show", "/interface wireguard peers print detail"],
        },
        "firewall_blocking_udp": {
            "arabic_title": "الجدار الناري يحجب UDP",
            "explanation_ar": "حزم WireGuard لا تصل بسبب قواعد firewall.",
            "likely_causes": ["DROP rule قبل allow", "مزود الخدمة يحجب المنفذ"],
            "suggested_fixes": ["أضف allow UDP للمنفذ", "جرّب منفذ مختلف"],
            "commands_to_inspect": [
                "iptables -S",
                "/ip firewall filter print stats",
            ],
        },
        "wrong_allowed_address": {
            "arabic_title": "Allowed-Address غير متطابق",
            "explanation_ar": "الشبكات المسموحة في peer لا تطابق التصميم.",
            "likely_causes": ["Allowed-address ضيقة", "شبكة VPN مكتوبة خطأ"],
            "suggested_fixes": ["طابق allowed-address مع خطة الإعداد"],
            "commands_to_inspect": ["wg show", "/interface wireguard peers print detail"],
        },
        "route_missing": {
            "arabic_title": "مسار مفقود",
            "explanation_ar": "لا يوجد route للوصول لشبكات VPN/RADIUS.",
            "likely_causes": ["لم تتم إضافة route", "gateway خاطئ"],
            "suggested_fixes": ["أضف المسار من script المقترح"],
            "commands_to_inspect": ["/ip route print detail"],
        },
        "radius_secret_mismatch": {
            "arabic_title": "Secret RADIUS غير متطابق",
            "explanation_ar": "secret بين الراوتر والخادم لا يتطابق.",
            "likely_causes": ["نسخ secret بشكل خاطئ", "وجود إعداد قديم متعارض"],
            "suggested_fixes": ["حدّث secret في الطرفين", "عزل إعدادات هوب راديوس بالتعليقات"],
            "commands_to_inspect": ["/radius print detail"],
        },
        "radius_server_unreachable": {
            "arabic_title": "خادم RADIUS غير قابل للوصول",
            "explanation_ar": "الراوتر لا يصل إلى عنوان RADIUS عبر VPN.",
            "likely_causes": ["VPN غير جاهز", "port 1812/1813 محجوب"],
            "suggested_fixes": ["اختبر ping عبر VPN", "افحص firewall للخادم"],
            "commands_to_inspect": ["/tool ping 10.10.0.1 count=5", "ss -ulpn | grep 1812"],
        },
        "api_login_failed": {
            "arabic_title": "فشل تسجيل API",
            "explanation_ar": "تعذر تسجيل الدخول عبر MikroTik API.",
            "likely_causes": ["مستخدم API غير موجود", "صلاحيات غير كافية", "جدار ناري"],
            "suggested_fixes": ["أنشئ مستخدم API مخصص", "تحقق من service api"],
            "commands_to_inspect": ["/user print detail", "/ip service print"],
        },
        "router_dns_issue": {
            "arabic_title": "مشكلة DNS على الراوتر",
            "explanation_ar": "الراوتر لا يحل أسماء النطاقات بشكل صحيح.",
            "likely_causes": ["DNS غير مضبوط", "DNS مزود الخدمة غير متاح"],
            "suggested_fixes": ["حدد DNS موثوق (1.1.1.1/8.8.8.8)"],
            "commands_to_inspect": ["/ip dns print", "/tool ping cloudflare.com count=3"],
        },
        "router_time_issue": {
            "arabic_title": "وقت الراوتر غير صحيح",
            "explanation_ar": "الوقت غير متزامن وقد يؤثر على مصادقة الجلسات.",
            "likely_causes": ["NTP غير مفعل", "timezone خاطئة"],
            "suggested_fixes": ["فعّل NTP وحدد timezone صحيحة"],
            "commands_to_inspect": ["/system clock print", "/system ntp client print"],
        },
        "duplicate_config_conflict": {
            "arabic_title": "تعارض إعدادات مكررة",
            "explanation_ar": "تم العثور على عناصر قديمة تتعارض مع خطة الإعداد.",
            "likely_causes": ["تعليقات غير موحدة", "واجهات/مسارات قديمة"],
            "suggested_fixes": ["راجع العناصر القديمة قبل أي تطبيق جديد"],
            "commands_to_inspect": [
                "/interface wireguard print detail",
                "/radius print detail",
            ],
        },
        "management_interface_conflict": {
            "arabic_title": "تعارض مع واجهة الإدارة",
            "explanation_ar": "الواجهة المختارة قد تكون واجهة الدخول الحالية للإدارة.",
            "likely_causes": ["اختيار منفذ إدارة كمنفذ uplink"],
            "suggested_fixes": ["استخدم منفذ WAN الصحيح أو نفّذ من جلسة محلية"],
            "commands_to_inspect": ["/ip address print", "/interface print detail"],
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
                "suggested_fixes": ["أعد الفحص مع سجل أدق"],
                "commands_to_inspect": [],
            }
        return {"code": code, **payload}

    def list_all(self) -> dict[str, dict[str, Any]]:
        return {key: self.get_diagnostic(key) for key in self._MAP}


class SetupVerificationService:
    _TITLES = {
        "vpn_tunnel": "نفق VPN",
        "vps_ping": "Ping من الراوتر إلى VPS",
        "router_ping": "Ping من VPS إلى الراوتر",
        "radius_reachable": "وصول RADIUS",
        "api_login": "تسجيل API",
        "hotspot_ready": "جاهزية Hotspot",
        "broadband_ready": "جاهزية Broadband",
    }

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
                    details_ar=self._details_for_status(status),
                )
            )
        return {"cards": [card.to_dict() for card in cards], "status_map": {c.key: c.status for c in cards}}

    @staticmethod
    def _details_for_status(status: str) -> str:
        if status == "success":
            return "تم التحقق بنجاح."
        if status == "failed":
            return "فشل الفحص، راجع التشخيص."
        if status == "blocked":
            return "هذا الفحص محجوب حتى استكمال الخطوات السابقة."
        return "بانتظار التنفيذ والفحص."
