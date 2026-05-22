"""mt_alerts_generator — O9 alert generator from O3 problems.

Bridges the O3 Problems aggregator to the S6 alerts_repo:
  - Each problem maps to one alert row.
  - dedup_key = "auto:<problem_type>:<router_id>" so re-running
    only refreshes existing rows, never duplicates.
  - When a problem disappears (operator fixed the issue), the
    matching open auto-alert is resolved.

This is a manual-trigger service for now — call
`refresh_alerts_from_problems(tenant_id)` from a future cron
or from an admin button. No automatic polling here.
"""
from __future__ import annotations

from typing import Any

from ..db.repos import alerts_repo
from .mt_problems import (
    Problem, SEV_CRITICAL, SEV_WARNING, build_problems,
)


_RULE_PREFIX = "auto."


def _dedup_key_for(problem: Problem) -> str:
    return (f"{_RULE_PREFIX}{problem.type}:"
            f"{problem.router_id}")


def _rule_name_for(problem: Problem) -> str:
    return f"{_RULE_PREFIX}{problem.type}"


def _recommended_action(problem: Problem) -> str:
    """Single Arabic step. Falls back to the problem's own
    suggested_action when nothing more specific is wired."""
    return problem.suggested_action_ar


def refresh_alerts_from_problems(
    tenant_id: int,
) -> dict[str, Any]:
    """Walk current problems, upsert auto-alerts, resolve any
    auto-alert that no longer has a matching problem.

    Returns a summary dict so a future UI can show what changed.
    """
    payload = build_problems(int(tenant_id))
    current_keys: set[str] = set()
    opened = 0
    refreshed = 0
    resolved = 0

    # Open / refresh.
    for bucket in ("now", "soon", "info"):
        for p in payload[bucket]:
            key = _dedup_key_for(p)
            current_keys.add(key)
            before = alerts_repo.list_open(
                int(tenant_id), router_id=p.router_id)
            had_open = any(r.get("dedup_key") == key for r in before)
            alerts_repo.open(
                tenant_id=int(tenant_id),
                rule=_rule_name_for(p),
                dedup_key=key,
                title_ar=p.title_ar,
                router_id=p.router_id,
                severity=p.severity,
                explanation_ar=p.explanation_ar,
                recommended_action_ar=_recommended_action(p),
                evidence={
                    "type": p.type,
                    "suggested_href": p.suggested_href,
                    "last_seen": p.last_seen,
                },
            )
            if had_open:
                refreshed += 1
            else:
                opened += 1

    # Resolve auto-alerts whose key isn't in current_keys.
    all_open = alerts_repo.list_open(int(tenant_id), limit=500)
    for row in all_open:
        key = row.get("dedup_key") or ""
        if not key.startswith(_RULE_PREFIX):
            continue
        if key not in current_keys:
            alerts_repo.resolve(int(tenant_id), key)
            resolved += 1

    return {
        "opened": opened,
        "refreshed": refreshed,
        "resolved": resolved,
        "total_active": len(current_keys),
    }


__all__ = ["refresh_alerts_from_problems"]
