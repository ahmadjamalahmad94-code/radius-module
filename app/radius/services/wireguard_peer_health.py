"""Read-only WireGuard peer health evaluation for setup wizard peers."""
from __future__ import annotations

import os
import re
from typing import Any

from .setup_wizard_server_wg import ServerWireGuardInspector
from .setup_wizard_server_wg_readiness import SafeCommandRunner, build_server_wg_command_runner


DEFAULT_STALE_AFTER_SECONDS = 300
DEFAULT_OFFLINE_AFTER_SECONDS = 900
WG_INTERFACE_ENV = "HOBERADIUS_WG_INTERFACE"
DEFAULT_WG_INTERFACE = "wg0"


class WireGuardPeerHealthService:
    """Classify a prepared server peer from read-only `wg show` observations."""

    def __init__(
        self,
        *,
        runner: SafeCommandRunner | None = None,
        interface: str | None = None,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        offline_after_seconds: int = DEFAULT_OFFLINE_AFTER_SECONDS,
    ) -> None:
        self.runner = runner or build_server_wg_command_runner()
        self.interface = interface or os.environ.get(WG_INTERFACE_ENV) or DEFAULT_WG_INTERFACE
        self.stale_after_seconds = int(stale_after_seconds)
        self.offline_after_seconds = int(offline_after_seconds)

    def inspect_peer(
        self,
        *,
        prepared_peer: dict[str, Any],
        wg_show_output: str | None = None,
        previous_observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        output = wg_show_output
        source = "pasted_output" if output is not None else "runner"
        runner_result: dict[str, Any] | None = None
        if output is None:
            runner_result = self.runner.execute_read_only(f"wg show {self.interface}")
            if not runner_result.get("ok"):
                return self._result(
                    status="unknown",
                    score=25,
                    peer={},
                    peer_count=0,
                    diagnostics=[self._diag("probe_unavailable")],
                    source=source,
                    runner_result=runner_result,
                )
            output = str(runner_result.get("stdout") or "")

        peers = ServerWireGuardInspector.parse_wg_show(str(output or ""))
        expected_key = str(prepared_peer.get("router_public_key") or "").strip()
        expected_allowed = str(
            prepared_peer.get("allowed_ips")
            or (f"{prepared_peer.get('router_vpn_ip')}/32" if prepared_peer.get("router_vpn_ip") else "")
        ).strip()

        public_matches = [peer for peer in peers if str(peer.get("public_key") or "").strip() == expected_key]
        allowed_matches = [
            peer
            for peer in peers
            if expected_allowed and expected_allowed in str(peer.get("allowed_ips") or "")
        ]

        if len(public_matches) > 1 or len(allowed_matches) > 1:
            matched = public_matches[0] if public_matches else allowed_matches[0]
            return self._result(
                status="duplicate_peer",
                score=10,
                peer=self._peer_payload(matched),
                peer_count=len(peers),
                diagnostics=[self._diag("duplicate_peer")],
                source=source,
                runner_result=runner_result,
            )

        if public_matches:
            matched = public_matches[0]
            if expected_allowed and expected_allowed not in str(matched.get("allowed_ips") or ""):
                return self._result(
                    status="allowed_ip_mismatch",
                    score=15,
                    peer=self._peer_payload(matched),
                    peer_count=len(peers),
                    diagnostics=[self._diag("allowed_ip_mismatch")],
                    source=source,
                    runner_result=runner_result,
                )
            return self._classify_live_peer(
                matched,
                peer_count=len(peers),
                source=source,
                runner_result=runner_result,
                previous_observation=previous_observation,
            )

        if allowed_matches:
            return self._result(
                status="allowed_ip_mismatch",
                score=15,
                peer=self._peer_payload(allowed_matches[0]),
                peer_count=len(peers),
                diagnostics=[self._diag("public_key_mismatch")],
                source=source,
                runner_result=runner_result,
            )

        return self._result(
            status="missing_peer",
            score=0,
            peer={},
            peer_count=len(peers),
            diagnostics=[self._diag("missing_peer")],
            source=source,
            runner_result=runner_result,
        )

    def _classify_live_peer(
        self,
        peer: dict[str, Any],
        *,
        peer_count: int,
        source: str,
        runner_result: dict[str, Any] | None,
        previous_observation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = self._peer_payload(peer)
        age = payload.get("handshake_age_seconds")
        rx = payload.get("rx_bytes")
        tx = payload.get("tx_bytes")

        if age is None:
            return self._result(
                status="applied_no_handshake",
                score=55,
                peer=payload,
                peer_count=peer_count,
                diagnostics=[self._diag("applied_no_handshake")],
                source=source,
                runner_result=runner_result,
            )

        if self._is_transfer_frozen(previous_observation, rx=rx, tx=tx) and age >= self.offline_after_seconds:
            return self._result(
                status="offline",
                score=30,
                peer=payload,
                peer_count=peer_count,
                diagnostics=[self._diag("offline")],
                source=source,
                runner_result=runner_result,
            )

        if age >= self.stale_after_seconds:
            return self._result(
                status="stale_peer",
                score=45,
                peer=payload,
                peer_count=peer_count,
                diagnostics=[self._diag("stale_peer")],
                source=source,
                runner_result=runner_result,
            )

        return self._result(
            status="healthy",
            score=92,
            peer=payload,
            peer_count=peer_count,
            diagnostics=[self._diag("healthy")],
            source=source,
            runner_result=runner_result,
        )

    @staticmethod
    def _is_transfer_frozen(previous: dict[str, Any] | None, *, rx: int | None, tx: int | None) -> bool:
        if not previous or rx is None or tx is None:
            return False
        prev_peer = previous.get("peer") if isinstance(previous.get("peer"), dict) else previous
        return int(prev_peer.get("rx_bytes") or -1) == int(rx) and int(prev_peer.get("tx_bytes") or -2) == int(tx)

    @classmethod
    def _peer_payload(cls, peer: dict[str, Any]) -> dict[str, Any]:
        rx, tx = cls._extract_transfer(peer)
        latest = str(peer.get("latest_handshake") or "").strip()
        return {
            "public_key_masked": cls._mask_key(str(peer.get("public_key") or "")),
            "allowed_ips": str(peer.get("allowed_ips") or "").strip(),
            "latest_handshake": latest or "never",
            "handshake_age_seconds": cls._parse_handshake_age(latest),
            "rx_bytes": rx,
            "tx_bytes": tx,
            "endpoint": str(peer.get("endpoint") or "").strip() or None,
            "persistent_keepalive": str(peer.get("persistent_keepalive") or "").strip() or None,
        }

    @staticmethod
    def _mask_key(value: str) -> str:
        key = str(value or "").strip()
        if len(key) < 12:
            return "***"
        return f"{key[:6]}...{key[-6:]}"

    @classmethod
    def _extract_transfer(cls, peer: dict[str, Any]) -> tuple[int | None, int | None]:
        rx = cls._parse_size(peer.get("rx_bytes"))
        tx = cls._parse_size(peer.get("tx_bytes"))
        transfer = str(peer.get("transfer") or "").strip()
        if transfer:
            match = re.search(r"(.+?)\s+received,\s+(.+?)\s+sent", transfer, flags=re.I)
            if match:
                rx = cls._parse_size(match.group(1))
                tx = cls._parse_size(match.group(2))
        return rx, tx

    @staticmethod
    def _parse_size(value: Any) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        plain = text.replace(",", "")
        if plain.isdigit():
            return int(plain)
        match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B|B)?$", plain, flags=re.I)
        if not match:
            return None
        number = float(match.group(1))
        unit = (match.group(2) or "B").lower()
        multipliers = {
            "b": 1,
            "kb": 1000,
            "kib": 1024,
            "mb": 1000**2,
            "mib": 1024**2,
            "gb": 1000**3,
            "gib": 1024**3,
            "tb": 1000**4,
            "tib": 1024**4,
        }
        return int(number * multipliers.get(unit, 1))

    @staticmethod
    def _parse_handshake_age(value: str) -> int | None:
        text = str(value or "").strip().lower()
        if not text or text in {"never", "(none)", "none", "0"}:
            return None
        if text in {"now", "just now"}:
            return 0
        text = text.replace("ago", "").replace(" and ", ", ")
        total = 0
        found = False
        units = {
            "second": 1,
            "seconds": 1,
            "minute": 60,
            "minutes": 60,
            "hour": 3600,
            "hours": 3600,
            "day": 86400,
            "days": 86400,
            "week": 604800,
            "weeks": 604800,
        }
        for number, unit in re.findall(r"(\d+)\s+([a-z]+)", text):
            if unit in units:
                total += int(number) * units[unit]
                found = True
        return total if found else None

    def _result(
        self,
        *,
        status: str,
        score: int,
        peer: dict[str, Any],
        peer_count: int,
        diagnostics: list[dict[str, Any]],
        source: str,
        runner_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        checks = {
            "peer_exists": status not in {"missing_peer", "unknown"},
            "allowed_ip_matches": status not in {"missing_peer", "allowed_ip_mismatch", "duplicate_peer", "unknown"},
            "handshake_recent": status == "healthy",
            "transfer_seen": bool(peer.get("rx_bytes") or peer.get("tx_bytes")),
        }
        return {
            "status": status,
            "health_score": max(0, min(100, int(score))),
            "checks": checks,
            "peer": peer,
            "diagnostics": diagnostics,
            "recommendation_ar": diagnostics[0]["suggested_fix_ar"] if diagnostics else "أعد الفحص بعد دقيقة.",
            "raw_observations": {
                "source": source,
                "peer_count": peer_count,
                "runner_mode": getattr(self.runner, "mode", "unknown"),
                "runner_code": (runner_result or {}).get("code", ""),
            },
        }

    @staticmethod
    def _diag(code: str) -> dict[str, Any]:
        catalog = {
            "healthy": (
                "الربط نشط",
                "تم العثور على peer والـ handshake حديث.",
                "استمر إلى خطوة التحقق التالية.",
            ),
            "applied_no_handshake": (
                "تمت إضافة peer بدون handshake بعد",
                "الخادم يرى peer لكن الراوتر لم يتصل بعد.",
                "تأكد من لصق سكربت الراوتر ثم أعد التحقق بعد دقيقة.",
            ),
            "stale_peer": (
                "الـ handshake قديم",
                "peer موجود لكن آخر اتصال قديم.",
                "افحص اتصال الراوتر بالإنترنت و endpoint وفتح UDP.",
            ),
            "missing_peer": (
                "peer غير موجود",
                "لم يتم العثور على public key المتوقع في WireGuard.",
                "راجع خطوة apply أو ألصق مخرجات wg show الصحيحة.",
            ),
            "allowed_ip_mismatch": (
                "عنوان السماح غير مطابق",
                "تم العثور على peer أو IP لكن الربط لا يطابق الحجز.",
                "افحص allowed IP وتأكد أنه يساوي عنوان الراوتر المحجوز /32.",
            ),
            "public_key_mismatch": (
                "Public key غير مطابق",
                "العنوان المحجوز موجود على peer آخر.",
                "تحقق من public key الخاص بالراوتر قبل إعادة apply.",
            ),
            "duplicate_peer": (
                "تكرار في peer",
                "يوجد أكثر من peer بنفس المفتاح أو نفس allowed IP.",
                "أوقف الاختبار وراجع إعدادات WireGuard يدويًا داخل المختبر.",
            ),
            "offline": (
                "الراوتر يبدو غير متصل",
                "الـ handshake قديم وحركة RX/TX لم تتغير.",
                "افحص اتصال VPS والراوتر وجرّب إعادة التحقق بعد دقيقة.",
            ),
            "probe_unavailable": (
                "الفحص غير متاح",
                "لا يوجد runner قراءة فقط متاح أو تم حظره للأمان.",
                "ألصق مخرجات wg show أو فعّل readiness المختبرية للقراءة فقط.",
            ),
        }
        title, explanation, fix = catalog.get(
            code,
            ("حالة غير معروفة", "لم نتمكن من تصنيف حالة peer بدقة.", "أعد التحقق بعد دقيقة أو راجع التفاصيل الهندسية."),
        )
        return {
            "code": code,
            "arabic_title": title,
            "explanation_ar": explanation,
            "suggested_fix_ar": fix,
        }
