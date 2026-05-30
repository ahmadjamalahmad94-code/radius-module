"""SW3 VPN/RADIUS bootstrap planner (preview-only, no execution)."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

from .setup_wizard_common import SetupWizardValidationError, assert_safe_script


def _safe_name(value: Any, *, fallback: str) -> str:
    raw = str(value or "").strip() or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        raise SetupWizardValidationError("generated name resolved to empty value")
    return safe[:48]


def _v4(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    try:
        ipaddress.IPv4Address(raw)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{field} must be valid IPv4") from exc
    return raw


def _cidr(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{field} must be valid IPv4 CIDR") from exc
    if network.version != 4:
        raise SetupWizardValidationError(f"{field} must be IPv4 CIDR")
    return str(network)


def _wireguard_public_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", raw):
        raise SetupWizardValidationError("server_public_key must be a valid WireGuard public key")
    return raw


@dataclass(frozen=True)
class VpnRadiusBootstrapPlan:
    script_text: str
    rollback_script_text: str
    validation_commands: list[str]
    warnings: list[str]
    generated_objects: list[dict[str, str]]
    masked_sensitive_values: dict[str, str]
    diagnostics_hints: list[str]
    router_provisioning: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "script_text": self.script_text,
            "rollback_script_text": self.rollback_script_text,
            "validation_commands": self.validation_commands,
            "warnings": self.warnings,
            "generated_objects": self.generated_objects,
            "masked_sensitive_values": self.masked_sensitive_values,
            "diagnostics_hints": self.diagnostics_hints,
            "router_provisioning": self.router_provisioning,
        }


class VpnRadiusBootstrapPlanner:
    """Generates safe preview script for VPN + RADIUS + API bootstrap contract."""

    def plan(self, *, wizard_run_id: int, payload: dict[str, Any]) -> VpnRadiusBootstrapPlan:
        vpn_tag = f"HOBERADIUS_SETUP:{int(wizard_run_id)}:vpn"
        radius_tag = f"HOBERADIUS_SETUP:{int(wizard_run_id)}:radius"
        api_tag = f"HOBERADIUS_SETUP:{int(wizard_run_id)}:api"
        router_registry_id = str(payload.get("router_registry_id") or "").strip()
        router_tag = f"HOBERADIUS_ROUTER:{router_registry_id}" if router_registry_id else ""
        tag_suffix = f" {router_tag}" if router_tag else ""
        vpn_comment = f"{vpn_tag}{tag_suffix}"
        radius_comment = f"{radius_tag}{tag_suffix}"
        api_comment = f"{api_tag}{tag_suffix}"

        wg_interface = _safe_name(payload.get("wg_interface_name"), fallback="hr-wg")
        peer_name = _safe_name(payload.get("peer_name"), fallback=f"hr-vps-{wizard_run_id}")
        router_vpn_ip = _v4(payload.get("router_vpn_ip"), "router_vpn_ip")
        vps_vpn_ip = _v4(payload.get("vps_vpn_ip"), "vps_vpn_ip")
        allowed_address = _cidr(payload.get("allowed_address") or f"{vps_vpn_ip}/32", "allowed_address")
        listen_port = int(payload.get("wg_listen_port") or 13231)
        endpoint = _v4(payload.get("vps_public_endpoint"), "vps_public_endpoint")
        endpoint_port = int(payload.get("endpoint_port") or 51820)
        server_public_key = _wireguard_public_key(payload.get("server_public_key"))
        radius_server = _v4(payload.get("radius_server_ip") or vps_vpn_ip, "radius_server_ip")
        radius_secret = str(payload.get("radius_secret") or payload.get("radius_secret_ref") or "").strip()
        if not radius_secret:
            raise SetupWizardValidationError("radius_secret is required")
        auth_port = int(payload.get("radius_auth_port") or 1812)
        acct_port = int(payload.get("radius_acct_port") or 1813)
        api_username = _safe_name(payload.get("api_username"), fallback=f"hr_api_{wizard_run_id}")

        lines = [
            "# ================================================",
            "# معاينة ربط HobeRadius والمصادقة",
            "# Preview only - no destructive commands",
            f"# Tags: {vpn_tag}, {radius_tag}, {api_tag}",
            f"# Router registry: {router_tag or 'not-reserved'}",
            "# ================================================",
            "",
            "# --- Cleanup of stale HobeRadius setup from previous wizard runs ---",
            "# The wizard is a fresh-setup tool. If the operator re-runs it on a",
            "# router that already carries HobeRadius config from a previous",
            "# registry id, those leftover rows MUST be removed first — otherwise",
            "# the new run inherits the old WireGuard keys (because RouterOS only",
            "# generates new ones when the interface is created, not reused).",
            "# Comment tags scope the cleanup to HobeRadius-owned rows only.",
            f'/interface wireguard remove [find where name="{wg_interface}" and comment~"HOBERADIUS_SETUP"]',
            f'/ip address remove [find where interface="{wg_interface}" and comment~"HOBERADIUS_SETUP"]',
            f'/ip route remove [find where gateway="{wg_interface}" and comment~"HOBERADIUS_SETUP"]',
            "",
            "# --- WireGuard interface ---",
            "# After cleanup above, the `find` returns empty and we always",
            "# create a fresh interface — which triggers RouterOS's automatic",
            "# private/public key generation. Each wizard run gets a unique",
            "# identity, as intended.",
            f':if ([:len [/interface wireguard find where name="{wg_interface}"]] = 0) do={{',
            f'  /interface wireguard add name="{wg_interface}" listen-port={listen_port} comment="{vpn_comment}"',
            "}",
            f':if ([:len [/ip address find where interface="{wg_interface}" and address="{router_vpn_ip}/24"]] = 0) do={{',
            f'  /ip address add interface="{wg_interface}" address="{router_vpn_ip}/24" comment="{vpn_comment}"',
            "}",
            "",
            "# --- WireGuard peer to VPS ---",
        ]
        if server_public_key:
            lines += [
                f':if ([:len [/interface wireguard peers find where interface="{wg_interface}" and public-key="{server_public_key}"]] = 0) do={{',
                f'  /interface wireguard peers add interface="{wg_interface}" public-key="{server_public_key}" endpoint-address="{endpoint}" endpoint-port={endpoint_port} allowed-address="{allowed_address}" persistent-keepalive=25s comment="{vpn_tag}:{peer_name}{tag_suffix}"',
                f'}} else={{',
                f'  /interface wireguard peers set [find where interface="{wg_interface}" and public-key="{server_public_key}"] endpoint-address="{endpoint}" endpoint-port={endpoint_port} allowed-address="{allowed_address}" persistent-keepalive=25s comment="{vpn_tag}:{peer_name}{tag_suffix}"',
                "}",
                "",
                "# --- Reachability route hints ---",
                f':if ([:len [/ip route find where dst-address="{allowed_address}" and gateway="{wg_interface}"]] = 0) do={{',
                f'  /ip route add dst-address="{allowed_address}" gateway="{wg_interface}" pref-src="{router_vpn_ip}" distance=1 comment="{vpn_comment}"',
                f'}} else={{',
                f'  /ip route set [find where dst-address="{allowed_address}" and gateway="{wg_interface}"] pref-src="{router_vpn_ip}" comment="{vpn_comment}"',
                "}",
                "",
            ]
        else:
            lines += [
                "# لا يمكن إنشاء peer الآن لأن مفتاح WireGuard العام للسيرفر غير مضبوط في HobeRadius.",
                "# اضبط HOBERADIUS_WG_SERVER_PUBKEY في بيئة الخادم ثم أعد توليد السكربت.",
                "# لم يتم توليد أمر إنشاء peer حتى لا يفشل MikroTik برسالة no key set.",
                "",
            ]
        lines += [
            "# --- RADIUS server entry (add-only; no overwrite) ---",
            f':if ([:len [/radius find where address="{radius_server}" and authentication-port={auth_port} and accounting-port={acct_port} and comment="{radius_comment}"]] = 0) do={{',
            f'  /radius add service=hotspot,ppp address="{radius_server}" secret="{radius_secret}" authentication-port={auth_port} accounting-port={acct_port} timeout=300ms comment="{radius_comment}"',
            "}",
            "",
            "# --- API bootstrap contract (plan only, not executed here) ---",
            f'# Intended API username: "{api_username}"',
            f'# Intended API tag: "{api_comment}"',
            "# Manual apply step (outside this planner): create API user with least privileges.",
            "",
            "# ===== Validation checks =====",
            "/interface wireguard print detail",
            "/interface wireguard peers print detail",
            f'/ip address print detail where interface="{wg_interface}"',
            "# Give WireGuard up to 30 seconds to exchange the first handshake before testing the tunnel.",
            ":delay 30s",
            f'/tool ping "{vps_vpn_ip}" src-address="{router_vpn_ip}" count=5',
            f"/radius print detail where comment~\"{radius_tag}\"",
            "/ip service print",
            "/user print detail",
        ]
        script_text = "\n".join(lines).strip() + "\n"
        assert_safe_script(script_text)
        rollback_script = (
            "# Rollback guidance (manual-safe):\n"
            "# - Review objects by comments first, then disable/remove manually if needed.\n"
            f"# - Search tags: {vpn_tag}, {radius_tag}, {api_tag}, {router_tag or 'no-router-registry'}\n"
            "# - Keep a full router backup before any rollback."
        )
        warnings = [
            "هذا المخطط للمعاينة فقط ولا ينفذ تلقائياً على أي راوتر.",
            "سر RADIUS يظهر داخل نص السكربت لنسخه إلى الطرف المطلوب فقط.",
            "إذا كانت الواجهة المختارة هي واجهة الإدارة الحالية، نفّذ من جلسة محلية لتجنب فقد الوصول.",
        ]
        if server_public_key:
            warnings.insert(1, "تم تضمين مفتاح WireGuard العام للسيرفر لإنشاء peer صالح على MikroTik.")
        else:
            warnings.insert(1, "مفتاح WireGuard العام للسيرفر غير مضبوط؛ لن يتم إنشاء peer حتى تضبط HOBERADIUS_WG_SERVER_PUBKEY.")
        return VpnRadiusBootstrapPlan(
            script_text=script_text,
            rollback_script_text=rollback_script,
            validation_commands=[
                "/interface wireguard print detail",
                "/interface wireguard peers print detail",
                f'/ip address print detail where interface="{wg_interface}"',
                ":delay 30s",
                f'/tool ping "{vps_vpn_ip}" src-address="{router_vpn_ip}" count=5',
                "/radius print detail",
                "/ip service print",
            ],
            warnings=warnings,
            generated_objects=[
                {"type": "interface.wireguard", "name": wg_interface, "tag": vpn_tag},
                *(
                    [{"type": "interface.wireguard.peer", "name": peer_name, "tag": vpn_tag}]
                    if server_public_key
                    else []
                ),
                {"type": "radius.server", "name": radius_server, "tag": radius_tag},
                {"type": "api.contract", "name": api_username, "tag": api_tag},
            ],
            masked_sensitive_values={"radius_secret": "***"},
            diagnostics_hints=[
                "vpn_not_handshaking",
                "wrong_public_endpoint",
                "firewall_blocking_udp",
                "wrong_allowed_address",
                "route_missing",
                "radius_secret_mismatch",
                "radius_server_unreachable",
                "api_login_failed",
                "management_interface_conflict",
            ],
            router_provisioning=dict(payload.get("router_provisioning") or {}),
        )
