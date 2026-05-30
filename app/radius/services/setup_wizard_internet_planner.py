"""SW2: Internet uplink script planner (preview-only, no execution)."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any

from .setup_wizard_common import SetupWizardValidationError, assert_safe_script


def _boolish(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _clean_iface(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SetupWizardValidationError(f"{field} is required")
    if not re.fullmatch(r"[A-Za-z0-9._@:/-]{1,64}", text):
        raise SetupWizardValidationError(f"{field} contains unsupported characters")
    return text


def _clean_name(value: Any, field: str, *, fallback: str = "") -> str:
    raw = str(value or "").strip()
    if not raw and fallback:
        raw = fallback
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not safe:
        raise SetupWizardValidationError(f"{field} resolved to empty name")
    return safe[:48]


def _validate_cidr(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    try:
        ipaddress.ip_interface(raw)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{field} must be valid IPv4 CIDR") from exc
    if ":" in raw:
        raise SetupWizardValidationError(f"{field} must be IPv4 CIDR")
    return raw


def _validate_ipv4(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SetupWizardValidationError(f"{field} is required")
    try:
        ipaddress.IPv4Address(raw)
    except ValueError as exc:
        raise SetupWizardValidationError(f"{field} must be valid IPv4") from exc
    return raw


def _validate_dns_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        items = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, list):
        items = [str(p).strip() for p in value if str(p).strip()]
    else:
        raise SetupWizardValidationError("dns_servers must be a list or comma-separated string")
    return [_validate_ipv4(item, "dns_server") for item in items]


@dataclass(frozen=True)
class InternetScriptPlan:
    source_type: str
    input_safe: dict[str, Any]
    script_text: str
    rollback_script_text: str
    validation_commands: list[str]
    warnings: list[str]
    generated_objects: list[dict[str, str]]
    masked_sensitive_values: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "input_safe": self.input_safe,
            "script_text": self.script_text,
            "rollback_script_text": self.rollback_script_text,
            "validation_commands": self.validation_commands,
            "warnings": self.warnings,
            "generated_objects": self.generated_objects,
            "masked_sensitive_values": self.masked_sensitive_values,
        }


class InternetUplinkScriptPlanner:
    def plan(self, *, wizard_run_id: int, source_type: str, payload: dict[str, Any]) -> InternetScriptPlan:
        normalized = str(source_type or "").strip().lower()
        tag = f"HOBERADIUS_SETUP:{int(wizard_run_id)}:internet"
        if normalized == "vlan":
            return self._plan_vlan(tag=tag, payload=payload)
        if normalized == "static":
            return self._plan_static(tag=tag, payload=payload)
        if normalized == "dhcp":
            return self._plan_dhcp(tag=tag, payload=payload)
        if normalized == "pppoe":
            return self._plan_pppoe(tag=tag, payload=payload)
        raise SetupWizardValidationError("internet source type must be vlan/static/dhcp/pppoe")

    def _base_header(self, *, tag: str, title: str) -> list[str]:
        return [
            "# ================================================",
            f"# {title}",
            f"# Tag: {tag}",
            "# Safety: preview-only; this script avoids destructive commands.",
            "# It does not delete or disable existing router configuration.",
            "# ================================================",
            "",
        ]

    def _validation_block(self, *, include_dns_ping: bool, route_target: str | None = None) -> list[str]:
        lines = [
            "",
            "# ===== Validation checks =====",
        ]
        if route_target:
            lines.append(f"/ip route print where gateway={route_target}")
        lines += [
            "# Wait briefly so DHCP/PPPoE/default routes can settle before ping.",
            ":delay 10s",
        ]
        lines.append("/tool ping 8.8.8.8 count=5")
        if include_dns_ping:
            lines.append("/tool ping cloudflare.com count=5")
        return lines

    def _finalize(
        self,
        *,
        source_type: str,
        input_safe: dict[str, Any],
        lines: list[str],
        validation_commands: list[str],
        warnings: list[str],
        generated_objects: list[dict[str, str]],
        masked_sensitive_values: dict[str, str],
    ) -> InternetScriptPlan:
        script_text = "\n".join(lines).strip() + "\n"
        assert_safe_script(script_text)
        rollback_script_text = (
            "# Rollback notes (safe manual mode):\n"
            "# 1) Disable only objects created with this run tag after manual review.\n"
            "# 2) Restore previous default route/DNS from your router backup if needed.\n"
            "# 3) Validate with /tool ping 8.8.8.8 count=5 after rollback."
        )
        return InternetScriptPlan(
            source_type=source_type,
            input_safe=input_safe,
            script_text=script_text,
            rollback_script_text=rollback_script_text,
            validation_commands=validation_commands,
            warnings=warnings,
            generated_objects=generated_objects,
            masked_sensitive_values=masked_sensitive_values,
        )

    def _plan_vlan(self, *, tag: str, payload: dict[str, Any]) -> InternetScriptPlan:
        parent = _clean_iface(payload.get("parent_interface"), "parent_interface")
        vlan_id = int(payload.get("vlan_id") or 0)
        if vlan_id < 1 or vlan_id > 4094:
            raise SetupWizardValidationError("vlan_id must be between 1 and 4094")
        default_vlan_name = f"hr-vlan-{vlan_id}"
        vlan_name = _clean_name(payload.get("vlan_name"), "vlan_name", fallback=default_vlan_name)
        address_mode = str(payload.get("address_mode") or "").strip().lower()
        if address_mode not in {"dhcp", "static"}:
            raise SetupWizardValidationError("address_mode must be dhcp or static")
        nat_enabled = _boolish(payload.get("nat_enabled"), False)
        dns_servers = _validate_dns_list(payload.get("dns_servers"))
        add_default_route = _boolish(payload.get("add_default_route"), True)
        use_peer_dns = _boolish(payload.get("use_peer_dns"), False)
        warnings = [
            "راجع الواجهة الأب بعناية. اختيار واجهة الإدارة الحالية قد يسبب فقدان الوصول.",
            "احفظ نسخة احتياطية من الراوتر قبل تنفيذ أي سكربت uplink.",
        ]
        lines = self._base_header(tag=tag, title="خطة ربط الإنترنت (شبكة افتراضية)")
        lines += [
            "# Create VLAN interface (idempotent by name check)",
            f':if ([:len [/interface vlan find where name="{vlan_name}"]] = 0) do={{',
            f'  /interface vlan add name="{vlan_name}" interface="{parent}" vlan-id={vlan_id} comment="{tag}"',
            "}",
            "",
        ]
        route_target: str | None = None
        if address_mode == "dhcp":
            lines += [
                "# DHCP client on VLAN uplink",
                f':if ([:len [/ip dhcp-client find where interface="{vlan_name}"]] = 0) do={{',
                f'  /ip dhcp-client add interface="{vlan_name}" add-default-route={"yes" if add_default_route else "no"} use-peer-dns={"yes" if use_peer_dns else "no"} disabled=no comment="{tag}"',
                "}",
                "",
            ]
            route_target = "dynamic"
        else:
            address_cidr = _validate_cidr(payload.get("address_cidr"), "address_cidr")
            gateway = _validate_ipv4(payload.get("gateway"), "gateway")
            lines += [
                "# Static address and default route on VLAN uplink",
                f':if ([:len [/ip address find where interface="{vlan_name}" and address="{address_cidr}"]] = 0) do={{',
                f'  /ip address add interface="{vlan_name}" address="{address_cidr}" comment="{tag}"',
                "}",
                f':if ([:len [/ip route find where dst-address="0.0.0.0/0" and gateway="{gateway}" and comment="{tag}"]] = 0) do={{',
                f'  /ip route add dst-address=0.0.0.0/0 gateway="{gateway}" comment="{tag}"',
                "}",
                "",
            ]
            route_target = gateway
        if dns_servers:
            lines += [
                "# DNS configuration",
                f'/ip dns set servers="{",".join(dns_servers)}" allow-remote-requests=yes',
                "",
            ]
        if nat_enabled:
            lines += [
                "# NAT masquerade only for the generated uplink interface",
                f':if ([:len [/ip firewall nat find where chain="srcnat" and out-interface="{vlan_name}" and action="masquerade" and comment="{tag}"]] = 0) do={{',
                f'  /ip firewall nat add chain=srcnat out-interface="{vlan_name}" action=masquerade comment="{tag}"',
                "}",
                "",
            ]
        lines += self._validation_block(include_dns_ping=bool(dns_servers), route_target=route_target)
        validation_commands = [cmd for cmd in lines if cmd.startswith("/")]
        return self._finalize(
            source_type="vlan",
            input_safe={
                "parent_interface": parent,
                "vlan_id": vlan_id,
                "vlan_name": vlan_name,
                "address_mode": address_mode,
                "address_cidr": payload.get("address_cidr") if address_mode == "static" else "",
                "gateway": payload.get("gateway") if address_mode == "static" else "",
                "dns_servers": dns_servers,
                "nat_enabled": nat_enabled,
                "add_default_route": add_default_route,
                "use_peer_dns": use_peer_dns,
            },
            lines=lines,
            validation_commands=validation_commands,
            warnings=warnings,
            generated_objects=[
                {"type": "interface.vlan", "name": vlan_name, "tag": tag},
                {"type": "ip.dhcp-client" if address_mode == "dhcp" else "ip.address-route", "name": vlan_name, "tag": tag},
            ],
            masked_sensitive_values={},
        )

    def _plan_static(self, *, tag: str, payload: dict[str, Any]) -> InternetScriptPlan:
        interface = _clean_iface(payload.get("interface"), "interface")
        address_cidr = _validate_cidr(payload.get("address_cidr"), "address_cidr")
        gateway = _validate_ipv4(payload.get("gateway"), "gateway")
        nat_enabled = _boolish(payload.get("nat_enabled"), False)
        dns_servers = _validate_dns_list(payload.get("dns_servers"))
        lines = self._base_header(tag=tag, title="خطة ربط الإنترنت (عنوان ثابت)")
        lines += [
            "# Static uplink address",
            f':if ([:len [/ip address find where interface="{interface}" and address="{address_cidr}"]] = 0) do={{',
            f'  /ip address add interface="{interface}" address="{address_cidr}" comment="{tag}"',
            "}",
            f':if ([:len [/ip route find where dst-address="0.0.0.0/0" and gateway="{gateway}" and comment="{tag}"]] = 0) do={{',
            f'  /ip route add dst-address=0.0.0.0/0 gateway="{gateway}" comment="{tag}"',
            "}",
            "",
        ]
        if dns_servers:
            lines += [
                "# DNS configuration",
                f'/ip dns set servers="{",".join(dns_servers)}" allow-remote-requests=yes',
                "",
            ]
        if nat_enabled:
            lines += [
                "# NAT masquerade only for selected uplink interface",
                f':if ([:len [/ip firewall nat find where chain="srcnat" and out-interface="{interface}" and action="masquerade" and comment="{tag}"]] = 0) do={{',
                f'  /ip firewall nat add chain=srcnat out-interface="{interface}" action=masquerade comment="{tag}"',
                "}",
                "",
            ]
        lines += self._validation_block(include_dns_ping=bool(dns_servers), route_target=gateway)
        validation_commands = [cmd for cmd in lines if cmd.startswith("/")]
        return self._finalize(
            source_type="static",
            input_safe={
                "interface": interface,
                "address_cidr": address_cidr,
                "gateway": gateway,
                "dns_servers": dns_servers,
                "nat_enabled": nat_enabled,
            },
            lines=lines,
            validation_commands=validation_commands,
            warnings=[
                "تأكد أن gateway يخص مزود الخدمة على نفس وصلة الإنترنت.",
                "تأكد أن هذه الواجهة ليست LAN داخلية.",
            ],
            generated_objects=[{"type": "ip.address-route", "name": interface, "tag": tag}],
            masked_sensitive_values={},
        )

    def _plan_dhcp(self, *, tag: str, payload: dict[str, Any]) -> InternetScriptPlan:
        interface = _clean_iface(payload.get("interface"), "interface")
        add_default_route = _boolish(payload.get("add_default_route"), True)
        use_peer_dns = _boolish(payload.get("use_peer_dns"), True)
        nat_enabled = _boolish(payload.get("nat_enabled"), False)
        lines = self._base_header(tag=tag, title="خطة ربط الإنترنت (عميل توزيع عناوين مباشر)")
        lines += [
            "# DHCP client directly on selected interface",
            f':if ([:len [/ip dhcp-client find where interface="{interface}"]] = 0) do={{',
            f'  /ip dhcp-client add interface="{interface}" add-default-route={"yes" if add_default_route else "no"} use-peer-dns={"yes" if use_peer_dns else "no"} disabled=no comment="{tag}"',
            "}",
            "",
        ]
        if nat_enabled:
            lines += [
                "# NAT masquerade only for selected uplink interface",
                f':if ([:len [/ip firewall nat find where chain="srcnat" and out-interface="{interface}" and action="masquerade" and comment="{tag}"]] = 0) do={{',
                f'  /ip firewall nat add chain=srcnat out-interface="{interface}" action=masquerade comment="{tag}"',
                "}",
                "",
            ]
        lines += self._validation_block(include_dns_ping=use_peer_dns, route_target="dynamic")
        validation_commands = [cmd for cmd in lines if cmd.startswith("/")]
        return self._finalize(
            source_type="dhcp",
            input_safe={
                "interface": interface,
                "add_default_route": add_default_route,
                "use_peer_dns": use_peer_dns,
                "nat_enabled": nat_enabled,
            },
            lines=lines,
            validation_commands=validation_commands,
            warnings=[
                "إذا كانت الشبكة تعطي DNS غير موثوق، عطّل use_peer_dns وحدد DNS يدويًا لاحقًا.",
            ],
            generated_objects=[{"type": "ip.dhcp-client", "name": interface, "tag": tag}],
            masked_sensitive_values={},
        )

    def _plan_pppoe(self, *, tag: str, payload: dict[str, Any]) -> InternetScriptPlan:
        interface = _clean_iface(payload.get("interface"), "interface")
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        if not username:
            raise SetupWizardValidationError("username is required")
        if not password:
            raise SetupWizardValidationError("password is required")
        service_name = str(payload.get("service_name") or "").strip()
        add_default_route = _boolish(payload.get("add_default_route"), True)
        use_peer_dns = _boolish(payload.get("use_peer_dns"), True)
        nat_enabled = _boolish(payload.get("nat_enabled"), False)
        fixed_ip = str(payload.get("fixed_ip") or "").strip()
        if fixed_ip:
            _validate_ipv4(fixed_ip, "fixed_ip")
        ppp_name = _clean_name(payload.get("pppoe_client_name"), "pppoe_client_name", fallback=f"hr-pppoe-{interface}")
        lines = self._base_header(tag=tag, title="خطة ربط الإنترنت (برودباند)")
        lines += [
            "# PPPoE client on selected interface",
            f':if ([:len [/interface pppoe-client find where name="{ppp_name}"]] = 0) do={{',
            f'  /interface pppoe-client add name="{ppp_name}" interface="{interface}" user="{username}" password="{password}" add-default-route={"yes" if add_default_route else "no"} use-peer-dns={"yes" if use_peer_dns else "no"} disabled=no comment="{tag}"',
            "}",
            "",
        ]
        if service_name:
            lines += [
                "# Optional service name pinning (explicit target by exact name)",
                f'/interface pppoe-client set "{ppp_name}" service-name="{service_name}"',
                "",
            ]
        if fixed_ip:
            lines += [
                "# Optional fixed local address hint for PPPoE client",
                f'/interface pppoe-client set "{ppp_name}" max-mtu=1480 max-mru=1480',
                f'# Requested fixed_ip (provider-side policy expected): {fixed_ip}',
                "",
            ]
        if nat_enabled:
            lines += [
                "# NAT masquerade only for generated PPPoE uplink interface",
                f':if ([:len [/ip firewall nat find where chain="srcnat" and out-interface="{ppp_name}" and action="masquerade" and comment="{tag}"]] = 0) do={{',
                f'  /ip firewall nat add chain=srcnat out-interface="{ppp_name}" action=masquerade comment="{tag}"',
                "}",
                "",
            ]
        lines += self._validation_block(include_dns_ping=use_peer_dns, route_target="dynamic")
        validation_commands = [cmd for cmd in lines if cmd.startswith("/")]
        return self._finalize(
            source_type="pppoe",
            input_safe={
                "interface": interface,
                "username": username,
                "service_name": service_name,
                "add_default_route": add_default_route,
                "use_peer_dns": use_peer_dns,
                "fixed_ip": fixed_ip,
                "nat_enabled": nat_enabled,
                "pppoe_client_name": ppp_name,
            },
            lines=lines,
            validation_commands=validation_commands,
            warnings=[
                "كلمة مرور PPPoE تظهر داخل نص السكربت فقط لغرض النسخ إلى MikroTik Terminal.",
                "لا يتم تخزين كلمة مرور PPPoE كنص صريح في metadata الخاصة بالمخطط.",
            ],
            generated_objects=[{"type": "interface.pppoe-client", "name": ppp_name, "tag": tag}],
            masked_sensitive_values={"password": "***"},
        )
