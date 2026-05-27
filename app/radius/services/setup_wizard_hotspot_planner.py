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


def _base_octets(value: Any) -> tuple[int, int]:
    raw = str(value or "10.20.0.0/16").strip()
    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise SetupWizardValidationError("hotspot subnet base must be valid IPv4 CIDR") from exc
    if network.version != 4 or network.prefixlen > 16:
        raise SetupWizardValidationError("hotspot subnet base must be an IPv4 /16 or wider range")
    octets = str(network.network_address).split(".")
    return int(octets[0]), int(octets[1])


def _interface_octet(iface: str, *, fallback_seed: int, used: set[int]) -> int:
    match = re.search(r"(\d+)$", iface)
    if match:
        candidate = int(match.group(1))
        if 2 <= candidate <= 254 and candidate not in used:
            return candidate
    candidate = 20 + (fallback_seed % 180)
    while candidate in used or candidate < 2 or candidate > 254:
        candidate += 1
        if candidate > 254:
            candidate = 2
    return candidate


def _network_for_interface(
    iface: str,
    *,
    base_a: int,
    base_b: int,
    fallback_seed: int,
    used_octets: set[int],
    blocked_networks: list[ipaddress.IPv4Network],
) -> tuple[ipaddress.IPv4Network, int]:
    octet = _interface_octet(iface, fallback_seed=fallback_seed, used=used_octets)
    searched = 0
    while searched < 253:
        network = ipaddress.ip_network(f"{base_a}.{base_b}.{octet}.0/24")
        if all(not network.overlaps(blocked) for blocked in blocked_networks):
            used_octets.add(octet)
            return network, octet
        octet += 1
        if octet > 254:
            octet = 2
        searched += 1
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
        base_a, base_b = _base_octets(payload.get("subnet_base") or payload.get("hotspot_subnet_base"))
        dns_name = _name(payload.get("dns_name"), fallback="login.hoberadius.local")
        radius_server_ip = _ip(payload.get("radius_server_ip") or "10.10.0.1", "radius_server_ip")
        router_vpn_ip = _ip(payload.get("router_vpn_ip") or payload.get("src_address"), "router_vpn_ip")
        radius_secret = str(payload.get("radius_secret") or payload.get("radius_secret_ref") or "").strip()
        if not radius_secret:
            raise SetupWizardValidationError("radius_secret is required")
        # The WAN interface name lets the script bootstrap a
        # `WAN` interface-list with that member so the NAT
        # masquerade rule actually matches. Fresh routers don't
        # have an empty WAN list by default — without this
        # bootstrap the rule never matches and hotspot users
        # get no internet.
        wan_interface = str(payload.get("wan_interface") or "").strip()

        used_octets: set[int] = set()
        port_plans: list[dict[str, Any]] = []
        for index, iface in enumerate(selected_interfaces):
            network, octet = _network_for_interface(
                iface,
                base_a=base_a,
                base_b=base_b,
                fallback_seed=int(wizard_run_id) + index,
                used_octets=used_octets,
                blocked_networks=blocked_networks,
            )
            prefix = ".".join(str(network.network_address).split(".")[:3])
            port_plans.append(
                {
                    "interface": iface,
                    "comment": f"HOBE_HOTSPOT_{iface}",
                    "network_cidr": str(network),
                    "gateway_ip": f"{prefix}.1",
                    "pool_range": f"{prefix}.10-{prefix}.254",
                    "pool_name": f"pool-hotspot-{iface}",
                    "dhcp_server_name": f"dhcp-hotspot-{iface}",
                    "profile_name": f"hsprof-{iface}",
                    "server_name": f"hotspot-{iface}",
                    "octet": octet,
                }
            )

        lines: list[str] = []
        # ── Header + cleanup warning ─────────────────────────
        # The first thing we tell the operator: this script
        # WILL remove any prior HobeRadius hotspot config on
        # the selected interfaces. Without this prepend, a
        # second paste would create duplicate hotspot servers
        # / DHCP servers / NAT rules / RADIUS rows.
        lines.extend(
            [
                "# ════════════════════════════════════════════════",
                "# HobeRadius Hotspot Setup",
                f"# Run: {wizard_run_id}",
                "# ⚠️  WARNING: this script REMOVES every prior",
                "#     /ip hotspot and /ip dhcp-server bound to",
                "#     the target interfaces before adding fresh",
                "#     state. NAT / pool / address rows are kept",
                "#     scoped to HOBE_HOTSPOT_<interface>, but the",
                "#     hotspot + dhcp server entries are removed",
                "#     by interface — otherwise MikroTik refuses",
                "#     the new add with «server or relay with",
                "#     such interface already exists».",
                "# ════════════════════════════════════════════════",
                "",
                "# ── Cleanup of prior HobeRadius hotspot rows ──",
            ]
        )
        for plan in port_plans:
            comment = plan["comment"]
            iface = plan["interface"]
            lines.extend(
                [
                    f'/ip firewall nat remove [find where comment~"{comment}"]',
                    # Remove by NAME first (HobeRadius-tagged rows).
                    f'/ip hotspot remove [find where name="{plan["server_name"]}"]',
                    f'/ip hotspot profile remove [find where name="{plan["profile_name"]}"]',
                    f'/ip dhcp-server remove [find where name="{plan["dhcp_server_name"]}"]',
                    # Then sweep anything still bound to the same INTERFACE
                    # — a hotspot/dhcp from a prior tool, an older HobeRadius
                    # naming scheme, or a half-cleaned manual setup will
                    # otherwise make /ip hotspot add fail with
                    # «server or relay with such interface already exists».
                    f'/ip hotspot remove [find where interface="{iface}"]',
                    f'/ip dhcp-server remove [find where interface="{iface}"]',
                    f'/ip dhcp-server network remove [find where comment~"{comment}"]',
                    f'/ip pool remove [find where name="{plan["pool_name"]}"]',
                    f'/ip address remove [find where comment~"{comment}"]',
                ]
            )
        lines.append("")
        # Bootstrap the interface-list named "WAN" so the NAT
        # rule below has a valid match. Idempotent: skips if
        # the list or the member already exists. This block is
        # only emitted when the operator told us the WAN
        # interface name (Hotspot card in v3 wizard or its
        # equivalent in v2).
        if wan_interface:
            lines.extend(
                [
                    "# Bootstrap interface-list WAN (idempotent)",
                    ':if ([:len [/interface list find where name="WAN"]] = 0) do={'
                    ' /interface list add name=WAN }',
                    f':if ([:len [/interface list member find where '
                    f'list=WAN interface="{wan_interface}"]] = 0) do={{'
                    f' /interface list member add list=WAN '
                    f'interface="{wan_interface}" }}',
                    "",
                ]
            )
        for plan in port_plans:
            iface = plan["interface"]
            comment = plan["comment"]
            gateway_ip = plan["gateway_ip"]
            network_cidr = plan["network_cidr"]
            lines.extend(
                [
                    f'/ip address add address={gateway_ip}/24 interface={iface} comment="{comment}"',
                    "",
                    f"/ip pool add name={plan['pool_name']} ranges={plan['pool_range']}",
                    "",
                    f'/ip dhcp-server add name={plan["dhcp_server_name"]} interface={iface} address-pool={plan["pool_name"]} disabled=no comment="{comment}"',
                    "",
                    f'/ip dhcp-server network add address={network_cidr} gateway={gateway_ip} dns-server={gateway_ip} comment="{comment}"',
                    "",
                ]
            )

        # NOTE: RADIUS server entry is intentionally NOT added
        # here. The Step 3 unified wizard script already added
        # one RADIUS row covering all services (hotspot, ppp,
        # login) with the run's pre-allocated secret. Adding
        # another row from this script would duplicate the
        # entry and confuse operators.
        lines.extend(
            [
                "/ip dns set allow-remote-requests=yes",
                "",
            ]
        )

        for plan in port_plans:
            iface = plan["interface"]
            gateway_ip = plan["gateway_ip"]
            network_cidr = plan["network_cidr"]
            comment = plan["comment"]
            lines.extend(
                [
                    f"/ip hotspot profile add name={plan['profile_name']} hotspot-address={gateway_ip} dns-name={dns_name} use-radius=yes radius-accounting=yes radius-interim-update=00:00:30 login-by=http-pap,cookie,mac-cookie",
                    "",
                    f"/ip hotspot add name={plan['server_name']} interface={iface} address-pool={plan['pool_name']} profile={plan['profile_name']} disabled=no",
                    "",
                    f"/ip hotspot set [find where name={plan['server_name']}] addresses-per-mac=1",
                    "",
                    f'/ip firewall nat add chain=srcnat src-address={network_cidr} out-interface-list=WAN action=masquerade comment="{comment} NAT"',
                    "",
                ]
            )

        script_text = "\n".join(lines).strip() + "\n"
        assert_safe_script(script_text)

        validation_commands: list[str] = []
        for plan in port_plans:
            validation_commands.extend(
                [
                    f"/ip address print detail where interface={plan['interface']}",
                    f"/ip dhcp-server print detail where name={plan['dhcp_server_name']}",
                    f"/ip hotspot print detail where name={plan['server_name']}",
                    f'/ip firewall nat print detail where comment~"{plan["comment"]}"',
                ]
            )
        validation_commands.append("/tool ping 8.8.8.8 count=5")

        generated_objects: list[dict[str, str]] = []
        for plan in port_plans:
            generated_objects.extend(
                [
                    {"type": "ip.address", "name": f"{plan['gateway_ip']}/24", "tag": plan["comment"]},
                    {"type": "ip.pool", "name": plan["pool_name"], "tag": plan["comment"]},
                    {"type": "ip.dhcp-server", "name": plan["dhcp_server_name"], "tag": plan["comment"]},
                    {"type": "ip.hotspot.profile", "name": plan["profile_name"], "tag": "name-only"},
                    {"type": "ip.hotspot.server", "name": plan["server_name"], "tag": "name-only"},
                    {"type": "ip.firewall.nat", "name": plan["network_cidr"], "tag": f"{plan['comment']} NAT"},
                ]
            )

        return HotspotPlan(
            mode=normalized,
            script_text=script_text,
            rollback_script_text=(
                "# Rollback guidance:\n"
                "# - Review HOBE_HOTSPOT_<interface> objects before manual rollback.\n"
                "# - Remove hotspot server/profile/DHCP/pool/address/NAT for the selected interface only.\n"
                "# - Keep backup before rollback."
            ),
            validation_commands=validation_commands,
            warnings=[
                "Preview only. Copy to MikroTik manually.",
                "WAN/VPN interfaces remain blocked from selection.",
                "Each selected port receives its own Hotspot /24 network.",
            ],
            generated_objects=generated_objects,
            masked_sensitive_values={"radius_secret": "***"},
            computed={
                "subnet_base": f"{base_a}.{base_b}.0.0/16",
                "radius_server_ip": radius_server_ip,
                "router_vpn_ip": router_vpn_ip,
                "selected_interfaces": selected_interfaces,
                "port_plans": port_plans,
            },
        )
