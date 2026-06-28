"""Live license-admin sync for local runtime enforcement.

The license panel remains the commercial source of truth. This service pulls
the signed license contract, stores a sanitized license snapshot, and derives a
local capacity/services contract that the RADIUS runtime can enforce without
SSH, gateway writes, WireGuard changes, MikroTik changes, or tc changes.
"""
from __future__ import annotations

from typing import Any

from app.radius.services.admin_panel_client import (
    LICENSE_CHECK_PATH,
    SNAPSHOT_CAPACITY,
    AdminBridgeConfig,
    AdminPanelClient,
    LicenseAdminSnapshotStore,
    bridge_flag,
    sanitize_bridge_payload,
)

ACTIVE_LICENSE_STATUSES = {"active", "valid", "ok", "healthy", "grace"}
BLOCKING_LICENSE_STATUSES = {
    "inactive",
    "expired",
    "blocked",
    "suspended",
    "revoked",
    "denied",
    "disabled",
    "not_found",
    "invalid_request",
    "fingerprint_denied",
}

DEFAULT_CAPACITY_FEATURES = (
    "subscribers",
    "cards",
    "nas",
    "routers",
    "profiles",
    "print_templates",
    "admins",
)


def derive_capacity_contract_from_license_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert the admin license-check response into a local capacity contract.

    The current admin panel exposes the production license contract through
    `/api/license/check`. Until a dedicated capacity-contract endpoint exists,
    the runtime can safely derive the limits and services it understands from
    that response while preserving backward-compatible capacity status shape.
    """
    status = _normalize_text(payload.get("status"), default="unknown")
    active = _license_payload_is_active(payload)
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    features = _derive_features(payload.get("features"), active=active)
    limits = _derive_limits(plan)
    services = _derive_services(payload.get("services"), active=active, license_status=status)

    contract = {
        "source": "license_check",
        "license": {
            "active": active,
            "status": status,
            "mode": _normalize_text(payload.get("mode"), default="unknown"),
            "expires_at": payload.get("expires_at"),
            "grace_until": payload.get("grace_until"),
        },
        "plan": sanitize_bridge_payload(plan),
        "limits": limits,
        "features": features,
        "services": services,
    }
    return {
        "status": status,
        "contract": sanitize_bridge_payload(contract),
        "limits": sanitize_bridge_payload(limits),
        "features": sanitize_bridge_payload(features),
        "services": sanitize_bridge_payload(services),
    }


class LicenseAdminRuntimeSyncService:
    """Pull admin approval and publish a local contract snapshot."""

    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
        store: LicenseAdminSnapshotStore | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.store = store or LicenseAdminSnapshotStore()
        self.admin_client = admin_client or AdminPanelClient(
            config=self.config,
            store=self.store,
        )

    def sync_once(self, *, tenant_id: int = 1) -> dict[str, Any]:
        if bridge_flag("HOBERADIUS_ADMIN_RUNTIME_CONTRACT_SYNC", "license_admin_bridge.runtime_contract_sync"):
            return self.sync_runtime_contract_once(tenant_id=tenant_id)
        license_result = self.admin_client.fetch_license_snapshot(tenant_id=tenant_id)
        if not license_result.get("ok"):
            return {
                "ok": False,
                "status": license_result.get("status") or "unavailable",
                "source": "license_check",
                "license_snapshot_id": _snapshot_id(license_result.get("snapshot")),
                "error": sanitize_bridge_payload(license_result.get("error") or {}),
                "state": sanitize_bridge_payload(license_result.get("state") or {}),
            }

        payload = license_result.get("payload")
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "status": "invalid_payload",
                "source": "license_check",
                "license_snapshot_id": _snapshot_id(license_result.get("snapshot")),
                "error": {"code": "missing_license_payload"},
            }

        derived = derive_capacity_contract_from_license_payload(payload)
        capacity_snapshot = self.store.save(
            tenant_id=tenant_id,
            snapshot_type=SNAPSHOT_CAPACITY,
            normalized_status=str(derived.get("status") or "unknown"),
            source_url=self._derived_capacity_source_url(),
            payload=derived,
            stale_after_seconds=_stale_after_seconds(payload),
        )
        contract = derived.get("contract") if isinstance(derived.get("contract"), dict) else {}
        license_info = contract.get("license") if isinstance(contract.get("license"), dict) else {}
        return {
            "ok": True,
            "status": derived.get("status") or "unknown",
            "source": "license_check",
            "license_active": bool(license_info.get("active")),
            "license_snapshot_id": _snapshot_id(license_result.get("snapshot")),
            "capacity_snapshot_id": capacity_snapshot.get("id"),
            "limits": sanitize_bridge_payload(derived.get("limits") or {}),
            "services": sanitize_bridge_payload(derived.get("services") or {}),
        }

    def sync_runtime_contract_once(self, *, tenant_id: int = 1) -> dict[str, Any]:
        contract_result = self.admin_client.fetch_runtime_contract(tenant_id=tenant_id)
        if not contract_result.get("ok"):
            return {
                "ok": False,
                "status": contract_result.get("status") or "unavailable",
                "source": "runtime_contract",
                "error": sanitize_bridge_payload(contract_result.get("error") or {}),
                "state": sanitize_bridge_payload(contract_result.get("state") or {}),
            }
        payload = contract_result.get("payload")
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "status": "invalid_payload",
                "source": "runtime_contract",
                "error": {"code": "missing_runtime_contract"},
            }
        # Consume bridge_token block using the raw (pre-sanitize) response so
        # the token value isn't masked before extraction. Failures are non-fatal.
        try:
            from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService
            raw = contract_result.get("_raw_response") or payload
            BridgeTokenSyncService(
                config=self.config,
                admin_client=self.admin_client,
            ).consume_panel_token(raw, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).debug(
                "bridge_token consume step skipped (non-fatal)", exc_info=True
            )

        contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else payload
        license_info = contract.get("license") if isinstance(contract.get("license"), dict) else {}
        owner_admins = apply_owner_admins_designation(contract.get("owner_admins"))
        return {
            "ok": True,
            "status": payload.get("status") or license_info.get("status") or "unknown",
            "source": "runtime_contract",
            "license_active": bool(license_info.get("active")),
            "capacity_snapshot_id": _snapshot_id(contract_result.get("snapshot")),
            "limits": sanitize_bridge_payload(contract.get("limits") or {}),
            "services": sanitize_bridge_payload(contract.get("services") or {}),
            "owner_admins": owner_admins,
        }

    def _derived_capacity_source_url(self) -> str:
        base = self.config.base_url.rstrip("/") if self.config.base_url else ""
        return f"{base}{LICENSE_CHECK_PATH}#derived-capacity"


def apply_owner_admins_designation(owner_admins: Any) -> list[str]:
    """Consume the synced ``owner_admins`` designation from a license contract.

    The licensing panel designates this customer panel's OWNER account(s)
    explicitly and ships them as a list of STABLE keys (admin username/email).
    When the contract carries a NON-EMPTY list we persist it as the authoritative
    owner set (``admins_repo.set_designated_owners``); the matching local admins
    become owners (full RBAC bypass + uncapped), MULTIPLE owners all qualify.

    We deliberately do NOT clear on an absent/empty field: a legacy panel that
    doesn't emit ``owner_admins`` (or a stray empty payload) must never strip the
    existing owner — owner detection then falls back to the min-id owner. Returns
    the applied keys (empty list when nothing was applied)."""
    if not isinstance(owner_admins, list):
        return []
    keys = [str(k).strip() for k in owner_admins if str(k or "").strip()]
    if not keys:
        return []
    try:
        from app.radius.db.repos import admins_repo
        return admins_repo.set_designated_owners(keys)
    except Exception:  # noqa: BLE001 — sync must not crash on owner-set persist
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "owner_admins designation persist skipped (non-fatal)", exc_info=True)
        return []


def _license_payload_is_active(payload: dict[str, Any]) -> bool:
    status = _normalize_text(payload.get("status"), default="unknown")
    if status not in ACTIVE_LICENSE_STATUSES:
        return False
    if "active" in payload:
        return payload.get("active") is True
    if "valid" in payload:
        return payload.get("valid") is True
    if "ok" in payload:
        return payload.get("ok") is True
    return True


def _derive_features(raw_features: Any, *, active: bool) -> dict[str, dict[str, str]]:
    default_state = "enabled" if active else "locked"
    source = raw_features if isinstance(raw_features, dict) else {}
    features: dict[str, dict[str, str]] = {}
    for key in DEFAULT_CAPACITY_FEATURES:
        raw = source.get(key)
        state = default_state
        if isinstance(raw, dict):
            state = _normalize_text(raw.get("state") or raw.get("status"), default=default_state)
        elif isinstance(raw, str):
            state = _normalize_text(raw, default=default_state)
        elif raw is False:
            state = "locked"
        elif raw is True:
            state = "enabled" if active else "locked"
        if not active:
            state = "locked"
        features[key] = {"state": state}
    return features


def _derive_limits(plan: dict[str, Any]) -> dict[str, dict[str, int]]:
    limits: dict[str, dict[str, int]] = {}
    _set_limit(limits, "subscribers", "max_total", _first_positive_int(plan, "max_users", "max_subscribers"))
    _set_limit(limits, "nas", "max_total", _first_positive_int(plan, "max_nas", "max_routers"))
    _set_limit(limits, "routers", "max_total", _first_positive_int(plan, "max_routers"))
    _set_limit(limits, "admins", "max_total", _first_positive_int(plan, "max_admins"))
    _set_limit(limits, "cards", "generate_per_batch", _first_positive_int(plan, "cards_per_batch"))
    _set_limit(limits, "cards", "monthly_generated", _first_positive_int(plan, "cards_monthly", "max_cards_monthly"))
    _set_limit(limits, "profiles", "max_total", _first_positive_int(plan, "max_profiles"))
    _set_limit(limits, "print_templates", "max_active", _first_positive_int(plan, "max_print_templates"))
    return limits


def _derive_services(raw_services: Any, *, active: bool, license_status: str) -> dict[str, Any]:
    services = sanitize_bridge_payload(raw_services) if isinstance(raw_services, dict) else {}
    if not isinstance(services, dict):
        services = {}

    raw_vpn = services.get("ip_change_vpn")
    if isinstance(raw_vpn, dict):
        vpn = dict(raw_vpn)
    elif raw_vpn is True:
        vpn = {"enabled": True, "status": "active"}
    else:
        vpn = {"enabled": False, "status": "disabled"}

    vpn_status = _normalize_text(vpn.get("status"), default="active" if vpn.get("enabled") else "disabled")
    vpn_enabled = bool(vpn.get("enabled")) and active and vpn_status == "active"
    if not active:
        vpn_status = license_status if license_status in BLOCKING_LICENSE_STATUSES else "disabled"
    vpn["enabled"] = vpn_enabled
    vpn["status"] = vpn_status
    vpn.setdefault("enforcement_mode", "customer_runtime")
    vpn.setdefault("runtime_hint", "wireguard_tc_or_chr_queue")
    services["ip_change_vpn"] = sanitize_bridge_payload(vpn)
    return services


def _set_limit(limits: dict[str, dict[str, int]], feature: str, key: str, value: int | None) -> None:
    if value is None:
        return
    limits.setdefault(feature, {})[key] = value


def _first_positive_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _positive_int(source.get(key))
        if value is not None:
            return value
    return None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _normalize_text(value: Any, *, default: str) -> str:
    text = str(value or "").strip().lower()
    return text or default


def _snapshot_id(snapshot: Any) -> int | None:
    if not isinstance(snapshot, dict):
        return None
    try:
        return int(snapshot.get("id"))
    except (TypeError, ValueError):
        return None


def _stale_after_seconds(payload: dict[str, Any]) -> int:
    try:
        return max(60, min(604800, int(payload.get("stale_after_seconds") or 300)))
    except (TypeError, ValueError):
        return 300
