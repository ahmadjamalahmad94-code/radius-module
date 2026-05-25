"""SW1: setup wizard run/step state foundation.

This service is intentionally state-only:
- no router execution
- no live MikroTik calls
- no script planning beyond storing generated previews
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..db.connection import db, transaction
from .setup_wizard_broadband_planner import BroadbandBootstrapPlanner
from .setup_wizard_added_services import AddedServicesPlanner
from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_hotspot_planner import HotspotBootstrapPlanner
from .setup_wizard_inventory import RouterInventoryService
from .setup_wizard_interface_contract import InterfaceDiscoveryContract, InterfaceInfo
from .setup_wizard_lab import SetupWizardLabPolicyEngine
from .setup_wizard_operations import (
    SetupWizardApplyService,
    SetupWizardDryRunService,
    SetupWizardOperationRepo,
    SetupWizardRollbackService,
)
from .setup_wizard_orchestration import (
    SetupWizardBroadbandOrchestrator,
    SetupWizardHotspotOrchestrator,
)
from .setup_wizard_pilot import SetupWizardPilotDrillService
from .setup_wizard_provisioning_orchestrator import RouterProvisioningOrchestrator
from .setup_wizard_recovery import SetupWizardRecoveryService
from .setup_wizard_router_provisioning import RouterProvisioningService
from .setup_wizard_server_wg import ServerWireGuardPeerApplyService
from .setup_wizard_support import SetupWizardSupportService
from .setup_wizard_internet_planner import InternetUplinkScriptPlanner
from .setup_wizard_verification import SetupVerificationEngine, SetupVerificationService
from .setup_wizard_vpn_radius_planner import VpnRadiusBootstrapPlanner
from .wireguard_peer_health import WireGuardPeerHealthService


RUN_STATUS_ACTIVE = "active"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUSES = {RUN_STATUS_ACTIVE, RUN_STATUS_FAILED, RUN_STATUS_COMPLETED}

STEP_STATUS_PENDING = "pending"
STEP_STATUS_GENERATED = "generated"
STEP_STATUS_APPLIED_BY_CUSTOMER = "applied_by_customer"
STEP_STATUS_VERIFIED = "verified"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_SKIPPED = "skipped"
STEP_STATUS_ABANDONED = "abandoned"
STEP_STATUSES = {
    STEP_STATUS_PENDING,
    STEP_STATUS_GENERATED,
    STEP_STATUS_APPLIED_BY_CUSTOMER,
    STEP_STATUS_VERIFIED,
    STEP_STATUS_FAILED,
    STEP_STATUS_SKIPPED,
    STEP_STATUS_ABANDONED,
}

INTERNET_SOURCE_TYPES = {"vlan", "static", "dhcp", "pppoe"}

STEP_WELCOME = "welcome"
STEP_INTERNET_SOURCE_SELECT = "internet_source_select"
STEP_INTERNET_SOURCE_DETAILS = "internet_source_details"
STEP_INTERNET_SCRIPT_PREVIEW = "internet_script_preview"
STEP_INTERNET_VERIFICATION = "internet_verification"
STEP_VPN_RADIUS_SCRIPT_PREVIEW = "vpn_radius_script_preview"
STEP_VPN_RADIUS_VERIFICATION = "vpn_radius_verification"
STEP_INTERFACES_REFRESH = "interfaces_refresh"
STEP_HOTSPOT_CHOICE = "hotspot_choice"
STEP_HOTSPOT_CONFIG = "hotspot_config"
STEP_HOTSPOT_SCRIPT_PREVIEW = "hotspot_script_preview"
STEP_HOTSPOT_VERIFICATION = "hotspot_verification"
STEP_BROADBAND_CHOICE = "broadband_choice"
STEP_BROADBAND_CONFIG = "broadband_config"
STEP_BROADBAND_SCRIPT_PREVIEW = "broadband_script_preview"
STEP_BROADBAND_VERIFICATION = "broadband_verification"
STEP_ADDED_SERVICES_CHOICE = "added_services_choice"
STEP_ADDED_SERVICE_CONFIG = "added_service_config"
STEP_FINAL_SUMMARY = "final_summary"

KNOWN_STEPS = {
    STEP_WELCOME,
    STEP_INTERNET_SOURCE_SELECT,
    STEP_INTERNET_SOURCE_DETAILS,
    STEP_INTERNET_SCRIPT_PREVIEW,
    STEP_INTERNET_VERIFICATION,
    STEP_VPN_RADIUS_SCRIPT_PREVIEW,
    STEP_VPN_RADIUS_VERIFICATION,
    STEP_INTERFACES_REFRESH,
    STEP_HOTSPOT_CHOICE,
    STEP_HOTSPOT_CONFIG,
    STEP_HOTSPOT_SCRIPT_PREVIEW,
    STEP_HOTSPOT_VERIFICATION,
    STEP_BROADBAND_CHOICE,
    STEP_BROADBAND_CONFIG,
    STEP_BROADBAND_SCRIPT_PREVIEW,
    STEP_BROADBAND_VERIFICATION,
    STEP_ADDED_SERVICES_CHOICE,
    STEP_ADDED_SERVICE_CONFIG,
    STEP_FINAL_SUMMARY,
}

OPTIONAL_STEPS = {
    STEP_HOTSPOT_CHOICE,
    STEP_HOTSPOT_CONFIG,
    STEP_HOTSPOT_SCRIPT_PREVIEW,
    STEP_HOTSPOT_VERIFICATION,
    STEP_BROADBAND_CHOICE,
    STEP_BROADBAND_CONFIG,
    STEP_BROADBAND_SCRIPT_PREVIEW,
    STEP_BROADBAND_VERIFICATION,
    STEP_ADDED_SERVICES_CHOICE,
    STEP_ADDED_SERVICE_CONFIG,
}

SCRIPT_STEPS = {
    STEP_INTERNET_SCRIPT_PREVIEW,
    STEP_VPN_RADIUS_SCRIPT_PREVIEW,
    STEP_HOTSPOT_SCRIPT_PREVIEW,
    STEP_BROADBAND_SCRIPT_PREVIEW,
}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _first_policy_code(policy: dict[str, Any]) -> str:
    reasons = policy.get("blocking_reasons") or []
    if reasons and isinstance(reasons[0], dict):
        return str(reasons[0].get("code") or "lab_policy_blocked")
    return "lab_policy_blocked"


def _row_to_run(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["verification_status_json"] = _json_loads(
        data.get("verification_status_json") or "{}", {}
    )
    return data


def _row_to_step(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["input_json"] = _json_loads(data.get("input_json") or "{}", {})
    data["validation_commands_json"] = _json_loads(
        data.get("validation_commands_json") or "[]", []
    )
    data["verification_result_json"] = _json_loads(
        data.get("verification_result_json") or "{}", {}
    )
    return data


@dataclass(frozen=True)
class SetupWizardStateMachine:
    """Safety checks for wizard phase transitions."""

    def guard_step_access(
        self,
        *,
        run: dict[str, Any],
        step_key: str,
        internet_verified: bool,
        vpn_verified: bool,
    ) -> None:
        if step_key not in KNOWN_STEPS:
            raise SetupWizardValidationError(f"unknown step: {step_key}")
        if run.get("status") != RUN_STATUS_ACTIVE:
            raise SetupWizardValidationError("wizard run is not active")
        if step_key in {
            STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            STEP_VPN_RADIUS_VERIFICATION,
            STEP_INTERFACES_REFRESH,
            STEP_HOTSPOT_CHOICE,
            STEP_HOTSPOT_CONFIG,
            STEP_HOTSPOT_SCRIPT_PREVIEW,
            STEP_HOTSPOT_VERIFICATION,
            STEP_BROADBAND_CHOICE,
            STEP_BROADBAND_CONFIG,
            STEP_BROADBAND_SCRIPT_PREVIEW,
            STEP_BROADBAND_VERIFICATION,
            STEP_ADDED_SERVICES_CHOICE,
            STEP_ADDED_SERVICE_CONFIG,
            STEP_FINAL_SUMMARY,
        } and not internet_verified:
            raise SetupWizardValidationError("internet verification is required first")
        if step_key in {
            STEP_INTERFACES_REFRESH,
            STEP_HOTSPOT_CHOICE,
            STEP_HOTSPOT_CONFIG,
            STEP_HOTSPOT_SCRIPT_PREVIEW,
            STEP_HOTSPOT_VERIFICATION,
            STEP_BROADBAND_CHOICE,
            STEP_BROADBAND_CONFIG,
            STEP_BROADBAND_SCRIPT_PREVIEW,
            STEP_BROADBAND_VERIFICATION,
            STEP_ADDED_SERVICES_CHOICE,
            STEP_ADDED_SERVICE_CONFIG,
            STEP_FINAL_SUMMARY,
        } and not vpn_verified:
            raise SetupWizardValidationError("vpn/radius verification is required first")

    def can_skip_optional(
        self, *, step_key: str, vpn_verified: bool
    ) -> None:
        if step_key not in OPTIONAL_STEPS:
            raise SetupWizardValidationError("only optional steps can be skipped")
        if not vpn_verified:
            raise SetupWizardValidationError("cannot skip optional steps before vpn/radius verification")

    def validate_status_transition(self, *, old: str, new: str, step_key: str) -> None:
        if old not in STEP_STATUSES or new not in STEP_STATUSES:
            raise SetupWizardValidationError("invalid step status")
        if old == STEP_STATUS_SKIPPED and new != STEP_STATUS_SKIPPED:
            raise SetupWizardValidationError("skipped step cannot transition")
        if old == STEP_STATUS_VERIFIED and new != STEP_STATUS_VERIFIED:
            raise SetupWizardValidationError("verified step cannot transition")
        if new == STEP_STATUS_APPLIED_BY_CUSTOMER and old not in {
            STEP_STATUS_PENDING,
            STEP_STATUS_GENERATED,
        }:
            raise SetupWizardValidationError("applied_by_customer requires pending/generated step")
        if new == STEP_STATUS_GENERATED and step_key not in SCRIPT_STEPS:
            raise SetupWizardValidationError("generated status is allowed only for script-preview steps")
        if new == STEP_STATUS_GENERATED and old not in {
            STEP_STATUS_PENDING,
            STEP_STATUS_FAILED,
            STEP_STATUS_GENERATED,
        }:
            raise SetupWizardValidationError("generated status requires pending/failed/generated step")
        if new == STEP_STATUS_VERIFIED and old not in {
            STEP_STATUS_PENDING,
            STEP_STATUS_GENERATED,
            STEP_STATUS_APPLIED_BY_CUSTOMER,
            STEP_STATUS_FAILED,
        }:
            raise SetupWizardValidationError("verified transition is not allowed from current state")
        if new == STEP_STATUS_SKIPPED and old not in {
            STEP_STATUS_PENDING,
            STEP_STATUS_FAILED,
        }:
            raise SetupWizardValidationError("skip transition is not allowed from current state")


class SetupWizardService:
    def __init__(
        self,
        state_machine: SetupWizardStateMachine | None = None,
        interface_discovery: InterfaceDiscoveryContract | None = None,
    ) -> None:
        self._sm = state_machine or SetupWizardStateMachine()
        self._interface_discovery = interface_discovery
        self._internet_planner = InternetUplinkScriptPlanner()
        self._vpn_radius_planner = VpnRadiusBootstrapPlanner()
        self._hotspot_planner = HotspotBootstrapPlanner()
        self._broadband_planner = BroadbandBootstrapPlanner()
        self._verification_service = SetupVerificationService()
        self._verification_engine = SetupVerificationEngine(self._verification_service)
        self._operation_repo = SetupWizardOperationRepo()
        self._dry_run_service = SetupWizardDryRunService(repo=self._operation_repo)
        self._apply_service = SetupWizardApplyService(repo=self._operation_repo)
        self._rollback_service = SetupWizardRollbackService(repo=self._operation_repo)
        self._inventory_service = RouterInventoryService()
        self._added_services_planner = AddedServicesPlanner()
        self._lab_policy = SetupWizardLabPolicyEngine()
        self._router_provisioning = RouterProvisioningService()
        self._provisioning_orchestrator = RouterProvisioningOrchestrator(
            registry=self._router_provisioning
        )
        self._server_wg_apply = ServerWireGuardPeerApplyService()
        self._wireguard_health = WireGuardPeerHealthService()

    def create_run(
        self, *, tenant_id: int, actor: str = "system", router_id: int | None = None
    ) -> dict[str, Any]:
        now = _now()
        with transaction() as c:
            cur = c.execute(
                """
                INSERT INTO setup_wizard_runs (
                  tenant_id, router_id, status, current_step, created_by,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(tenant_id),
                    router_id,
                    RUN_STATUS_ACTIVE,
                    STEP_WELCOME,
                    (actor or "system")[:120],
                    now,
                    now,
                ),
            )
            run_id = int(cur.lastrowid)
        return self.get_run(tenant_id=tenant_id, run_id=run_id)

    def get_run(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM setup_wizard_runs WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(run_id)),
        ).fetchone()
        if not row:
            raise SetupWizardValidationError("wizard run not found")
        return _row_to_run(row)

    def list_run_steps(self, *, tenant_id: int, run_id: int) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM setup_wizard_steps
            WHERE tenant_id=? AND wizard_run_id=?
            ORDER BY id ASC
            """,
            (int(tenant_id), int(run_id)),
        ).fetchall()
        return [_row_to_step(row) for row in rows]

    def get_step(self, *, tenant_id: int, run_id: int, step_key: str) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM setup_wizard_steps
            WHERE tenant_id=? AND wizard_run_id=? AND step_key=?
            """,
            (int(tenant_id), int(run_id), step_key),
        ).fetchone()
        return _row_to_step(row) if row else None

    def _is_step_verified(self, *, tenant_id: int, run_id: int, step_key: str) -> bool:
        step = self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        return bool(step and step.get("status") == STEP_STATUS_VERIFIED)

    def _ensure_step_row(
        self, *, tenant_id: int, run_id: int, step_key: str, input_json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        existing = self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        if existing:
            if input_json is not None:
                self._update_step(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    step_key=step_key,
                    input_json=input_json,
                )
                return self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key) or existing
            return existing
        now = _now()
        with transaction() as c:
            c.execute(
                """
                INSERT INTO setup_wizard_steps (
                  wizard_run_id, tenant_id, step_key, status, input_json,
                  validation_commands_json, verification_result_json,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    int(tenant_id),
                    step_key,
                    STEP_STATUS_PENDING,
                    _json_dumps(input_json or {}),
                    "[]",
                    "{}",
                    now,
                    now,
                ),
            )
        return self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key) or {}

    def _update_step(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        status: str | None = None,
        input_json: dict[str, Any] | None = None,
        generated_script: str | None = None,
        rollback_script: str | None = None,
        validation_commands_json: list[str] | None = None,
        verification_result_json: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {"updated_at": _now()}
        if status is not None:
            fields["status"] = status
        if input_json is not None:
            fields["input_json"] = _json_dumps(input_json)
        if generated_script is not None:
            fields["generated_script"] = generated_script
        if rollback_script is not None:
            fields["rollback_script"] = rollback_script
        if validation_commands_json is not None:
            fields["validation_commands_json"] = _json_dumps(validation_commands_json)
        if verification_result_json is not None:
            fields["verification_result_json"] = _json_dumps(verification_result_json)
        cols = ", ".join(f"{k}=?" for k in fields)
        with transaction() as c:
            c.execute(
                f"""
                UPDATE setup_wizard_steps
                   SET {cols}
                 WHERE tenant_id=? AND wizard_run_id=? AND step_key=?
                """,
                (*fields.values(), int(tenant_id), int(run_id), step_key),
            )

    def _update_run(self, *, tenant_id: int, run_id: int, **fields: Any) -> None:
        payload = {**fields, "updated_at": _now()}
        cols = ", ".join(f"{k}=?" for k in payload)
        with transaction() as c:
            c.execute(
                f"UPDATE setup_wizard_runs SET {cols} WHERE tenant_id=? AND id=?",
                (*payload.values(), int(tenant_id), int(run_id)),
            )

    def advance_to_step(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        input_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        internet_verified = self._is_step_verified(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION
        )
        vpn_verified = self._is_step_verified(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION
        )
        self._sm.guard_step_access(
            run=run,
            step_key=step_key,
            internet_verified=internet_verified,
            vpn_verified=vpn_verified,
        )
        self._ensure_step_row(
            tenant_id=tenant_id, run_id=run_id, step_key=step_key, input_json=input_json
        )
        self._update_run(
            tenant_id=tenant_id,
            run_id=run_id,
            current_step=step_key,
            last_error="",
        )
        return self.get_run(tenant_id=tenant_id, run_id=run_id)

    def set_internet_source(
        self,
        *,
        tenant_id: int,
        run_id: int,
        source_type: str,
        selected_wan_interface: str = "",
        input_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = (source_type or "").strip().lower()
        if normalized not in INTERNET_SOURCE_TYPES:
            raise SetupWizardValidationError("internet_source_type must be vlan/static/dhcp/pppoe")
        self.advance_to_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_INTERNET_SOURCE_DETAILS,
            input_json=input_json or {"internet_source_type": normalized},
        )
        self._update_run(
            tenant_id=tenant_id,
            run_id=run_id,
            internet_source_type=normalized,
            selected_wan_interface=(selected_wan_interface or "")[:120],
        )
        return self.get_run(tenant_id=tenant_id, run_id=run_id)

    def generate_internet_script(
        self,
        *,
        tenant_id: int,
        run_id: int,
        source_type: str,
        payload: dict[str, Any],
        selected_wan_interface: str = "",
    ) -> dict[str, Any]:
        run = self.set_internet_source(
            tenant_id=tenant_id,
            run_id=run_id,
            source_type=source_type,
            selected_wan_interface=selected_wan_interface,
            input_json=payload,
        )
        plan = self._internet_planner.plan(
            wizard_run_id=int(run_id),
            source_type=source_type,
            payload=payload,
        )
        self.mark_script_generated(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_INTERNET_SCRIPT_PREVIEW,
            generated_script=plan.script_text,
            rollback_script=plan.rollback_script_text,
            validation_commands=plan.validation_commands,
        )
        return plan.to_dict()

    def generate_vpn_radius_script(
        self, *, tenant_id: int, run_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        self._sm.guard_step_access(
            run=run,
            step_key=STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            internet_verified=self._is_step_verified(
                tenant_id=tenant_id,
                run_id=run_id,
                step_key=STEP_INTERNET_VERIFICATION,
            ),
            vpn_verified=self._is_step_verified(
                tenant_id=tenant_id,
                run_id=run_id,
                step_key=STEP_VPN_RADIUS_VERIFICATION,
            ),
        )
        reservation = self._router_provisioning.reserve_for_run(
            tenant_id=tenant_id,
            wizard_run_id=run_id,
            router_label=str(payload.get("router_label") or ""),
            router_identity=str(payload.get("router_identity") or ""),
            force_new=bool(payload.get("force_new_reservation", False)),
        )
        endpoint_defaults = self._router_provisioning.endpoint_defaults()
        effective_payload = dict(payload)
        effective_payload.update(
            {
                "wg_interface_name": reservation["wireguard_interface_name"],
                "peer_name": reservation["wireguard_peer_name"],
                "router_vpn_ip": reservation["router_vpn_ip"],
                "vps_vpn_ip": reservation["server_vpn_ip"],
                "allowed_address": f'{reservation["server_vpn_ip"]}/32',
                "radius_server_ip": reservation["server_vpn_ip"],
                "radius_secret": reservation["radius_secret_ref"],
                "radius_secret_ref": reservation["radius_secret_ref"],
                "api_username": reservation["api_username"],
                "router_registry_id": reservation["id"],
                "router_provisioning": reservation,
            }
        )
        effective_payload["vps_public_endpoint"] = str(
            payload.get("vps_public_endpoint")
            or endpoint_defaults.get("vps_public_endpoint")
            or ""
        )
        effective_payload["endpoint_port"] = int(
            payload.get("endpoint_port")
            or endpoint_defaults.get("endpoint_port")
            or 51820
        )
        effective_payload["server_public_key"] = str(
            payload.get("server_public_key")
            or endpoint_defaults.get("server_public_key")
            or ""
        ).strip()
        self.advance_to_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            input_json=effective_payload,
        )
        plan = self._vpn_radius_planner.plan(wizard_run_id=int(run_id), payload=effective_payload)
        reservation = self._router_provisioning.mark_generated(
            tenant_id=tenant_id,
            registry_id=int(reservation["id"]),
        )
        provisioning_status = self._provisioning_orchestrator.record_script_generated(
            tenant_id=tenant_id,
            reservation=reservation,
            listen_port=int(effective_payload["endpoint_port"]),
        )
        self.mark_script_generated(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            generated_script=plan.script_text,
            rollback_script=plan.rollback_script_text,
            validation_commands=plan.validation_commands,
        )
        result = plan.to_dict()
        result["router_provisioning"] = reservation
        result["provisioning_lifecycle"] = provisioning_status
        result["prepared_wireguard_peer"] = provisioning_status.get("prepared_wireguard_peer")
        return result

    def get_verification_contract(
        self, *, tenant_id: int, run_id: int, statuses: dict[str, str] | None = None
    ) -> dict[str, Any]:
        internet_verified = self._is_step_verified(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION
        )
        vpn_verified = self._is_step_verified(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION
        )
        return self._verification_service.build_contract(
            internet_verified=internet_verified,
            vpn_verified=vpn_verified,
            statuses=statuses,
        )

    def verify_internet(
        self, *, tenant_id: int, run_id: int, mode: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        step = self.get_step(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_INTERNET_SOURCE_DETAILS
        ) or {}
        internet_input = dict(step.get("input_json") or {})
        result = self._verification_engine.verify_internet(
            run=run,
            internet_input=internet_input,
            mode=mode,
            payload=payload or {},
        )
        return self._finalize_verification(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_INTERNET_VERIFICATION,
            result=result,
            error_hint="internet verification failed",
        )

    def verify_vpn_radius(
        self, *, tenant_id: int, run_id: int, mode: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        vpn_step = self.get_step(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_VPN_RADIUS_SCRIPT_PREVIEW
        ) or {}
        vpn_payload = dict(vpn_step.get("input_json") or {})
        result = self._verification_engine.verify_vpn_radius(
            run=run,
            vpn_payload=vpn_payload,
            mode=mode,
            payload=payload or {},
        )
        finalized = self._finalize_verification(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_VPN_RADIUS_VERIFICATION,
            result=result,
            error_hint="vpn/radius verification failed",
        )
        if bool(finalized.get("gate_unlocked")):
            reservation = self._router_provisioning.latest_for_run(
                tenant_id=tenant_id,
                wizard_run_id=run_id,
            )
            if reservation:
                try:
                    status = self._provisioning_orchestrator.status(
                        tenant_id=tenant_id,
                        registry_id=int(reservation["id"]),
                    )
                    if status["current_state"] == "peer_ready":
                        self._provisioning_orchestrator.mark_vpn_verified(
                            tenant_id=tenant_id,
                            registry_id=int(reservation["id"]),
                        )
                        self._provisioning_orchestrator.mark_radius_verified(
                            tenant_id=tenant_id,
                            registry_id=int(reservation["id"]),
                        )
                        self._provisioning_orchestrator.mark_api_verified(
                            tenant_id=tenant_id,
                            registry_id=int(reservation["id"]),
                        )
                except SetupWizardValidationError:
                    pass
        return finalized

    def verify_hotspot(
        self, *, tenant_id: int, run_id: int, mode: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = self._verification_engine.verify_hotspot(mode=mode, payload=payload or {})
        return self._finalize_verification(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_HOTSPOT_VERIFICATION,
            result=result,
            error_hint="hotspot verification failed",
        )

    def verify_broadband(
        self, *, tenant_id: int, run_id: int, mode: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = self._verification_engine.verify_broadband(mode=mode, payload=payload or {})
        return self._finalize_verification(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_BROADBAND_VERIFICATION,
            result=result,
            error_hint="broadband verification failed",
        )

    def _finalize_verification(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        result: dict[str, Any],
        error_hint: str,
    ) -> dict[str, Any]:
        if bool(result.get("gate_unlocked")):
            step = self.mark_verified(
                tenant_id=tenant_id,
                run_id=run_id,
                step_key=step_key,
                verification_result=result,
            )
            return {"status": "success", "step": step, **result}
        step = self.mark_failed(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            error_message=str(error_hint),
            verification_result=result,
        )
        status = result.get("overall_status") or "failed"
        return {"status": status, "step": step, **result}

    def get_run_summary(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        steps = self.list_run_steps(tenant_id=tenant_id, run_id=run_id)
        step_index = {step["step_key"]: step for step in steps}
        verification_contract = self.get_verification_contract(
            tenant_id=tenant_id, run_id=run_id
        )
        provisioning_status = self._provisioning_orchestrator.status_for_run(
            tenant_id=tenant_id,
            wizard_run_id=run_id,
        )
        return {
            "run": run,
            "steps": steps,
            "step_index": step_index,
            "verification": verification_contract,
            "operations": self._operation_repo.list_for_run(tenant_id=tenant_id, run_id=run_id),
            "latest_router_snapshot": self._inventory_service.latest_snapshot(
                tenant_id=tenant_id,
                run_id=run_id,
            ),
            "router_provisioning": self._router_provisioning.latest_for_run(
                tenant_id=tenant_id,
                wizard_run_id=run_id,
            ),
            "router_lifecycle": provisioning_status,
            "prepared_wireguard_peer": (
                provisioning_status.get("prepared_wireguard_peer")
                if provisioning_status
                else None
            ),
        }

    def submit_router_public_key(
        self,
        *,
        tenant_id: int,
        run_id: int,
        public_key: str,
        actor: str = "wizard",
    ) -> dict[str, Any]:
        reservation = self._router_provisioning.latest_for_run(
            tenant_id=tenant_id,
            wizard_run_id=run_id,
        )
        if not reservation:
            raise SetupWizardValidationError("router provisioning reservation not found")
        return self._provisioning_orchestrator.submit_router_public_key(
            tenant_id=tenant_id,
            registry_id=int(reservation["id"]),
            public_key=public_key,
            actor=actor,
        )

    def _prepared_peer_id_for_run(self, *, tenant_id: int, run_id: int) -> int:
        summary = self.get_run_summary(tenant_id=tenant_id, run_id=run_id)
        peer = summary.get("prepared_wireguard_peer") or {}
        peer_id = int(peer.get("id") or 0)
        if not peer_id:
            raise SetupWizardValidationError("prepared WireGuard peer not found")
        return peer_id

    def server_peer_dry_run(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        peer_id = self._prepared_peer_id_for_run(tenant_id=tenant_id, run_id=run_id)
        return self._server_wg_apply.dry_run(tenant_id=tenant_id, prepared_peer_id=peer_id)

    def server_peer_apply(
        self, *, tenant_id: int, run_id: int, confirmation: str
    ) -> dict[str, Any]:
        peer_id = self._prepared_peer_id_for_run(tenant_id=tenant_id, run_id=run_id)
        return self._server_wg_apply.apply(
            tenant_id=tenant_id,
            prepared_peer_id=peer_id,
            confirmation=confirmation,
        )

    def server_peer_rollback(
        self, *, tenant_id: int, run_id: int, confirmation: str
    ) -> dict[str, Any]:
        peer_id = self._prepared_peer_id_for_run(tenant_id=tenant_id, run_id=run_id)
        return self._server_wg_apply.rollback(
            tenant_id=tenant_id,
            prepared_peer_id=peer_id,
            confirmation=confirmation,
        )

    def server_peer_verify(
        self, *, tenant_id: int, run_id: int, output: str = ""
    ) -> dict[str, Any]:
        peer_id = self._prepared_peer_id_for_run(tenant_id=tenant_id, run_id=run_id)
        return self._server_wg_apply.verify(
            tenant_id=tenant_id,
            prepared_peer_id=peer_id,
            wg_show_output=output,
        )

    def server_peer_health(
        self,
        *,
        tenant_id: int,
        run_id: int,
        output: str = "",
        previous_observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        peer_id = self._prepared_peer_id_for_run(tenant_id=tenant_id, run_id=run_id)
        peer = self._server_wg_apply._planner.load_peer(
            tenant_id=tenant_id,
            prepared_peer_id=peer_id,
        )
        return self._wireguard_health.inspect_peer(
            prepared_peer=peer,
            wg_show_output=output if output else None,
            previous_observation=previous_observation,
        )

    def server_peer_operations(self, *, tenant_id: int, run_id: int) -> list[dict[str, Any]]:
        peer_id = self._prepared_peer_id_for_run(tenant_id=tenant_id, run_id=run_id)
        return self._server_wg_apply.list_operations(
            tenant_id=tenant_id,
            prepared_peer_id=peer_id,
        )

    def _script_step_for_operation_step(self, step_key: str) -> str:
        normalized = str(step_key or "").strip().lower()
        mapping = {
            "internet": STEP_INTERNET_SCRIPT_PREVIEW,
            "vpn": STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            "vpn-radius": STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            "vpn_radius": STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            "hotspot": STEP_HOTSPOT_SCRIPT_PREVIEW,
            "broadband": STEP_BROADBAND_SCRIPT_PREVIEW,
            STEP_INTERNET_SCRIPT_PREVIEW: STEP_INTERNET_SCRIPT_PREVIEW,
            STEP_VPN_RADIUS_SCRIPT_PREVIEW: STEP_VPN_RADIUS_SCRIPT_PREVIEW,
            STEP_HOTSPOT_SCRIPT_PREVIEW: STEP_HOTSPOT_SCRIPT_PREVIEW,
            STEP_BROADBAND_SCRIPT_PREVIEW: STEP_BROADBAND_SCRIPT_PREVIEW,
        }
        if normalized not in mapping:
            raise SetupWizardValidationError("unknown operation step")
        return mapping[normalized]

    def _operation_tag_step(self, step_key: str) -> str:
        script_step = self._script_step_for_operation_step(step_key)
        if script_step == STEP_INTERNET_SCRIPT_PREVIEW:
            return "internet"
        if script_step == STEP_VPN_RADIUS_SCRIPT_PREVIEW:
            return "vpn"
        if script_step == STEP_HOTSPOT_SCRIPT_PREVIEW:
            return "hotspot"
        if script_step == STEP_BROADBAND_SCRIPT_PREVIEW:
            return "broadband"
        return step_key

    def dry_run_step(self, *, tenant_id: int, run_id: int, step_key: str) -> dict[str, Any]:
        script_step = self._script_step_for_operation_step(step_key)
        step = self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=script_step)
        if not step or not str(step.get("generated_script") or "").strip():
            raise SetupWizardValidationError("generated script is required before dry-run")
        snapshot = self._inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)
        return self._dry_run_service.dry_run(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=self._operation_tag_step(step_key),
            script_text=str(step.get("generated_script") or ""),
            router_snapshot=snapshot or {},
        )

    def apply_step(
        self, *, tenant_id: int, run_id: int, step_key: str, confirmation: str
    ) -> dict[str, Any]:
        script_step = self._script_step_for_operation_step(step_key)
        op_step = self._operation_tag_step(step_key)
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        snapshot = self._inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)
        operations = self._operation_repo.list_for_run(
            tenant_id=tenant_id, run_id=run_id, step_key=op_step
        )
        policy = self._lab_policy.validate(
            run=run,
            step_key=op_step,
            snapshot=snapshot,
            operations=operations,
            script_step=self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=script_step),
        )
        if not policy["allowed"]:
            return {
                "status": "blocked",
                "blocked_reason": _first_policy_code(policy),
                "policy": policy,
            }
        preview = self._rollback_service.preview(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=op_step,
        )
        if not preview.get("operations"):
            return {
                "status": "blocked",
                "blocked_reason": "rollback_missing",
                "policy": policy,
            }
        result = self._apply_service.apply(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=op_step,
            confirmation=confirmation,
        )
        result["policy"] = policy
        result["verification_required"] = result.get("status") == "applied"
        result["rollback_suggested"] = result.get("status") == "failed"
        return result

    def rollback_step(
        self, *, tenant_id: int, run_id: int, step_key: str, confirmation: str = "", preview: bool = False
    ) -> dict[str, Any]:
        self._script_step_for_operation_step(step_key)
        op_step = self._operation_tag_step(step_key)
        if preview:
            return self._rollback_service.preview(
                tenant_id=tenant_id,
                run_id=run_id,
                step_key=op_step,
            )
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        snapshot = self._inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)
        operations = self._operation_repo.list_for_run(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=op_step,
        )
        policy = self._lab_policy.validate_rollback(
            run=run,
            step_key=op_step,
            snapshot=snapshot,
            operations=operations,
        )
        if not policy["allowed"]:
            return {
                "status": "blocked",
                "blocked_reason": _first_policy_code(policy),
                "policy": policy,
            }
        return self._rollback_service.rollback(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=op_step,
            confirmation=confirmation,
        )

    def list_operations(
        self, *, tenant_id: int, run_id: int, step_key: str | None = None
    ) -> list[dict[str, Any]]:
        return self._operation_repo.list_for_run(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=self._operation_tag_step(step_key) if step_key else None,
        )

    def collect_router_inventory(
        self, *, tenant_id: int, run_id: int, output: str
    ) -> dict[str, Any]:
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        return self._inventory_service.create_from_pasted_output(
            tenant_id=tenant_id,
            run_id=run_id,
            output=output,
            selected_wan_interface=str(run.get("selected_wan_interface") or ""),
        )

    def latest_router_snapshot(self, *, tenant_id: int, run_id: int) -> dict[str, Any] | None:
        return self._inventory_service.latest_snapshot(tenant_id=tenant_id, run_id=run_id)

    def plan_hotspot_orchestration(
        self,
        *,
        tenant_id: int,
        run_id: int,
        mode: str,
        payload: dict[str, Any],
        manual_override: bool = False,
    ) -> dict[str, Any]:
        orchestrator = SetupWizardHotspotOrchestrator(
            wizard_service=self,
            inventory_service=self._inventory_service,
            dry_run_service=self._dry_run_service,
        )
        return orchestrator.plan_from_snapshot(
            tenant_id=tenant_id,
            run_id=run_id,
            mode=mode,
            payload=payload,
            manual_override=manual_override,
        )

    def plan_broadband_orchestration(
        self,
        *,
        tenant_id: int,
        run_id: int,
        mode: str,
        payload: dict[str, Any],
        manual_override: bool = False,
    ) -> dict[str, Any]:
        orchestrator = SetupWizardBroadbandOrchestrator(
            wizard_service=self,
            inventory_service=self._inventory_service,
            dry_run_service=self._dry_run_service,
        )
        return orchestrator.plan_from_snapshot(
            tenant_id=tenant_id,
            run_id=run_id,
            mode=mode,
            payload=payload,
            manual_override=manual_override,
        )

    def added_services_catalog(self) -> dict[str, Any]:
        return self._added_services_planner.catalog_payload()

    def plan_added_service(
        self, *, tenant_id: int, run_id: int, service_key: str, inputs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.advance_to_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_ADDED_SERVICE_CONFIG,
            input_json={"service_key": service_key, "inputs": inputs or {}},
        )
        return self._added_services_planner.plan(
            wizard_run_id=run_id,
            service_key=service_key,
            inputs=inputs or {},
        )

    def dry_run_added_service(
        self, *, tenant_id: int, run_id: int, service_key: str, inputs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.advance_to_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_ADDED_SERVICE_CONFIG,
            input_json={"service_key": service_key, "inputs": inputs or {}, "mode": "dry_run"},
        )
        return self._added_services_planner.dry_run(
            wizard_run_id=run_id,
            service_key=service_key,
            inputs=inputs or {},
        )

    def verify_added_service(
        self, *, tenant_id: int, run_id: int, service_key: str
    ) -> dict[str, Any]:
        self.advance_to_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_ADDED_SERVICE_CONFIG,
            input_json={"service_key": service_key, "mode": "verify_guidance"},
        )
        return self._added_services_planner.verify_guidance(service_key=service_key)

    def support_bundle(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        return SetupWizardSupportService(wizard_service=self).support_bundle(
            tenant_id=tenant_id,
            run_id=run_id,
        )

    def health(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        return SetupWizardSupportService(wizard_service=self).health(
            tenant_id=tenant_id,
            run_id=run_id,
        )

    def pilot_drill(
        self, *, tenant_id: int, run_id: int, step_key: str = "internet"
    ) -> dict[str, Any]:
        return SetupWizardPilotDrillService(wizard_service=self).build_drill(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
        )

    def recovery(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        return SetupWizardRecoveryService(wizard_service=self).analyze(
            tenant_id=tenant_id,
            run_id=run_id,
        )

    def recovery_resume(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        return SetupWizardRecoveryService(wizard_service=self).resume(
            tenant_id=tenant_id,
            run_id=run_id,
        )

    def recovery_retry_verification(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str = "",
        mode: str = "pasted_output",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return SetupWizardRecoveryService(wizard_service=self).retry_verification(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            mode=mode,
            payload=payload or {},
        )

    def recovery_regenerate_script(
        self, *, tenant_id: int, run_id: int, step_key: str = "vpn_radius"
    ) -> dict[str, Any]:
        return SetupWizardRecoveryService(wizard_service=self).regenerate_script(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
        )

    def recovery_abandon_step(
        self, *, tenant_id: int, run_id: int, step_key: str, reason: str
    ) -> dict[str, Any]:
        return SetupWizardRecoveryService(wizard_service=self).abandon_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            reason=reason,
        )

    def recovery_retire_router(self, *, tenant_id: int, run_id: int, reason: str) -> dict[str, Any]:
        return SetupWizardRecoveryService(wizard_service=self).retire_router(
            tenant_id=tenant_id,
            run_id=run_id,
            reason=reason,
        )

    def recovery_repair_plan(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        return SetupWizardRecoveryService(wizard_service=self).repair_plan(
            tenant_id=tenant_id,
            run_id=run_id,
        )

    def get_interface_candidates(
        self, *, tenant_id: int, run_id: int, interfaces: list[InterfaceInfo] | None = None
    ) -> list[dict[str, Any]]:
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        internet_verified = self._is_step_verified(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_INTERNET_VERIFICATION
        )
        vpn_verified = self._is_step_verified(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION
        )
        self._sm.guard_step_access(
            run=run,
            step_key=STEP_INTERFACES_REFRESH,
            internet_verified=internet_verified,
            vpn_verified=vpn_verified,
        )
        source = interfaces
        if source is None and self._interface_discovery is not None:
            source = self._interface_discovery.list_interfaces(tenant_id=tenant_id, run_id=run_id)
        source = source or []
        blocked = {str(run.get("selected_wan_interface") or "").strip(), "hr-wg"}
        blocked = {x for x in blocked if x}
        candidates: list[dict[str, Any]] = []
        for item in source:
            if item.name in blocked:
                continue
            candidates.append({"name": item.name, "kind": item.kind, "running": bool(item.running)})
        return candidates

    def generate_hotspot_script(
        self,
        *,
        tenant_id: int,
        run_id: int,
        mode: str,
        payload: dict[str, Any],
        blocked_network_cidrs: list[str],
    ) -> dict[str, Any]:
        self.advance_to_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_HOTSPOT_SCRIPT_PREVIEW,
            input_json={"mode": mode, **payload},
        )
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        blocked_ifaces = [str(run.get("selected_wan_interface") or "").strip(), "hr-wg"]
        plan = self._hotspot_planner.plan(
            wizard_run_id=int(run_id),
            mode=mode,
            payload=payload,
            blocked_interfaces=[x for x in blocked_ifaces if x],
            blocked_network_cidrs=blocked_network_cidrs,
        )
        self.mark_script_generated(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_HOTSPOT_SCRIPT_PREVIEW,
            generated_script=plan.script_text,
            rollback_script=plan.rollback_script_text,
            validation_commands=plan.validation_commands,
        )
        return plan.to_dict()

    def generate_broadband_script(
        self,
        *,
        tenant_id: int,
        run_id: int,
        mode: str,
        payload: dict[str, Any],
        blocked_network_cidrs: list[str],
    ) -> dict[str, Any]:
        self.advance_to_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_BROADBAND_SCRIPT_PREVIEW,
            input_json={"mode": mode, **payload},
        )
        run = self.get_run(tenant_id=tenant_id, run_id=run_id)
        blocked_ifaces = [str(run.get("selected_wan_interface") or "").strip(), "hr-wg"]
        plan = self._broadband_planner.plan(
            wizard_run_id=int(run_id),
            mode=mode,
            payload=payload,
            blocked_interfaces=[x for x in blocked_ifaces if x],
            blocked_network_cidrs=blocked_network_cidrs,
        )
        self.mark_script_generated(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=STEP_BROADBAND_SCRIPT_PREVIEW,
            generated_script=plan.script_text,
            rollback_script=plan.rollback_script_text,
            validation_commands=plan.validation_commands,
        )
        return plan.to_dict()

    def mark_script_generated(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        generated_script: str,
        rollback_script: str = "",
        validation_commands: list[str] | None = None,
    ) -> dict[str, Any]:
        if step_key not in SCRIPT_STEPS:
            raise SetupWizardValidationError("script generation is allowed only for script preview steps")
        self.advance_to_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        step = self._ensure_step_row(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        self._sm.validate_status_transition(
            old=str(step.get("status") or STEP_STATUS_PENDING),
            new=STEP_STATUS_GENERATED,
            step_key=step_key,
        )
        self._update_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            status=STEP_STATUS_GENERATED,
            generated_script=generated_script,
            rollback_script=rollback_script,
            validation_commands_json=validation_commands or [],
        )
        return self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key) or {}

    def mark_applied_by_customer(
        self, *, tenant_id: int, run_id: int, step_key: str
    ) -> dict[str, Any]:
        step = self._ensure_step_row(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        self._sm.validate_status_transition(
            old=str(step.get("status") or STEP_STATUS_PENDING),
            new=STEP_STATUS_APPLIED_BY_CUSTOMER,
            step_key=step_key,
        )
        self._update_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            status=STEP_STATUS_APPLIED_BY_CUSTOMER,
        )
        return self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key) or {}

    def mark_verified(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        verification_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.advance_to_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        step = self._ensure_step_row(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        self._sm.validate_status_transition(
            old=str(step.get("status") or STEP_STATUS_PENDING),
            new=STEP_STATUS_VERIFIED,
            step_key=step_key,
        )
        self._update_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            status=STEP_STATUS_VERIFIED,
            verification_result_json=verification_result or {"ok": True},
        )
        current = self.get_run(tenant_id=tenant_id, run_id=run_id)
        verification_map = dict(current.get("verification_status_json") or {})
        verification_map[step_key] = {"status": "verified", "at": _now()}
        self._update_run(
            tenant_id=tenant_id,
            run_id=run_id,
            verification_status_json=_json_dumps(verification_map),
            last_error="",
        )
        return self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key) or {}

    def mark_failed(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        error_message: str,
        verification_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.advance_to_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        step = self._ensure_step_row(tenant_id=tenant_id, run_id=run_id, step_key=step_key)
        self._sm.validate_status_transition(
            old=str(step.get("status") or STEP_STATUS_PENDING),
            new=STEP_STATUS_FAILED,
            step_key=step_key,
        )
        self._update_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            status=STEP_STATUS_FAILED,
            verification_result_json=verification_result or {"ok": False},
        )
        self._update_run(
            tenant_id=tenant_id,
            run_id=run_id,
            last_error=(error_message or "wizard step failed")[:2000],
        )
        return self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key) or {}

    def skip_optional_step(
        self, *, tenant_id: int, run_id: int, step_key: str, reason: str = ""
    ) -> dict[str, Any]:
        self.get_run(tenant_id=tenant_id, run_id=run_id)
        vpn_verified = self._is_step_verified(
            tenant_id=tenant_id, run_id=run_id, step_key=STEP_VPN_RADIUS_VERIFICATION
        )
        self._sm.can_skip_optional(step_key=step_key, vpn_verified=vpn_verified)
        step = self._ensure_step_row(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            input_json={"skip_reason": reason[:200]},
        )
        self._sm.validate_status_transition(
            old=str(step.get("status") or STEP_STATUS_PENDING),
            new=STEP_STATUS_SKIPPED,
            step_key=step_key,
        )
        self._update_step(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=step_key,
            status=STEP_STATUS_SKIPPED,
            verification_result_json={"skipped": True, "reason": reason[:200]},
        )
        self._update_run(
            tenant_id=tenant_id,
            run_id=run_id,
            current_step=step_key,
        )
        return self.get_step(tenant_id=tenant_id, run_id=run_id, step_key=step_key) or {}


def get_setup_wizard_service() -> SetupWizardService:
    return SetupWizardService()
