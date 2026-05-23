"""SW5 broadband/PPPoE bootstrap planner (preview-only)."""
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


def _safe_name(value: Any, fallback: str) -> str:
    raw = str(value or "").strip() or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        raise SetupWizardValidationError("name resolved to empty value")
    return safe[:48]


def _ip(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    try:
        ipaddress.IPv4Address(raw)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{field} must be valid IPv4") from exc
    return raw


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


def _choose_smart_subnet(blocked: list[ipaddress.IPv4Network]) -> ipaddress.IPv4Network:
    for octet in range(120, 230):
        candidate = ipaddress.ip_network(f"10.{octet}.0.0/24")
        if all(not candidate.overlaps(other) for other in blocked):
            return candidate
    raise SetupWizardValidationError("unable to allocate broadband pool without conflict")


@dataclass(frozen=True)
class BroadbandPlan:
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


class BroadbandBootstrapPlanner:
    def plan(
        self,
        *,
        wizard_run_id: int,
        mode: str,
        payload: dict[str, Any],
        blocked_interfaces: list[str],
        blocked_network_cidrs: list[str],
    ) -> BroadbandPlan:
        normalized = str(mode or "").strip().lower()
        if normalized not in {"manual", "smart"}:
            raise SetupWizardValidationError("broadband mode must be manual or smart")
        tag = f"HOBERADIUS_SETUP:{int(wizard_run_id)}:broadband"

        selected_interfaces = payload.get("selected_interfaces") or []
        if isinstance(selected_interfaces, str):
            selected_interfaces = [p.strip() for p in selected_interfaces.split(",") if p.strip()]
        selected_interfaces = [_iface(i, "selected_interface") for i in selected_interfaces]
        if not selected_interfaces:
            raise SetupWizardValidationError("at least one interface is required for broadband")
        blocked_set = {str(x).strip() for x in blocked_interfaces if str(x).strip()}
        for iface in selected_interfaces:
            if iface in blocked_set:
                raise SetupWizardValidationError(
                    f"interface '{iface}' is blocked (WAN/VPN) and cannot be used for broadband"
                )

        blocked_networks = [
            _network(item, "blocked_network_cidrs")
            for item in blocked_network_cidrs
            if str(item or "").strip()
        ]
        service_name = _safe_name(payload.get("service_name"), f"hr-pppoe-srv-{wizard_run_id}")
        profile_name = _safe_name(payload.get("profile_name"), f"hr-ppp-profile-{wizard_run_id}")
        dns_servers = str(payload.get("dns_servers") or "1.1.1.1,8.8.8.8").strip()

        if normalized == "manual":
            local_address = _ip(payload.get("local_address"), "local_address")
            remote_pool_cidr = _network(payload.get("remote_pool_cidr"), "remote_pool_cidr")
            if any(remote_pool_cidr.overlaps(other) for other in blocked_networks):
                raise SetupWizardValidationError("remote_pool_cidr conflicts with WAN/VPN/hotspot ranges")
        else:
            remote_pool_cidr = _choose_smart_subnet(blocked_networks)
            local_address = str(list(remote_pool_cidr.hosts())[0])

        hosts = list(remote_pool_cidr.hosts())
        pool_start = str(hosts[10])
        pool_end = str(hosts[-10])
        pool_name = _safe_name(payload.get("pool_name"), f"{service_name}-pool")
        lines = [
            "# ================================================",
            "# HobeRadius Broadband/PPPoE bootstrap preview",
            f"# Tag: {tag}",
            "# Preview only - no destructive commands",
            "# ================================================",
            "",
            "# --- PPP profile ---",
            f':if ([:len [/ppp profile find where name="{profile_name}"]] = 0) do={{',
            f'  /ppp profile add name="{profile_name}" local-address="{local_address}" dns-server="{dns_servers}" use-radius=yes comment="{tag}"',
            "}",
            "",
            "# --- IP pool for remote clients ---",
            f':if ([:len [/ip pool find where name="{pool_name}"]] = 0) do={{',
            f'  /ip pool add name="{pool_name}" ranges="{pool_start}-{pool_end}" comment="{tag}"',
            "}",
            "",
            "# --- Link pool to PPP profile ---",
            f'/ppp profile set "{profile_name}" remote-address="{pool_name}"',
            "",
        ]
        for iface in selected_interfaces:
            lines.extend(
                [
                    f"# --- PPPoE server on {iface} ---",
                    f':if ([:len [/interface pppoe-server server find where interface="{iface}"]] = 0) do={{',
                    f'  /interface pppoe-server server add interface="{iface}" service-name="{service_name}" default-profile="{profile_name}" one-session-per-host=yes disabled=no comment="{tag}"',
                    "}",
                    "",
                ]
            )
        lines += [
            "# --- NAT scoped to broadband remote pool ---",
            f':if ([:len [/ip firewall nat find where chain="srcnat" and src-address="{remote_pool_cidr}" and action="masquerade" and comment="{tag}"]] = 0) do={{',
            f'  /ip firewall nat add chain=srcnat src-address="{remote_pool_cidr}" action=masquerade comment="{tag}"',
            "}",
            "",
            "# ===== Validation checks =====",
            "/ppp profile print detail where name=\"" + profile_name + "\"",
            "/ip pool print detail where name=\"" + pool_name + "\"",
            "/interface pppoe-server server print detail",
            "/ip firewall nat print detail where comment~\"" + tag + "\"",
            "/tool ping 8.8.8.8 count=5",
        ]
        script_text = "\n".join(lines).strip() + "\n"
        assert_safe_script(script_text)

        return BroadbandPlan(
            mode=normalized,
            script_text=script_text,
            rollback_script_text=(
                "# Rollback guidance:\n"
                f"# - Review objects by tag '{tag}' before any manual rollback.\n"
                "# - Remove PPPoE server entries first, then pool/profile, then NAT."
            ),
            validation_commands=[
                "/ppp profile print detail",
                "/ip pool print detail",
                "/interface pppoe-server server print detail",
                "/ip firewall nat print detail",
                "/tool ping 8.8.8.8 count=5",
            ],
            warnings=[
                "المخطط معاينة فقط ولا ينفذ تلقائياً.",
                "تم استبعاد واجهات WAN/VPN من هذا المخطط.",
                "تم تقييد NAT على نطاق broadband pool فقط.",
            ],
            generated_objects=[
                {"type": "ppp.profile", "name": profile_name, "tag": tag},
                {"type": "ip.pool", "name": pool_name, "tag": tag},
                {"type": "pppoe.server", "name": service_name, "tag": tag},
            ],
            masked_sensitive_values={},
            computed={
                "remote_pool_cidr": str(remote_pool_cidr),
                "pool_range": f"{pool_start}-{pool_end}",
                "local_address": local_address,
                "service_name": service_name,
                "profile_name": profile_name,
                "pool_name": pool_name,
                "selected_interfaces": selected_interfaces,
            },
        )
