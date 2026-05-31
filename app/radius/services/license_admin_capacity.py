"""Backend capacity checks for the V40 admin bridge.

This module reads the last successful capacity-contract snapshot and gates
selected create operations. It does not fetch remote data, mutate RADIUS,
touch MikroTik, or enforce anything when no contract snapshot exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.api.responses import fail
from app.radius.services.admin_panel_client import (
    SNAPSHOT_CAPACITY,
    SNAPSHOT_LICENSE,
    LicenseAdminSnapshotStore,
)
from app.radius.db.repos.service_entitlements_repo import LocalServiceEntitlementRepository
from app.radius.services.license_admin_runtime_sync import (
    ACTIVE_LICENSE_STATUSES,
    BLOCKING_LICENSE_STATUSES,
)
from app.radius.services.license_admin_usage_metering import UsageMeteringService


FEATURE_BLOCK_CODES = {
    "locked": "feature_locked",
    "readonly": "feature_readonly",
    "read_only": "feature_readonly",
    "hidden": "feature_hidden",
}

CAPACITY_FEATURES = {
    "subscribers": {
        "usage_metric": "subscribers_total",
        "limits": {"max_total": "subscribers.max_total"},
    },
    "cards": {
        "usage_metric": "cards_generated_month",
        "limits": {
            "generate_per_batch": "cards.generate_per_batch",
            "monthly_generated": "cards.monthly_generated",
        },
    },
    "nas": {
        "usage_metric": "nas_count",
        "limits": {"max_total": "nas.max_total"},
    },
    "routers": {
        "usage_metric": "routers_count",
        "limits": {"max_total": "routers.max_total"},
    },
    "profiles": {
        "usage_metric": "profiles_plans_count",
        "limits": {"max_total": "profiles.max_total"},
    },
    "print_templates": {
        "usage_metric": "print_templates_count",
        "limits": {"max_active": "print_templates.max_active"},
    },
    "admins": {
        "usage_metric": "admins_count",
        "limits": {"max_total": "admins.max_total"},
    },
}


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


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
        license_decision = self._license_gate(tenant_id=tenant_id, feature_key=feature_key)
        if license_decision is not None:
            return license_decision

        state, payload, warnings = self._capacity_payload(tenant_id=tenant_id)
        if not payload:
            return CapacityDecision(
                allowed=True,
                feature_key=feature_key,
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        service_decision = self._service_gate(payload, feature_key, warnings, str(state.get("status") or "unknown"))
        if service_decision is not None:
            return service_decision

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
        license_decision = self._license_gate(tenant_id=tenant_id, feature_key="cards")
        if license_decision is not None:
            return license_decision

        state, payload, warnings = self._capacity_payload(tenant_id=tenant_id)
        if not payload:
            return CapacityDecision(
                allowed=True,
                feature_key="cards",
                warning_codes=warnings,
                contract_status=str(state.get("status") or "unknown"),
            )

        service_decision = self._service_gate(payload, "cards", warnings, str(state.get("status") or "unknown"))
        if service_decision is not None:
            return service_decision

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

    def capacity_status(self, *, tenant_id: int) -> dict[str, Any]:
        state, payload, warnings = self._capacity_payload(tenant_id=tenant_id)
        license_state, license_payload, license_warnings = self._license_payload(tenant_id=tenant_id)
        warnings = [*warnings, *license_warnings]
        metrics = self.usage_service.collect_metrics(tenant_id=tenant_id)
        contract_status = str(state.get("status") or "unknown")
        status = contract_status
        if "no_capacity_contract" in warnings:
            status = "degraded"
        elif state.get("stale"):
            status = "stale"
        license_status = str(license_payload.get("status") or license_state.get("status") or "unknown")
        if self._license_blocks(license_state=license_state, payload=license_payload):
            status = "blocked"

        features: dict[str, Any] = {}
        for feature_key, spec in CAPACITY_FEATURES.items():
            usage_metric = str(spec["usage_metric"])
            current = int(metrics.get(usage_metric) or 0)
            limits = {
                alias: self._limit(payload, str(path))
                for alias, path in dict(spec["limits"]).items()
            }
            limits = {key: value for key, value in limits.items() if value is not None}
            primary_limit = (
                limits.get("max_total")
                if "max_total" in limits
                else limits.get("monthly_generated")
                if "monthly_generated" in limits
                else limits.get("max_active")
            )
            state_value = self._feature_state(payload, feature_key) if payload else "unknown"
            remaining = None
            if primary_limit is not None:
                remaining = max(0, int(primary_limit) - current)
            blocked_code = FEATURE_BLOCK_CODES.get(state_value)
            features[feature_key] = {
                "state": state_value,
                "usage_metric": usage_metric,
                "current_usage": current,
                "limits": limits,
                "remaining": remaining,
                "blocked": blocked_code is not None,
                "block_code": blocked_code,
                "message_ar": self._feature_status_message(
                    feature_state=state_value,
                    current_usage=current,
                    limit=primary_limit,
                ),
                "upgrade_hint_ar": self._upgrade_hint(feature_key, state_value, current, primary_limit),
            }

        snapshot = state.get("last_success") or {}
        latest_capacity_snapshot = state.get("snapshot") or {}
        latest_capacity_payload = (
            latest_capacity_snapshot.get("payload_json")
            if isinstance(latest_capacity_snapshot, dict)
            else {}
        )
        latest_capacity_contract = (
            latest_capacity_payload.get("contract")
            if isinstance(latest_capacity_payload, dict)
            else {}
        )
        license_snapshot = license_state.get("snapshot") or license_state.get("last_success") or {}
        services = {}
        if isinstance(payload, dict) and isinstance(payload.get("services"), dict):
            services = payload.get("services") or {}
        elif isinstance(latest_capacity_contract, dict) and isinstance(latest_capacity_contract.get("services"), dict):
            services = latest_capacity_contract.get("services") or {}
        elif isinstance(license_payload, dict) and isinstance(license_payload.get("services"), dict):
            services = license_payload.get("services") or {}
        services = self._merge_local_services(tenant_id=tenant_id, services=services)
        return {
            "status": status,
            "mode": "local_snapshot",
            "generated_at": _utcnow(),
            "contract": {
                "status": contract_status,
                "stale": bool(state.get("stale")),
                "snapshot_id": snapshot.get("id") if isinstance(snapshot, dict) else None,
                "fetched_at": snapshot.get("fetched_at") if isinstance(snapshot, dict) else None,
                "warnings": warnings,
            },
            "license": {
                "status": license_status,
                "active": self._license_is_active_payload(license_payload),
                "snapshot_id": license_snapshot.get("id") if isinstance(license_snapshot, dict) else None,
                "fetched_at": license_snapshot.get("fetched_at") if isinstance(license_snapshot, dict) else None,
                "warnings": license_warnings,
            },
            "usage": metrics,
            "features": features,
            "services": services,
            "warnings": warnings,
            "upgrade_intent": {
                "available": True,
                "mode": "local_intent_only",
                "dry_run_only": True,
                "message_ar": "يمكن استخدام هذه البيانات لاحقاً لعرض طلب ترقية، بدون إرسال أي طلب مدفوع من هذا المسار.",
            },
        }

    def _capacity_payload(self, *, tenant_id: int) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        state = self.store.state(tenant_id=tenant_id, snapshot_type=SNAPSHOT_CAPACITY)
        warnings: list[str] = []
        if not state.get("last_success"):
            warnings.append("no_capacity_contract")
            local_services = self._merge_local_services(tenant_id=tenant_id, services={})
            return state, {"services": local_services} if local_services else {}, warnings
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
            or isinstance(contract.get("services"), dict)
        ):
            return state, self._payload_with_local_services(tenant_id=tenant_id, payload=contract), warnings
        return state, self._payload_with_local_services(tenant_id=tenant_id, payload=payload), warnings

    def _payload_with_local_services(self, *, tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        local_services = LocalServiceEntitlementRepository().contract_services(tenant_id=tenant_id)
        if not local_services:
            return payload
        merged = dict(payload)
        services = merged.get("services") if isinstance(merged.get("services"), dict) else {}
        merged["services"] = {**services, **local_services}
        return merged

    def _merge_local_services(
        self,
        *,
        tenant_id: int,
        services: dict[str, Any],
    ) -> dict[str, Any]:
        local_services = LocalServiceEntitlementRepository().contract_services(tenant_id=tenant_id)
        if not local_services:
            return dict(services or {})
        return {**dict(services or {}), **local_services}

    def _license_payload(self, *, tenant_id: int) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        state = self.store.state(tenant_id=tenant_id, snapshot_type=SNAPSHOT_LICENSE)
        warnings: list[str] = []
        snapshot = state.get("snapshot") or state.get("last_success") or {}
        payload = snapshot.get("payload_json") if isinstance(snapshot, dict) else {}
        if state.get("stale"):
            warnings.append("stale_license")
        if not isinstance(payload, dict):
            payload = {}
        return state, payload, warnings

    def _license_gate(self, *, tenant_id: int, feature_key: str) -> CapacityDecision | None:
        license_state, payload, warnings = self._license_payload(tenant_id=tenant_id)
        if not payload:
            return None
        if not self._license_blocks(license_state=license_state, payload=payload):
            return None
        status = str(payload.get("status") or license_state.get("status") or "unknown")
        return CapacityDecision(
            allowed=False,
            feature_key=feature_key,
            code="license_not_active",
            message_ar="النظام غير مفعّل من لوحة التراخيص أو الترخيص موقوف/منتهي.",
            warning_codes=[*warnings, "license_not_active"],
            contract_status=status,
        )

    def _license_blocks(self, *, license_state: dict[str, Any], payload: dict[str, Any]) -> bool:
        if not payload:
            return False
        status = str(payload.get("status") or license_state.get("status") or "unknown").strip().lower()
        if status in BLOCKING_LICENSE_STATUSES:
            return True
        if status in ACTIVE_LICENSE_STATUSES and self._license_is_active_payload(payload):
            return False
        if payload.get("active") is False or payload.get("valid") is False:
            return True
        return False

    def _license_is_active_payload(self, payload: dict[str, Any]) -> bool | None:
        if not isinstance(payload, dict) or not payload:
            return None
        status = str(payload.get("status") or "").strip().lower()
        if status and status not in ACTIVE_LICENSE_STATUSES:
            return False
        if "active" in payload:
            return payload.get("active") is True
        if "valid" in payload:
            return payload.get("valid") is True
        if "ok" in payload:
            return payload.get("ok") is True
        return status in ACTIVE_LICENSE_STATUSES

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

    def _service_gate(
        self,
        payload: dict[str, Any],
        feature_key: str,
        warnings: list[str],
        contract_status: str,
    ) -> CapacityDecision | None:
        services = payload.get("services")
        if not isinstance(services, dict):
            return None
        service = services.get(feature_key)
        if not isinstance(service, dict):
            return None
        status = str(service.get("status") or "disabled").strip().lower()
        enabled = service.get("enabled") is True and status == "active"
        if enabled:
            return None
        return CapacityDecision(
            allowed=False,
            feature_key=feature_key,
            code="service_not_enabled",
            message_ar="هذه الخدمة غير مفعلة لهذا العميل من لوحة التراخيص.",
            warning_codes=[*warnings, "service_not_enabled"],
            contract_status=contract_status,
        )

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

    def _feature_status_message(
        self,
        *,
        feature_state: str,
        current_usage: int,
        limit: int | None,
    ) -> str:
        if feature_state in FEATURE_BLOCK_CODES:
            return self._feature_block_message(feature_state)
        if limit is None:
            return "لا يوجد حد مخزن لهذه الميزة حالياً."
        if current_usage >= limit:
            return "وصلت هذه الميزة إلى الحد المخزن في عقد الترخيص."
        return "هذه الميزة ضمن الحدود المخزنة حالياً."

    def _upgrade_hint(
        self,
        feature_key: str,
        feature_state: str,
        current_usage: int,
        limit: int | None,
    ) -> str:
        if feature_state in FEATURE_BLOCK_CODES:
            return "قد تحتاج إلى ترقية الباقة أو تفعيل الميزة من لوحة الإدارة."
        if limit is not None and current_usage >= limit:
            return "قد تحتاج إلى رفع الحد قبل إضافة عناصر جديدة."
        if feature_key == "cards" and limit is None:
            return "يمكن للواجهة عرض حالة الكروت بدون حظر عند عدم وجود حد شهري مخزن."
        return ""
