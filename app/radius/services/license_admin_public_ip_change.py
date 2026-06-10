"""V40 adapter for network.public_ip_change.

Dry-run plans the tagged src-nat command without touching the router. Live
apply (dry_run=False) is a real, gated operation: when the owner enables the
HOBERADIUS_PUBLIC_IP_CHANGE_LIVE_APPLY_ENABLED flag it opens a MikroTik session
and adds the tagged src-nat rule (add-only). With the flag off it returns a
clear «بانتظار تفعيلك» envelope — never a silent no-op.
"""
from __future__ import annotations

import ipaddress
import os
from typing import Any

ACTION_KEY = "network.public_ip_change"
LIVE_APPLY_CODE = "public_ip_change_live_apply_not_enabled"
LIVE_APPLY_FLAG = "HOBERADIUS_PUBLIC_IP_CHANGE_LIVE_APPLY_ENABLED"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


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
            if not _truthy(os.environ.get(LIVE_APPLY_FLAG)):
                return {
                    "status": "failed",
                    "supported": True,
                    "dry_run": False,
                    "error": {
                        "code": LIVE_APPLY_CODE,
                        "message": (
                            "بانتظار تفعيلك: التطبيق الفعلي لتغيير عنوان الإنترنت "
                            "العام مقفل. فعّل العلم "
                            "HOBERADIUS_PUBLIC_IP_CHANGE_LIVE_APPLY_ENABLED من "
                            "إعدادات النظام لتمكين التطبيق المباشر على الراوتر."),
                    },
                    "warnings": validation["warnings"],
                }
            return self._apply_live(
                job=job,
                normalized=validation["normalized"],
                warnings=validation["warnings"],
            )

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


    def _apply_live(
        self, *, job: dict[str, Any], normalized: dict[str, str],
        warnings: list[str],
    ) -> dict[str, Any]:
        """Open a real MikroTik session and add the tagged src-nat rule."""
        from app.radius.db.connection import db
        from app.radius.services import mikrotik_admin_client as mac

        tenant_id = int(job.get("tenant_id") or 1)
        try:
            router_id = int(normalized["router_id"])
        except (TypeError, ValueError):
            return {
                "status": "failed", "supported": True, "dry_run": False,
                "error": {"code": "invalid_router_id",
                          "message": "معرّف الراوتر غير صالح."},
                "warnings": warnings,
            }
        row = db().execute(
            "SELECT * FROM nas_devices WHERE id = ? AND tenant_id = ? "
            "AND (deleted_at IS NULL OR deleted_at = '')",
            (router_id, tenant_id),
        ).fetchone()
        if not row:
            return {
                "status": "failed", "supported": True, "dry_run": False,
                "error": {"code": "router_not_found",
                          "message": "الراوتر غير موجود لهذا المستأجر."},
                "warnings": warnings,
            }
        tag = f"HOBERADIUS_ADMIN_BRIDGE:public-ip-change:{job.get('reference') or 'pending'}"
        result = mac.firewall_nat_add(
            dict(row),
            chain="srcnat",
            action="src-nat",
            to_addresses=normalized["requested_public_ip"],
            out_interface=normalized.get("wan_interface", ""),
            comment=tag,
        )
        if result.ok:
            return {
                "status": "completed", "supported": True, "dry_run": False,
                "target": {
                    "router_id": normalized["router_id"],
                    "router_label": normalized["router_label"],
                },
                "applied": {
                    "action_key": ACTION_KEY,
                    "requested_public_ip": normalized["requested_public_ip"],
                    "wan_interface": normalized["wan_interface"],
                    "comment_tag": tag,
                },
                "warnings": warnings,
            }
        return {
            "status": "failed", "supported": True, "dry_run": False,
            "error": {"code": "live_apply_failed",
                      "message": result.error or "تعذّر تطبيق التغيير على الراوتر."},
            "warnings": warnings,
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
            errors["requested_public_ip"] = "عنوان الإنترنت المطلوب يجب أن يكون عنوانًا صحيحًا"

    router_type = str(payload.get("router_type") or "mikrotik").strip().lower()
    if router_type not in {"mikrotik", "routeros"}:
        errors["router_type"] = "only MikroTik/RouterOS targets are supported for planning"

    wan_interface = str(payload.get("wan_interface") or payload.get("egress_interface") or "").strip()
    if not wan_interface:
        warnings.append("wan_interface missing; future apply must resolve a scoped interface first")

    method = str(payload.get("method") or "srcnat_to_addresses").strip().lower()
    if method not in {"srcnat_to_addresses", "site_exit_nat"}:
        errors["method"] = "طريقة تغيير عنوان الإنترنت غير مدعومة"

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
