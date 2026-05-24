"""Added services catalog and preview planner for setup wizard."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AddedService:
    key: str
    title_ar: str
    description_ar: str
    risk_level: str
    required_inputs: list[str]
    supported: bool
    delegate: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title_ar": self.title_ar,
            "description_ar": self.description_ar,
            "risk_level": self.risk_level,
            "required_inputs": self.required_inputs,
            "supported": self.supported,
            "delegate": self.delegate,
        }


class AddedServicesCatalog:
    def services(self) -> list[AddedService]:
        return [
            AddedService(
                key="walled_garden",
                title_ar="Open sites without login",
                description_ar="Allow selected domains before Hotspot login using the existing NPC planner.",
                risk_level="medium",
                required_inputs=["domains"],
                supported=True,
                delegate="npc_walled_garden_planner",
            ),
            AddedService(
                key="web_block",
                title_ar="Block sites",
                description_ar="Block selected domains using the existing NPC web-block foundation.",
                risk_level="medium",
                required_inputs=["domains"],
                supported=True,
                delegate="npc_web_block_planner",
            ),
            AddedService(
                key="site_exit",
                title_ar="Change public IP / Site exit",
                description_ar="Route selected destinations through a VPS using the existing site-exit foundation.",
                risk_level="high",
                required_inputs=["policy_id"],
                supported=True,
                delegate="site_exit_script_planner",
            ),
            AddedService(
                key="anti_sharing",
                title_ar="Anti-sharing / tethering",
                description_ar="No stable setup-wizard planner is available yet.",
                risk_level="high",
                required_inputs=[],
                supported=False,
                delegate="not_supported_yet",
            ),
        ]

    def presets(self) -> dict[str, list[str]]:
        return {
            "isp_basic": ["web_block"],
            "hotel_cafe": ["walled_garden", "web_block"],
            "school": ["walled_garden", "web_block"],
            "gaming_center": ["web_block", "site_exit"],
        }

    def get(self, key: str) -> AddedService | None:
        return next((svc for svc in self.services() if svc.key == key), None)


class AddedServicesPlanner:
    def __init__(self, catalog: AddedServicesCatalog | None = None) -> None:
        self.catalog = catalog or AddedServicesCatalog()

    def catalog_payload(self) -> dict[str, Any]:
        return {
            "services": [svc.to_dict() for svc in self.catalog.services()],
            "presets": self.catalog.presets(),
        }

    def plan(self, *, wizard_run_id: int, service_key: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        svc = self.catalog.get(service_key)
        if not svc:
            return {"service_key": service_key, "plan_status": "not_supported_yet", "diagnostics": ["unknown_service"]}
        if not svc.supported:
            return {
                "service_key": svc.key,
                "plan_status": "not_supported_yet",
                "warnings": ["service has no stable setup wizard planner yet"],
                "required_inputs": svc.required_inputs,
                "diagnostics": ["not_supported_yet"],
            }
        tag = f"HOBERADIUS_SETUP:{int(wizard_run_id)}:added-services"
        missing = [name for name in svc.required_inputs if not (inputs or {}).get(name)]
        if missing:
            return {
                "service_key": svc.key,
                "plan_status": "blocked",
                "warnings": [f"missing required inputs: {', '.join(missing)}"],
                "required_inputs": svc.required_inputs,
                "diagnostics": ["missing_inputs"],
            }
        return {
            "service_key": svc.key,
            "plan_status": "preview",
            "script_preview": "\n".join([
                "# Added service preview placeholder",
                f"# Delegate: {svc.delegate}",
                f"# Tag: {tag}",
                "# Generate/apply through the existing service module, then dry-run via setup wizard.",
            ]),
            "warnings": ["delegated to existing service foundation; no duplicate networking logic generated here"],
            "required_inputs": svc.required_inputs,
            "validation_commands": ["/ip firewall print detail", "/ip hotspot walled-garden print detail"],
            "rollback_notes": "Rollback must use the delegated service rollback/safety path and setup wizard operation log.",
            "diagnostics": [],
        }
