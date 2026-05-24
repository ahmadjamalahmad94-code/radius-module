"""CHR lab-mode policy checks for guarded setup wizard execution."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .setup_wizard_inventory import RouterRiskAnalyzer
from .setup_wizard_operations import (
    OP_STATUS_DRY_RUN_READY,
    live_apply_enabled,
    lab_mode_enabled,
)


ALLOWED_LAB_STEPS = {"internet", "vpn", "hotspot", "broadband"}


class SetupWizardLabPolicyEngine:
    def __init__(
        self,
        *,
        risk_analyzer: RouterRiskAnalyzer | None = None,
        snapshot_max_age_seconds: int = 1800,
    ) -> None:
        self.risk_analyzer = risk_analyzer or RouterRiskAnalyzer()
        self.snapshot_max_age_seconds = int(snapshot_max_age_seconds)

    def validate(
        self,
        *,
        run: dict[str, Any],
        step_key: str,
        snapshot: dict[str, Any] | None,
        operations: list[dict[str, Any]],
        script_step: dict[str, Any] | None = None,
        require_rollback: bool = True,
    ) -> dict[str, Any]:
        normalized = _normalize_step(step_key)
        blocking: list[dict[str, str]] = []
        warnings: list[dict[str, Any]] = []

        if normalized not in ALLOWED_LAB_STEPS:
            blocking.append(_reason("multiple_steps_requested", "Only one explicit lab step may be applied."))
        if not live_apply_enabled():
            blocking.append(_reason("feature_flag_disabled", "HOBERADIUS_SETUP_WIZARD_LIVE_APPLY is disabled."))
        if not lab_mode_enabled():
            blocking.append(_reason("lab_mode_disabled", "HOBERADIUS_SETUP_WIZARD_LAB_MODE is disabled."))
        if not snapshot:
            blocking.append(_reason("inventory_missing", "Router inventory snapshot is required."))
        elif _snapshot_is_stale(str(snapshot.get("created_at") or ""), self.snapshot_max_age_seconds):
            blocking.append(_reason("stale_snapshot", "Router inventory snapshot is stale."))

        if not operations:
            blocking.append(_reason("no_dry_run", "Dry-run operation queue is required."))
        elif not all(op.get("status") == OP_STATUS_DRY_RUN_READY for op in operations):
            blocking.append(_reason("no_dry_run", "All operations must be dry-run-ready before lab apply."))

        rollback_ops = [op for op in operations if str(op.get("rollback_command") or "").strip()]
        if require_rollback and not rollback_ops:
            blocking.append(_reason("rollback_missing", "Rollback preview is required before lab apply."))

        step_input = dict((script_step or {}).get("input_json") or {})
        if snapshot:
            risk = self.risk_analyzer.analyze(
                snapshot=snapshot,
                selected_wan_interface=str(run.get("selected_wan_interface") or ""),
                candidate_cidrs=_candidate_cidrs(normalized, step_input),
            )
            warnings.extend(risk.get("warnings") or [])
            selected = _selected_interfaces(step_input)
            excluded = set(risk.get("excluded_interfaces") or [])
            if selected & excluded:
                blocking.append(_reason("wan_interface_risk", "Selected interface includes WAN/VPN."))
            if risk.get("subnet_overlaps"):
                blocking.append(_reason("subnet_conflict", "Candidate subnet overlaps router inventory."))
            if normalized in {"vpn", "hotspot", "broadband"} and "hr-wg" in excluded and normalized == "vpn":
                warnings.append({"code": "vpn_conflict", "message_ar": "WireGuard interface already appears in inventory."})
            if int(risk.get("existing_nat_count") or 0) > 0:
                warnings.append({"code": "duplicate_nat", "message_ar": "Existing NAT rules require manual review."})

        return {
            "allowed": not blocking,
            "step_key": normalized,
            "blocking_reasons": blocking,
            "warnings": warnings,
            "live_apply_enabled": live_apply_enabled(),
            "lab_mode_enabled": lab_mode_enabled(),
            "next_action_ar": (
                "ممنوع التنفيذ المخبري قبل معالجة أسباب الحظر."
                if blocking
                else "مسموح مخبريًا بخطوة واحدة فقط، ثم يجب تنفيذ التحقق مباشرة."
            ),
        }

    def validate_rollback(
        self,
        *,
        run: dict[str, Any],
        step_key: str,
        snapshot: dict[str, Any] | None,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = _normalize_step(step_key)
        blocking: list[dict[str, str]] = []
        warnings: list[dict[str, Any]] = []
        if normalized not in ALLOWED_LAB_STEPS:
            blocking.append(_reason("multiple_steps_requested", "Only one explicit lab step may be rolled back."))
        if not live_apply_enabled():
            blocking.append(_reason("feature_flag_disabled", "HOBERADIUS_SETUP_WIZARD_LIVE_APPLY is disabled."))
        if not lab_mode_enabled():
            blocking.append(_reason("lab_mode_disabled", "HOBERADIUS_SETUP_WIZARD_LAB_MODE is disabled."))
        if not snapshot:
            blocking.append(_reason("inventory_missing", "Router inventory snapshot is required."))
        elif _snapshot_is_stale(str(snapshot.get("created_at") or ""), self.snapshot_max_age_seconds):
            blocking.append(_reason("stale_snapshot", "Router inventory snapshot is stale."))
        applied_with_rollback = [
            op for op in operations
            if op.get("status") == "applied" and str(op.get("rollback_command") or "").strip()
        ]
        if not applied_with_rollback:
            blocking.append(_reason("rollback_missing", "Rollback drill requires applied tagged operations."))
        if snapshot:
            risk = self.risk_analyzer.analyze(
                snapshot=snapshot,
                selected_wan_interface=str(run.get("selected_wan_interface") or ""),
            )
            warnings.extend(risk.get("warnings") or [])
        return {
            "allowed": not blocking,
            "step_key": normalized,
            "blocking_reasons": blocking,
            "warnings": warnings,
            "live_apply_enabled": live_apply_enabled(),
            "lab_mode_enabled": lab_mode_enabled(),
            "next_action_ar": (
                "ممنوع rollback المخبري قبل توفر snapshot وعمليات مطبقة ذات tag."
                if blocking
                else "مسموح rollback مخبري لعمليات مطبقة وموسومة فقط، ثم يجب التحقق مباشرة."
            ),
        }


def _normalize_step(step_key: str) -> str:
    raw = str(step_key or "").strip().lower()
    if raw in {"vpn-radius", "vpn_radius"}:
        return "vpn"
    if raw not in ALLOWED_LAB_STEPS:
        return raw
    return raw


def _reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _snapshot_is_stale(created_at: str, max_age_seconds: int) -> bool:
    if not created_at:
        return True
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return age.total_seconds() > max_age_seconds


def _selected_interfaces(step_input: dict[str, Any]) -> set[str]:
    raw = step_input.get("selected_interfaces") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _candidate_cidrs(step_key: str, step_input: dict[str, Any]) -> list[str]:
    if step_key == "hotspot" and step_input.get("network_cidr"):
        return [str(step_input["network_cidr"])]
    if step_key == "broadband" and step_input.get("remote_pool_cidr"):
        return [str(step_input["remote_pool_cidr"])]
    return []
