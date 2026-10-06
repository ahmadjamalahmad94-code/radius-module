"""Audit trail of the operations-assistant executor.

Every decision (validated / rejected / draft / choices / confirmation
mismatch) and every execution (per step) lands in ``audit_log`` with
``target_type='ops_assistant'``: who (the token's admin), the conversation, the
proposal id + hash, when it was confirmed and the outcome. NEVER secrets: the
generated subscriber password and card codes are not passed here at all, and
``audit_repo.record`` redacts secret-keyed values again as a safety net.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from flask import g, request

_LOG = logging.getLogger(__name__)


def _actor() -> str:
    try:
        from ....api.access_control import token_admin
        adm = token_admin()
        if adm is not None:
            return (getattr(adm, "full_name", "") or getattr(adm, "username", "")
                    or f"admin#{adm.id}")
    except Exception:  # noqa: BLE001
        pass
    return f"admin#{int(getattr(g, 'admin_id', 0) or 0)}"


def record(event: str, *, conversation_id: str, outcome: str,
           proposal_id: str = "", proposal_hash: str = "", action: str = "",
           confirmed_at: Optional[str] = None, details: Optional[dict[str, Any]] = None,
           error: str = "") -> None:
    from ...db.repos import audit_repo
    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "admin_id": int(getattr(g, "admin_id", 0) or 0),
        "admin_login": "",
        "proposal_id": proposal_id,
        "proposal_hash": proposal_hash,
        "action": action,
        "confirmed_at": confirmed_at,
    }
    try:
        from ....api.access_control import token_admin
        adm = token_admin()
        payload["admin_login"] = getattr(adm, "username", "") if adm else ""
    except Exception:  # noqa: BLE001
        pass
    if details:
        payload["details"] = details
    severity = "warning" if outcome in {"rejected", "failed", "partial", "mismatch"} else "info"
    try:
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
              or request.remote_addr or "")
        ua = (request.headers.get("User-Agent") or "")[:200]
    except RuntimeError:
        ip, ua = "", ""
    try:
        audit_repo.record(
            tenant_id=int(getattr(g, "tenant_id", 1) or 1), actor=_actor(),
            action=f"ops.{event}", target_type="ops_assistant",
            target_id=proposal_id or conversation_id, payload=payload,
            ip_address=ip, user_agent=ua, severity=severity,
            result_status=outcome[:32], error_message=(error or "")[:500])
    except Exception:  # noqa: BLE001 — auditing never breaks the flow
        _LOG.warning("ops assistant audit failed", exc_info=True)


__all__ = ["record"]
