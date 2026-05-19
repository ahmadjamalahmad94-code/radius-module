"""Small helpers for future customer-roadmap audit payloads.

These helpers do not write to the database. They only keep metadata shape
consistent for upcoming loans, payments, settlement, restore, and card actions.
"""
from __future__ import annotations

from typing import Any

from ..core.constants import AUDIT_SCHEMA_CUSTOMER_ROADMAP_V1


def roadmap_audit_payload(
    *,
    domain: str,
    action: str,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": AUDIT_SCHEMA_CUSTOMER_ROADMAP_V1,
        "domain": domain,
        "action": action,
        "reason": reason or "",
        "metadata": metadata or {},
    }
