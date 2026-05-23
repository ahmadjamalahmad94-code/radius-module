"""Shared types/constants for setup wizard services."""
from __future__ import annotations

FORBIDDEN_SCRIPT_TOKENS = (
    "/remove",
    "\nremove ",
    "/interface disable",
    "reset-configuration",
    "system reset",
)


class SetupWizardValidationError(ValueError):
    """Raised when wizard state transition or planner input is invalid."""


def assert_safe_script(script_text: str) -> None:
    low = (script_text or "").lower()
    for token in FORBIDDEN_SCRIPT_TOKENS:
        if token in low:
            raise SetupWizardValidationError(
                f"generated script failed safety filter: contains '{token}'"
            )
