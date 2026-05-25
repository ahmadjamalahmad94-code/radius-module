"""SW4 hotspot bootstrap planner (preview-only)."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

from .setup_wizard_common import SetupWizardValidationError, assert_safe_script


def _iface(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    if not re.fullmatch(r"[A-Za-z0-9._@:/-]{1,64}", raw):
        raise SetupWizardValidationError(f"{field} contains unsupported characters")
    return raw


def _name(value: Any, fallback: str) -> str:
    raw = str(value or "").strip() or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        raise SetupWizardValidationError("name resolved to empty value")
    return safe[:48]


def _network(value: Any, field: str) -> ipaddress.IPv4Network:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    try:
        net = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{field} must be valid IPv4 CIDR") from exc
    if net.version != 4:
        raise SetupWizardValidationError(f"{field} must be IPv4 CIDR")
    return net


def _ip(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    try:
        ipaddress.IPv4Address(raw)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{field} must be valid IPv4") from exc
    return raw


def _pick_smart_pool(
    *, blocked_networks: list[ipaddress.IPv4Network], fallback_seed: int = 0
) -> tuple[ipaddress.IPv4Network, str]:
    candidates = [
        ipaddress.ip_network(f"10.{x}.0.0/24")
        for x in range(50 + fallback_seed, 220 + fallback_seed)
    ]
    for candidate in candidates:
        if all(not candidate.overlaps(blocked) for blocked in blocked_networks):
            hosts = list(candidate.hosts())
            start = str(hosts[20])
            end = str(hosts[220])
            return candidate, f"{start}-{end}"
    raise SetupWizardValidationError("unable to allocate hotspot subnet without conflict")


@dataclass(frozen=True)
class HotspotPlan:
    mode: str
    script_text: str
    rollback_script_text: str
    validation_commands: list[str]
    warnings: list[str]
    generated_objects: list[dict[str, str]]
    masked_sensitive_values: dict[str, str]
    computed: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "script_text": self.script_text,
            "rollback_script_text": self.rollback_script_text,
            "validation_commands": self.validation_commands,
            "warnings": self.warnings,
            "generated_objects": self.generated_objects,
            "masked_sensitive_values": self.masked_sensitive_values,
            "computed": self.computed,
        }


class HotspotBootstrapPlanner:
    def plan(
        self,
        *,
        wizard_run_id: int,
        mode: str,
        payload: dict[str, Any],
        blocked_interfaces: list[str],
        blocked_network_cidrs: list[str],
    ) -> HotspotPlan:
        normalized = str(mode or "").strip().lower()
        if normalized not in {"manual", "smart"}:
            raise SetupWizardValidationError("hotspot mode must be manual or smart")
        tag = f"HOBERADIUS_SETUP:{int(wizard_run_id)}:hotspot"

        selected_interfaces = payload.get("selected_interfaces") or []
        if isinstance(selected_interfaces, str):
            selected_interfaces = [p.strip() for p in selected_interfaces.split(",") if p.strip()]
        selected_interfaces = [_iface(i, "selected_interface") for i in selected_interfaces]
        if not selected_interfaces:
            raise SetupWizardValidationError("at least one hotspot interface is required")
        blocked_set = {str(x).strip() for x in blocked_interfaces if str(x).strip()}
        for iface in selected_interfaces:
            if iface in blocked_set:
                raise SetupWizardValidationError(
                    f"interface '{iface}' is blocked (WAN/VPN) and cannot be used for hotspot"
                )

        blocked_networks = [
            _network(item, "blocked_network_cidrs")
            for item in blocked_network_cidrs
            if str(item or "").strip()
        ]
        bridge_name = _name(payload.get("bridge_name"), fallback=f"hr-hs-br-{wizard_run_id}")
        profile_name = _name(payload.get("profile_name"), fallback=f"hr-hs-profile-{wizard_run_id}")
        server_name = _name(payload.get("server_name"), fallback=f"hr-hs-{wizard_run_id}")
        pool_name = _name(payload.get("pool_name"), fallback=f"{bridge_name}-pool")
        dhcp_server_name = _name(payload.get("dhcp_server_name"), fallback=f"{bridge_name}-dhcp")
        dns_name = str(payload.get("dns_name") or "hotspot.local").strip()

        if normalized == "manual":
            network = _network(payload.get("network_cidr"), "network_cidr")
            if any(network.overlaps(blocked) for blocked in blocked_networks):
                raise SetupWizardValidationError("manual hotspot network conflicts with WAN/VPN/existing ranges")
            pool_range = str(payload.get("pool_range") or "").strip()
            if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+-\d+\.\d+\.\d+\.\d+", pool_range):
                raise SetupWizardValidationError("pool_range must look like 10.20.30.10-10.20.30.250")
            gateway_ip = _ip(payload.get("gateway_ip") or str(list(network.hosts())[0]), "gateway_ip")
        else:
            network, pool_range = _pick_smart_pool(blocked_networks=blocked_networks, fallback_seed=wizard_run_id % 7)
            gateway_ip = str(list(network.hosts())[0])

        lines = [
            "# ================================================",
            "# HobeRadius Hotspot bootstrap preview",
            f"# Tag: {tag}",
            "# Preview only - no destructive commands",
            "# ================================================",
            "",
            "# --- Bridge and member ports ---",
            f':if ([:len [/interface bridge find where name="{bridge_name}"]] = 0) do={{',
            f'  /interface bridge add name="{bridge_name}" comment="{tag}"',
            "}",
        ]
        for iface in selected_interfaces:
            lines.extend(
                [
                    f':if ([:len [/interface bridge port find where bridge="{bridge_name}" and interface="{iface}"]] = 0) do={{',
                    f'  /interface bridge port add bridge="{bridge_name}" interface="{iface}" comment="{tag}"',
                    "}",
                ]
            )
        lines += [
            "",
            "# --- Addressing and pool ---",
            f':if ([:len [/ip address find where interface="{bridge_name}" and address="{gateway_ip}/{network.prefixlen}"]] = 0) do={{',
            f'  /ip address add interface="{bridge_name}" address="{gateway_ip}/{network.prefixlen}" comment="{tag}"',
            "}",
            f':if ([:len [/ip pool find where name="{pool_name}"]] = 0) do={{',
            f'  /ip pool add name="{pool_name}" ranges="{pool_range}" comment="{tag}"',
            "}",
            "",
            "# --- DHCP for hotspot clients ---",
            f':if ([:len [/ip dhcp-server find where name="{dhcp_server_name}"]] = 0) do={{',
            f'  /ip dhcp-server add name="{dhcp_server_name}" interface="{bridge_name}" address-pool="{pool_name}" disabled=no comment="{tag}"',
            "}",
            f':if ([:len [/ip dhcp-server network find where address="{network}"]] = 0) do={{',
            f'  /ip dhcp-server network add address="{network}" gateway="{gateway_ip}" dns-server="{gateway_ip}" comment="{tag}"',
            "}",
            "",
            "# --- Hotspot profile + server ---",
            "# RouterOS 7 does not accept comment= on these Hotspot add commands; names are the stable identifiers.",
            f':if ([:len [/ip hotspot profile find where name="{profile_name}"]] = 0) do={{',
            f'  /ip hotspot profile add name="{profile_name}" hotspot-address="{gateway_ip}" dns-name="{dns_name}" radius-interim-update=1m',
            "}",
            f':if ([:len [/ip hotspot find where name="{server_name}"]] = 0) do={{',
            f'  /ip hotspot add name="{server_name}" interface="{bridge_name}" address-pool="{pool_name}" profile="{profile_name}"',
            "}",
            "",
            "# --- NAT for hotspot client network only ---",
            f':if ([:len [/ip firewall nat find where chain="srcnat" and src-address="{network}" and action="masquerade"]] = 0) do={{',
            f'  /ip firewall nat add chain=srcnat src-address="{network}" action=masquerade comment="{tag}"',
            "}",
            "",
            "# --- RADIUS hint ---",
            "/ip hotspot profile set [find where name=\"" + profile_name + "\"] use-radius=yes",
            "",
            "# ===== Validation checks =====",
            "/interface bridge print detail where name=\"" + bridge_name + "\"",
            "/ip hotspot print detail where name=\"" + server_name + "\"",
            "/ip pool print detail where name=\"" + pool_name + "\"",
            "/ip dhcp-server print detail where name=\"" + dhcp_server_name + "\"",
            "/ip dhcp-server network print detail where address=\"" + str(network) + "\"",
            "/ip firewall nat print detail where comment~\"" + tag + "\"",
            "/tool ping 8.8.8.8 count=5",
        ]
        script_text = "\n".join(lines).strip() + "\n"
        assert_safe_script(script_text)
        return HotspotPlan(
            mode=normalized,
            script_text=script_text,
            rollback_script_text=(
                "# Rollback guidance:\n"
                f"# - Review objects by tag '{tag}' before manual rollback.\n"
                "# - Remove bridge ports first, then hotspot server/profile/DHCP/pool, then bridge.\n"
                "# - Keep backup before rollback."
            ),
            validation_commands=[
                "/interface bridge print detail",
                "/ip hotspot print detail",
                "/ip pool print detail",
                "/ip dhcp-server print detail",
                "/ip dhcp-server network print detail",
                "/ip firewall nat print detail",
                "/tool ping 8.8.8.8 count=5",
            ],
            warnings=[
                "هذا مخطط معاينة فقط ولا ينفذ تلقائياً.",
                "تم منع واجهات WAN/VPN من الاختيار في هذا المخطط.",
                "تم تقييد NAT على شبكة hotspot فقط.",
            ],
            generated_objects=[
                {"type": "interface.bridge", "name": bridge_name, "tag": tag},
                {"type": "ip.pool", "name": pool_name, "tag": tag},
                {"type": "ip.dhcp-server", "name": dhcp_server_name, "tag": tag},
                {"type": "ip.hotspot.profile", "name": profile_name, "tag": "name-only"},
                {"type": "ip.hotspot.server", "name": server_name, "tag": "name-only"},
            ],
            masked_sensitive_values={},
            computed={
                "network_cidr": str(network),
                "pool_range": pool_range,
                "gateway_ip": gateway_ip,
                "bridge_name": bridge_name,
                "pool_name": pool_name,
                "dhcp_server_name": dhcp_server_name,
                "profile_name": profile_name,
                "server_name": server_name,
                "selected_interfaces": selected_interfaces,
            },
        )
