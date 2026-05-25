"""Backend capacity checks for the V40 admin bridge.

This module reads the last successful capacity-contract snapshot and gates
selected create operations. It does not fetch remote data, mutate RADIUS,
touch MikroTik, or enforce anything when no contract snapshot exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.api.responses import fail
from app.radius.services.admin_panel_client import (
    SNAPSHOT_CAPACITY,
    LicenseAdminSnapshotStore,
)
from app.radius.services.license_admin_usage_metering import UsageMeteringService


FEATURE_BLOCK_CODES = {
    "locked": "feature_locked",
    "readonly": "feature_readonly",
    "read_only": "feature_readonly",
    "hidden": "feature_hidden",
}


@dataclass(frozen=True)
class CapacityDecision:
    allowed: bool
    feature_key: str
    code: str = "allowed"
    message_ar: str = "مسموح"
    current_usage: int | None = None
    limit: int | None = None
    warning_codes: list[str] = field(default_factory=list)
    contract_status: str = "unknown"

    def details(self) -> dict[str, Any]:
        return {
            "feature_key": self.feature_key,
            "current_usage": self.current_usage,
            "limit": self.limit,
            "warnings": self.warning_codes,
            "contract_status": self.contract_status,
        }


def capacity_error_response(decision: CapacityDecision, *, status: int = 403):
    return fail(
        decision.code,
        decision.message_ar,
        status=status,
        details=decision.details(),
    )


class CapacityEnforcementService:
    """Contract-backed create guard.

    No contract or an unreadable contract is deliberately non-blocking. A stale
    last-successful contract remains enforceable and is flagged in details.
    """

    def __init__(
        self,
        *,
        store: LicenseAdminSnapshotStore | None = None,
        usage_service: UsageMeteringService | None = None,
    ) -> None:
        self.store = store or LicenseAdminSnapshotStore()
        self.usage_service = usage_service or UsageMeteringService()

    def check_create(
        self,
        *,
        tenant_id: int,
        feature_key: str,
        limit_path: str,
        usage_metric: str,
        increment: int = 1,
    ) -> CapacityDecision:
        state, payload, warnings = self._capacity_payload(tenant_id=tenant_id)
        if not payload:
            return CapacityDecision(
                allowed=True,
                feature_key=feature_key,
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        feature_state = self._feature_state(payload, feature_key)
        if feature_state in FEATURE_BLOCK_CODES:
            return CapacityDecision(
                allowed=False,
                feature_key=feature_key,
                code=FEATURE_BLOCK_CODES[feature_state],
                message_ar=self._feature_block_message(feature_state),
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        limit = self._limit(payload, limit_path)
        if limit is None:
            return CapacityDecision(
                allowed=True,
                feature_key=feature_key,
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        metrics = self.usage_service.collect_metrics(tenant_id=tenant_id)
        current = int(metrics.get(usage_metric) or 0)
        if current + int(increment or 0) > limit:
            return CapacityDecision(
                allowed=False,
                feature_key=feature_key,
                code="capacity_limit_exceeded",
                message_ar="تم الوصول إلى الحد المسموح لهذه الميزة.",
                current_usage=current,
                limit=limit,
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        return CapacityDecision(
            allowed=True,
            feature_key=feature_key,
            current_usage=current,
            limit=limit,
            warning_codes=warnings,
            contract_status=str(state.get("status") or "unknown"),
        )

    def check_cards_generate(self, *, tenant_id: int, requested_count: int) -> CapacityDecision:
        state, payload, warnings = self._capacity_payload(tenant_id=tenant_id)
        if not payload:
            return CapacityDecision(
                allowed=True,
                feature_key="cards",
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        feature_state = self._feature_state(payload, "cards")
        if feature_state in FEATURE_BLOCK_CODES:
            return CapacityDecision(
                allowed=False,
                feature_key="cards",
                code=FEATURE_BLOCK_CODES[feature_state],
                message_ar=self._feature_block_message(feature_state),
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        per_batch_limit = self._limit(payload, "cards.generate_per_batch")
        if per_batch_limit is not None and requested_count > per_batch_limit:
            return CapacityDecision(
                allowed=False,
                feature_key="cards",
                code="capacity_limit_exceeded",
                message_ar="عدد الكروت في هذه الدفعة يتجاوز الحد المسموح.",
                current_usage=requested_count,
                limit=per_batch_limit,
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        monthly_limit = self._limit(payload, "cards.monthly_generated")
        if monthly_limit is not None:
            metrics = self.usage_service.collect_metrics(tenant_id=tenant_id)
            current_month = int(metrics.get("cards_generated_month") or 0)
            if current_month + requested_count > monthly_limit:
                return CapacityDecision(
                    allowed=False,
                    feature_key="cards",
                    code="capacity_limit_exceeded",
                    message_ar="تم تجاوز حد الكروت الشهري المسموح.",
                    current_usage=current_month,
                    limit=monthly_limit,
                    warning_codes=warnings,
                    contract_status=str(state.get("status") or "unknown"),
                )

        return CapacityDecision(
            allowed=True,
            feature_key="cards",
            warning_codes=warnings,
            contract_status=str(state.get("status") or "unknown"),
        )

    def _capacity_payload(self, *, tenant_id: int) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        state = self.store.state(tenant_id=tenant_id, snapshot_type=SNAPSHOT_CAPACITY)
        warnings: list[str] = []
        if not state.get("last_success"):
            warnings.append("no_capacity_contract")
            return state, {}, warnings
        if state.get("stale"):
            warnings.append("stale_contract")
        snapshot = state.get("last_success") or {}
        payload = snapshot.get("payload_json") if isinstance(snapshot, dict) else {}
        if not isinstance(payload, dict):
            return state, {}, warnings
        contract = payload.get("contract")
        if isinstance(contract, dict) and (
            isinstance(contract.get("limits"), dict)
            or isinstance(contract.get("features"), dict)
        ):
            return state, contract, warnings
        return state, payload, warnings

    def _feature_state(self, payload: dict[str, Any], feature_key: str) -> str:
        features = payload.get("features")
        if not isinstance(features, dict):
            return "enabled"
        raw = features.get(feature_key)
        if isinstance(raw, str):
            return raw.strip().lower() or "enabled"
        if isinstance(raw, dict):
            return str(raw.get("state") or raw.get("status") or "enabled").strip().lower()
        return "enabled"

    def _limit(self, payload: dict[str, Any], dotted_path: str) -> int | None:
        node: Any = payload.get("limits")
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        try:
            value = int(node)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def _feature_block_message(self, feature_state: str) -> str:
        if feature_state in {"readonly", "read_only"}:
            return "هذه الميزة للقراءة فقط حسب عقد الترخيص الحالي."
        if feature_state == "hidden":
            return "هذه الميزة غير متاحة في عقد الترخيص الحالي."
        return "هذه الميزة مقفلة حسب عقد الترخيص الحالي."
