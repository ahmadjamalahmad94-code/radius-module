"""Dry-run V40 adapter for network.public_ip_change.

This adapter deliberately plans only. It does not open a MikroTik session,
does not call an executor, and does not mutate router state.
"""
from __future__ import annotations

import ipaddress
from typing import Any

ACTION_KEY = "network.public_ip_change"
LIVE_APPLY_CODE = "public_ip_change_live_apply_not_enabled"


class PublicIpChangeDryRunAdapter:
    action_key = ACTION_KEY
    dry_run_supported = True

    def __init__(self, *, service_key: str = "network") -> None:
        self.service_key = service_key

    def execute(self, *, job: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        validation = validate_public_ip_change_payload(payload)
        if validation["errors"]:
            return {
                "status": "failed",
                "supported": True,
                "dry_run": bool(dry_run),
                "error": {
                    "code": "invalid_public_ip_change_payload",
                    "fields": validation["errors"],
                },
                "warnings": validation["warnings"],
            }
        if not dry_run:
            return {
                "status": "failed",
                "supported": True,
                "dry_run": False,
                "error": {
                    "code": LIVE_APPLY_CODE,
                    "message": "Public IP change live apply is intentionally disabled in P10.",
                },
                "warnings": validation["warnings"],
            }

        normalized = validation["normalized"]
        tag = f"HOBERADIUS_ADMIN_BRIDGE:public-ip-change:{job.get('reference') or 'pending'}"
        commands = _planned_commands(normalized=normalized, tag=tag)
        return {
            "status": "dry_run_completed",
            "supported": True,
            "dry_run": True,
            "target": {
                "router_id": normalized["router_id"],
                "router_label": normalized["router_label"],
                "router_type": normalized["router_type"],
            },
            "operation": {
                "action_key": ACTION_KEY,
                "method": normalized["method"],
                "requested_public_ip": normalized["requested_public_ip"],
                "wan_interface": normalized["wan_interface"],
            },
            "planned_commands": commands,
            "rollback_expectations": [
                "Use the previous tagged NAT/route version if it exists.",
                "Do not remove unrelated NAT, route, or mangle rules.",
                f"Only inspect objects tagged {tag}.",
            ],
            "risk_notes": validation["warnings"]
            + [
                "Dry-run only. No MikroTik connection was opened.",
                "A fresh router backup/export is required before any future live apply.",
            ],
        }


def validate_public_ip_change_payload(payload: dict[str, Any]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    warnings: list[str] = []
    router_id = str(
        payload.get("router_id")
        or payload.get("nas_id")
        or payload.get("target_router_id")
        or payload.get("target_router")
        or ""
    ).strip()
    if not router_id:
        errors["router_id"] = "router_id or nas_id is required"

    requested_public_ip = str(
        payload.get("requested_public_ip")
        or payload.get("new_public_ip")
        or payload.get("public_ip")
        or ""
    ).strip()
    if not requested_public_ip:
        errors["requested_public_ip"] = "requested_public_ip is required"
    else:
        try:
            parsed_ip = ipaddress.ip_address(requested_public_ip)
            if parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_multicast:
                warnings.append("requested_public_ip is not globally routable")
        except ValueError:
            errors["requested_public_ip"] = "requested_public_ip must be a valid IP address"

    router_type = str(payload.get("router_type") or "mikrotik").strip().lower()
    if router_type not in {"mikrotik", "routeros"}:
        errors["router_type"] = "only MikroTik/RouterOS targets are supported for planning"

    wan_interface = str(payload.get("wan_interface") or payload.get("egress_interface") or "").strip()
    if not wan_interface:
        warnings.append("wan_interface missing; future apply must resolve a scoped interface first")

    method = str(payload.get("method") or "srcnat_to_addresses").strip().lower()
    if method not in {"srcnat_to_addresses", "site_exit_nat"}:
        errors["method"] = "unsupported public IP change method"

    normalized = {
        "router_id": router_id,
        "router_label": str(payload.get("router_label") or payload.get("nas_name") or router_id),
        "router_type": router_type,
        "requested_public_ip": requested_public_ip,
        "wan_interface": wan_interface,
        "method": method,
    }
    return {"errors": errors, "warnings": warnings, "normalized": normalized}


def _planned_commands(*, normalized: dict[str, str], tag: str) -> list[dict[str, Any]]:
    interface_clause = (
        f'out-interface="{normalized["wan_interface"]}" '
        if normalized.get("wan_interface")
        else ""
    )
    return [
        {
            "type": "routeros_preview",
            "command": (
                "/ip firewall nat add chain=srcnat "
                f"{interface_clause}"
                f'action=src-nat to-addresses="{normalized["requested_public_ip"]}" '
                f'comment="{tag}"'
            ),
            "safety": "add-only-preview-tagged",
        },
        {
            "type": "verification_preview",
            "command": f'/ip firewall nat print detail where comment="{tag}"',
            "safety": "read-only-preview",
        },
    ]
