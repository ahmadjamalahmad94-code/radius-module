"""SW2 — Internet uplink phase planner (PhasePlanner protocol).

Wraps the legacy v2 `InternetUplinkScriptPlanner` so the v3
orchestrator can drive it through the uniform SW1 contract:

  * input:  `run_id` + `inputs` mapping (with `source_type`
            and a normalised payload)
  * output: `PhasePlanResult` (script, rollback, validation
            commands, warnings, notes, tags, blockers)

Validation failures get translated into diagnostic codes from
the catalogue (so the UI can show Arabic explanations from
`setup_wizard_diagnostics`).

This module never touches DB / Flask / network — pure
functions only. The orchestrator persists the result.
"""
from __future__ import annotations

from typing import Any, Mapping

from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_internet_planner import (
    InternetScriptPlan,
    InternetUplinkScriptPlanner,
)
from .setup_wizard_phase_planner import (
    PhasePlannerBase,
    PhasePlanResult,
)


_ALLOWED_SOURCES = {"vlan", "static", "dhcp", "pppoe"}


def _map_validation_to_code(source_type: str, message: str) -> str:
    """Translate a `SetupWizardValidationError` message into a
    diagnostic code from the catalogue. The legacy planner
    raises free-text messages; this keeps the v3 surface
    deterministic."""
    low = (message or "").lower()
    if "username" in low or "password" in low:
        return "internet_pppoe_credentials_missing"
    if "vlan_id" in low:
        return "internet_static_ip_invalid"
    if "cidr" in low or "gateway" in low or "valid ipv4" in low:
        return "internet_static_ip_invalid"
    if "dns" in low:
        return "internet_dns_unresolved"
    if "interface" in low:
        return "internet_interface_missing"
    if "address_mode" in low or "source" in low:
        return "internet_source_missing"
    if "name" in low:
        return "internet_interface_missing"
    # Catch-all so a planner change never leaks an unknown code.
    return "internet_source_missing"


def _extract_payload(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """The orchestrator may pass payload either as a nested
    `payload` dict or as flat top-level keys. Accept both."""
    if isinstance(inputs.get("payload"), Mapping):
        return dict(inputs["payload"])
    flat = dict(inputs)
    flat.pop("source_type", None)
    flat.pop("run_id", None)
    return flat


class InternetPhasePlanner(PhasePlannerBase):
    """SW1-protocol-conforming internet uplink planner."""

    PHASE = "internet"

    def __init__(
        self,
        legacy: InternetUplinkScriptPlanner | None = None,
    ) -> None:
        # Composition over inheritance — the v2 planner stays
        # used as-is by the legacy wizard.
        self._legacy = legacy or InternetUplinkScriptPlanner()

    def plan(
        self, *, run_id: int, inputs: Mapping[str, Any],
    ) -> PhasePlanResult:
        source_type = str(
            inputs.get("source_type") or "",
        ).strip().lower()

        # ── Hard blockers (no script emitted) ───────────────
        if not source_type:
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=("internet_source_missing",),
            )
        if source_type not in _ALLOWED_SOURCES:
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=("internet_source_missing",),
            )

        payload = _extract_payload(inputs)

        # ── Delegate to legacy planner for script bytes ─────
        try:
            legacy_plan: InternetScriptPlan = self._legacy.plan(
                wizard_run_id=int(run_id),
                source_type=source_type,
                payload=payload,
            )
        except SetupWizardValidationError as exc:
            code = _map_validation_to_code(source_type, str(exc))
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=(code,),
            )

        # ── Build the SW1-shaped result ─────────────────────
        tag = self.comment_prefix(run_id=run_id, step="internet")
        notes = self._build_notes(source_type, payload)

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

    # ─── Operator-facing notes (Arabic) ────────────────────

    @staticmethod
    def _build_notes(
        source_type: str, payload: Mapping[str, Any],
    ) -> list[str]:
        notes: list[str] = [
            "ألصق السكربت في MikroTik Terminal، ثم انتظر اكتمال "
            "أوامر التحقّق في الأسفل قبل المتابعة.",
        ]
        if payload.get("add_default_route", True):
            notes.append(
                "سيُضاف مسار افتراضي جديد. إذا كنت متّصلاً عبر "
                "الإنترنت الحالي، استخدم Winbox على شبكة LAN "
                "قبل اللصق."
            )
        if source_type == "pppoe":
            notes.append(
                "كلمة مرور PPPoE تظهر داخل السكربت — لا "
                "تشاركه مع طرف خارجي."
            )
        return notes


__all__ = [
    "InternetPhasePlanner",
]
