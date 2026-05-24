"""Recovery, support bundle and pilot health helpers for setup wizard."""
from __future__ import annotations

import json
import re
from typing import Any

from .setup_wizard_inventory import RouterInventoryService, sanitize_inventory
from .setup_wizard_operations import SetupWizardOperationRepo, live_apply_enabled


def mask_secrets(value: Any) -> Any:
    value = sanitize_inventory(value)
    if isinstance(value, str):
        value = re.sub(r'(secret=)"[^"]*"', r'\1"***"', value, flags=re.I)
        value = re.sub(r'(password=)"[^"]*"', r'\1"***"', value, flags=re.I)
        value = re.sub(r'("radius_secret"\s*:\s*)"[^"]*"', r'\1"***"', value, flags=re.I)
        return value
    if isinstance(value, dict):
        return {k: ("***" if "secret" in str(k).lower() or "password" in str(k).lower() else mask_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    return value


class SetupWizardSupportService:
    def __init__(
        self,
        *,
        wizard_service: Any,
        operation_repo: SetupWizardOperationRepo | None = None,
        inventory_service: RouterInventoryService | None = None,
    ) -> None:
        self.wizard_service = wizard_service
        self.operation_repo = operation_repo or SetupWizardOperationRepo()
        self.inventory_service = inventory_service or RouterInventoryService()

    def support_bundle(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        summary = self.wizard_service.get_run_summary(tenant_id=tenant_id, run_id=run_id)
        operations = self.operation_repo.list_for_run(tenant_id=tenant_id, run_id=run_id)
        snapshot = self.inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)
        return mask_secrets({
            "run": summary.get("run"),
            "steps": summary.get("steps"),
            "verification": summary.get("verification"),
            "operations": operations,
            "router_snapshot_summary": _snapshot_summary(snapshot),
            "live_apply_enabled": live_apply_enabled(),
        })

    def health(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        summary = self.wizard_service.get_run_summary(tenant_id=tenant_id, run_id=run_id)
        operations = self.operation_repo.list_for_run(tenant_id=tenant_id, run_id=run_id)
        snapshot = self.inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)
        failed_steps = [s for s in summary.get("steps", []) if s.get("status") == "failed"]
        diagnostics = []
        for step in failed_steps:
            result = step.get("verification_result_json") or {}
            diagnostics.extend(result.get("diagnostics") or [])
        return {
            "status": "ok" if not failed_steps else "attention_required",
            "run_id": run_id,
            "current_step": (summary.get("run") or {}).get("current_step"),
            "failed_verifications": len(failed_steps),
            "unresolved_diagnostics": diagnostics,
            "apply_attempts": len([op for op in operations if op.get("applied_at")]),
            "rollback_available": any(op.get("rollback_command") for op in operations),
            "router_snapshot_age": (snapshot or {}).get("created_at", ""),
            "live_apply_enabled": live_apply_enabled(),
            "next_action": "review_diagnostics" if failed_steps else "continue_wizard",
        }

    def compatibility(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        identity = (snapshot or {}).get("identity") or {}
        version_text = json.dumps(identity, ensure_ascii=False)
        warnings: list[str] = []
        if "6." in version_text:
            warnings.append("RouterOS 6 may not support WireGuard")
        return {
            "routeros_version": identity.get("routeros_version", ""),
            "features": {
                "wireguard": "6." not in version_text,
                "hotspot": True,
                "pppoe": True,
                "radius": True,
                "api": True,
            },
            "warnings": warnings,
        }


def _snapshot_summary(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {}
    return {
        "id": snapshot.get("id"),
        "source": snapshot.get("source"),
        "created_at": snapshot.get("created_at"),
        "interfaces": len(snapshot.get("interfaces") or []),
        "addresses": len(snapshot.get("addresses") or []),
        "routes": len(snapshot.get("routes") or []),
        "risk_report": snapshot.get("risk_report") or {},
    }
