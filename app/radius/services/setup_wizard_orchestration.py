"""Hotspot/Broadband orchestration over planner + inventory + dry-run."""
from __future__ import annotations

from typing import Any

from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_inventory import RouterInventoryService
from .setup_wizard_operations import SetupWizardDryRunService


class SetupWizardHotspotOrchestrator:
    def __init__(
        self,
        *,
        wizard_service: Any,
        inventory_service: RouterInventoryService | None = None,
        dry_run_service: SetupWizardDryRunService | None = None,
    ) -> None:
        self.wizard_service = wizard_service
        self.inventory_service = inventory_service or RouterInventoryService()
        self.dry_run_service = dry_run_service or SetupWizardDryRunService()

    def plan_from_snapshot(
        self,
        *,
        tenant_id: int,
        run_id: int,
        mode: str,
        payload: dict[str, Any],
        manual_override: bool = False,
    ) -> dict[str, Any]:
        snapshot = self.inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)
        if not snapshot and not manual_override:
            raise SetupWizardValidationError("router snapshot is required unless manual_override is true")
        risk = (snapshot or {}).get("risk_report") or {}
        blocked_networks = list(risk.get("existing_subnets") or [])
        plan = self.wizard_service.generate_hotspot_script(
            tenant_id=tenant_id,
            run_id=run_id,
            mode=mode,
            payload=payload,
            blocked_network_cidrs=blocked_networks,
        )
        dry_run = self.dry_run_service.dry_run(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key="hotspot",
            script_text=str(plan.get("script_text") or ""),
            router_snapshot=snapshot or {},
        )
        return {
            "status": "planned",
            "plan": plan,
            "risk_report": risk,
            "dry_run": dry_run,
            "already_present": _tagged_presence(snapshot, "hotspot"),
        }


class SetupWizardBroadbandOrchestrator:
    def __init__(
        self,
        *,
        wizard_service: Any,
        inventory_service: RouterInventoryService | None = None,
        dry_run_service: SetupWizardDryRunService | None = None,
    ) -> None:
        self.wizard_service = wizard_service
        self.inventory_service = inventory_service or RouterInventoryService()
        self.dry_run_service = dry_run_service or SetupWizardDryRunService()

    def plan_from_snapshot(
        self,
        *,
        tenant_id: int,
        run_id: int,
        mode: str,
        payload: dict[str, Any],
        manual_override: bool = False,
    ) -> dict[str, Any]:
        snapshot = self.inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)
        if not snapshot and not manual_override:
            raise SetupWizardValidationError("router snapshot is required unless manual_override is true")
        risk = (snapshot or {}).get("risk_report") or {}
        blocked_networks = list(risk.get("existing_subnets") or [])
        plan = self.wizard_service.generate_broadband_script(
            tenant_id=tenant_id,
            run_id=run_id,
            mode=mode,
            payload=payload,
            blocked_network_cidrs=blocked_networks,
        )
        dry_run = self.dry_run_service.dry_run(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key="broadband",
            script_text=str(plan.get("script_text") or ""),
            router_snapshot=snapshot or {},
        )
        return {
            "status": "planned",
            "plan": plan,
            "risk_report": risk,
            "dry_run": dry_run,
            "already_present": _tagged_presence(snapshot, "broadband"),
        }


def _tagged_presence(snapshot: dict[str, Any] | None, step: str) -> bool:
    if not snapshot:
        return False
    needle = f":{step}"
    for key in ("hotspot", "ppp", "nat", "pools", "interfaces"):
        for item in snapshot.get(key, []) or []:
            if needle in str(item):
                return True
    return False
