"""SW6 — Added Services phase planner (PhasePlanner protocol).

Wraps the legacy `AddedServicesPlanner` (which already
delegates to the NPC walled-garden / web-block / site-exit
planners) in the SW1 protocol.

Unlike the other phases, this planner takes a `service_key`
input identifying which added service to plan. Unknown or
unsupported services map to
`added_services_module_not_available` in the catalogue.

No DB / Flask / network — pure functions only.
"""
from __future__ import annotations

from typing import Any, Mapping

from .setup_wizard_added_services import AddedServicesPlanner
from .setup_wizard_phase_planner import (
    PhasePlannerBase,
    PhasePlanResult,
)


def _split_script_into_lines(script: str) -> tuple[str, ...]:
    """Strip comments + blanks to get an executable command
    list for `validation_commands` style consumers."""
    out: list[str] = []
    for raw in (script or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return tuple(out)


def _extract_inputs(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(inputs.get("inputs"), Mapping):
        return dict(inputs["inputs"])
    if isinstance(inputs.get("payload"), Mapping):
        return dict(inputs["payload"])
    flat = dict(inputs)
    flat.pop("run_id", None)
    flat.pop("service_key", None)
    return flat


class AddedServicesPhasePlanner(PhasePlannerBase):
    """SW1-protocol-conforming added-services planner.

    Inputs schema (in the `inputs` mapping passed to `plan`):
      * `service_key` — the added-service key
        (`walled_garden`, `block_sites`, `site_exit_public_ip`)
      * `inputs` (nested) or flat keys — the per-service inputs
        (`domains`, `destinations`, etc.)
    """

    PHASE = "added_services"

    def __init__(
        self,
        legacy: AddedServicesPlanner | None = None,
    ) -> None:
        self._legacy = legacy or AddedServicesPlanner()

    def plan(
        self, *, run_id: int, inputs: Mapping[str, Any],
    ) -> PhasePlanResult:
        service_key = str(
            inputs.get("service_key") or "",
        ).strip()
        if not service_key:
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=(
                    "added_services_module_not_available",
                ),
            )
        svc_inputs = _extract_inputs(inputs)

        # ── Delegate to legacy planner ──────────────────────
        legacy_result = self._legacy.plan(
            wizard_run_id=int(run_id),
            service_key=service_key,
            inputs=svc_inputs,
        )
        status = str(legacy_result.get("plan_status") or "")

        # ── Unknown / unsupported services ──────────────────
        if status in {"rejected", "not_supported_yet"}:
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=(
                    "added_services_module_not_available",
                ),
                notes=(
                    "الخدمة المختارة غير مدعومة في هذا "
                    "الإصدار. اختر خدمة أخرى أو تخطّ هذه "
                    "المرحلة.",
                ),
            )

        # ── Blocked plans surface their diagnostics codes ──
        if status == "blocked":
            blockers = _collect_blocker_codes(legacy_result)
            return PhasePlanResult(
                phase=self.PHASE,
                is_applicable=False,
                blocking_errors=blockers or (
                    "added_services_module_not_available",
                ),
            )

        # ── Successful preview/partial plan ─────────────────
        tag = self.comment_prefix(
            run_id=run_id, step=f"added:{service_key}",
        )
        script = str(legacy_result.get("script_preview") or "")
        rollback = str(legacy_result.get("rollback_notes") or "")
        warnings = [
            str(w)
            for w in (legacy_result.get("warnings") or [])
        ]
        validation = [
            str(c)
            for c in (legacy_result.get("validation_commands") or [])
        ]
        notes = [
            "ألصق السكربت في MikroTik Terminal بعد التحقّق "
            "من القائمة في الواجهة.",
            "الخدمات الإضافية تستخدم محرّك NPC الحالي — لا "
            "حاجة لإعادة تنفيذ سياسات قائمة.",
        ]
        return PhasePlanResult(
            phase=self.PHASE,
            is_applicable=True,
            script=script,
            rollback_script=rollback,
            validation_commands=tuple(validation),
            warnings=tuple(warnings),
            notes=tuple(notes),
            tags=(tag,),
        )


def _collect_blocker_codes(legacy_result: Mapping[str, Any]) -> tuple[str, ...]:
    """Pull diagnostic codes out of the legacy planner's
    response. Legacy emits diagnostics in two shapes (string or
    dict-with-code) — normalise both, and silently swap any
    unknown codes for the catalogue fallback so the UI never
    breaks on stale entries."""
    from . import setup_wizard_diagnostics as d

    out: list[str] = []
    for diag in legacy_result.get("diagnostics") or []:
        code: str
        if isinstance(diag, str):
            code = diag
        elif isinstance(diag, Mapping):
            code = str(diag.get("code") or "")
        else:
            continue
        if not code:
            continue
        if d.has(code):
            out.append(code)
        else:
            out.append("added_services_module_not_available")
    return tuple(out)


__all__ = ["AddedServicesPhasePlanner"]
