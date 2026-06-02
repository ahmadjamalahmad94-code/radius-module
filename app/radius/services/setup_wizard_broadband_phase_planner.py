"""SW5 — Broadband/PPPoE phase planner (PhasePlanner protocol).

Wraps the legacy `BroadbandBootstrapPlanner`. Validation
failures map to catalogue diagnostic codes:
`broadband_no_interface_selected`, `broadband_pool_conflict`.

No DB / Flask / network — pure functions only.
"""
from __future__ import annotations

from typing import Any, Mapping

from .setup_wizard_broadband_planner import (
    BroadbandBootstrapPlanner,
    BroadbandPlan,
)
from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_phase_planner import (
    PhasePlannerBase,
    PhasePlanResult,
)


def _map_validation_to_code(message: str) -> str:
    low = (message or "").lower()
    if "interface" in low and (
        "required" in low or "blocked" in low
    ):
        return "broadband_no_interface_selected"
    if (
        "pool" in low
        or "overlap" in low
        or "conflict" in low
        or "subnet" in low
        or "remote_pool" in low
        or "يتعارض" in low
        or "نطاق العناوين" in low
    ):
        return "broadband_pool_conflict"
    if "interface" in low:
        return "broadband_no_interface_selected"
    return "broadband_no_interface_selected"


def _extract_payload(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(inputs.get("payload"), Mapping):
        return dict(inputs["payload"])
    flat = dict(inputs)
    flat.pop("run_id", None)
    flat.pop("mode", None)
    flat.pop("blocked_interfaces", None)
    flat.pop("blocked_network_cidrs", None)
    return flat


class BroadbandPhasePlanner(PhasePlannerBase):
    """SW1-protocol-conforming broadband/PPPoE planner."""

    PHASE = "broadband"

    def __init__(
        self,
        legacy: BroadbandBootstrapPlanner | None = None,
    ) -> None:
        self._legacy = legacy or BroadbandBootstrapPlanner()

    def plan(
        self, *, run_id: int, inputs: Mapping[str, Any],
    ) -> PhasePlanResult:
        mode = str(inputs.get("mode") or "manual").strip().lower()
        payload = _extract_payload(inputs)
        blocked_interfaces = list(
            inputs.get("blocked_interfaces") or []
        )
        blocked_network_cidrs = list(
            inputs.get("blocked_network_cidrs") or []
        )

        # ── Hard blockers ───────────────────────────────────
        if not (payload.get("selected_interfaces") or []):
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=(
                    "broadband_no_interface_selected",
                ),
            )

        # ── Delegate ────────────────────────────────────────
        try:
            legacy_plan: BroadbandPlan = self._legacy.plan(
                wizard_run_id=int(run_id),
                mode=mode,
                payload=payload,
                blocked_interfaces=blocked_interfaces,
                blocked_network_cidrs=blocked_network_cidrs,
            )
        except SetupWizardValidationError as exc:
            code = _map_validation_to_code(str(exc))
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=(code,),
            )

        tag = self.comment_prefix(run_id=run_id, step="broadband")
        notes = [
            "ألصق السكربت في MikroTik Terminal بعد التأكّد من "
            "إقفال أي خادم PPPoE قائم على نفس الواجهة.",
            "النطاق المُخصّص للعملاء البعيدين معزول عبر NAT "
            "مقيّد بـ src-address لذلك النطاق فقط.",
        ]
        return PhasePlanResult(
            phase=self.PHASE,
            is_applicable=True,
            script=legacy_plan.script_text,
            rollback_script=legacy_plan.rollback_script_text,
            validation_commands=tuple(legacy_plan.validation_commands),
            warnings=tuple(legacy_plan.warnings),
            notes=tuple(notes),
            tags=(tag,),
        )


__all__ = ["BroadbandPhasePlanner"]
