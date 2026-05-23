"""Shared types/constants for setup wizard services."""
from __future__ import annotations


class SetupWizardValidationError(ValueError):
    """Raised when wizard state transition or planner input is invalid."""

