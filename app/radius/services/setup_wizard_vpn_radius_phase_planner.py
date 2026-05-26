"""SW3 — VPN/RADIUS phase planner (PhasePlanner protocol).

Wraps the legacy `VpnRadiusBootstrapPlanner` so the v3
orchestrator drives it through the SW1 protocol. Maps
validation failures into diagnostic codes from the catalogue:
`vpn_not_handshaking`, `wrong_public_endpoint`,
`firewall_blocking_udp`, `radius_secret_mismatch`,
`radius_server_unreachable`, `wrong_allowed_address`,
`route_missing`.

No DB / Flask / network — pure functions only. The
orchestrator persists the result.
"""
from __future__ import annotations

from typing import Any, Mapping

from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_phase_planner import (
    PhasePlannerBase,
    PhasePlanResult,
)
from .setup_wizard_vpn_radius_planner import (
    VpnRadiusBootstrapPlan,
    VpnRadiusBootstrapPlanner,
)


def _map_validation_to_code(message: str) -> str:
    low = (message or "").lower()
    if "radius_secret" in low:
        return "radius_secret_mismatch"
    if "radius_server" in low:
        return "radius_server_unreachable"
    if "endpoint" in low:
        return "wrong_public_endpoint"
    if "allowed_address" in low:
        return "wrong_allowed_address"
    if "public_key" in low or "wireguard" in low:
        return "vpn_not_handshaking"
    if "router_vpn_ip" in low or "vps_vpn_ip" in low:
        return "route_missing"
    # Generic fallback — visible in catalogue.
    return "vpn_not_handshaking"


def _extract_payload(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(inputs.get("payload"), Mapping):
        return dict(inputs["payload"])
    flat = dict(inputs)
    flat.pop("run_id", None)
    return flat


class VpnRadiusPhasePlanner(PhasePlannerBase):
    """SW1-protocol-conforming VPN/RADIUS bootstrap planner."""

    PHASE = "vpn_radius"

    def __init__(
        self,
        legacy: VpnRadiusBootstrapPlanner | None = None,
    ) -> None:
        self._legacy = legacy or VpnRadiusBootstrapPlanner()

    def plan(
        self, *, run_id: int, inputs: Mapping[str, Any],
    ) -> PhasePlanResult:
        payload = _extract_payload(inputs)

        # ── Hard blockers (no script emitted) ───────────────
        if not str(payload.get("radius_secret") or "").strip():
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=("radius_secret_mismatch",),
            )

        # ── Delegate to legacy planner ──────────────────────
        try:
            legacy_plan: VpnRadiusBootstrapPlan = self._legacy.plan(
                wizard_run_id=int(run_id),
                payload=payload,
            )
        except SetupWizardValidationError as exc:
            code = _map_validation_to_code(str(exc))
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=(code,),
            )

        # ── Build SW1-shaped result ─────────────────────────
        vpn_tag = self.comment_prefix(run_id=run_id, step="vpn")
        radius_tag = self.comment_prefix(run_id=run_id, step="radius")
        api_tag = self.comment_prefix(run_id=run_id, step="api")
        notes = [
            "ألصق السكربت في MikroTik Terminal، ثم انتظر "
            "اكتمال أوامر التحقّق في الأسفل قبل المتابعة.",
            "تأكّد من فتح UDP على منفذ الـ endpoint عند مزوّد "
            "الإنترنت قبل لصق السكربت.",
            "سرّ RADIUS يظهر داخل السكربت — لا تشاركه مع طرف "
            "خارجي بعد اللصق.",
        ]
        return PhasePlanResult(
            phase=self.PHASE,
            is_applicable=True,
            script=legacy_plan.script_text,
            rollback_script=legacy_plan.rollback_script_text,
            validation_commands=tuple(legacy_plan.validation_commands),
            warnings=tuple(legacy_plan.warnings),
            notes=tuple(notes),
            tags=(vpn_tag, radius_tag, api_tag),
        )


__all__ = ["VpnRadiusPhasePlanner"]
