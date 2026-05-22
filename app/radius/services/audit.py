"""
RadiusAuditService — DB-backed audit logger.

- يكتب في جدول audit_log مع tenant_id من flask.g.
- لا يرفع exceptions: فشل التدقيق لا يكسر العملية.
- يقرأ ip_address + user_agent من request إن متاح.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional

from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import RadiusAuditEntry
from ..db.repos import audit_repo

_LOG = logging.getLogger(__name__)


def _tenant_and_request() -> tuple[int, str, str]:
    """يُرجع (tenant_id, ip, ua) من flask context إن متاح."""
    try:
        from flask import g, request
        tid = int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or \
             (request.remote_addr or "")
        ua = (request.headers.get("User-Agent") or "")[:200]
        return tid, ip, ua
    except (ImportError, RuntimeError):
        return DEFAULT_TENANT_ID, "", ""


class RadiusAuditService:
    def record(
        self, *, actor: str, action: str, target_type: str,
        target_id: str, payload: Optional[dict] = None,
        # S2.1 — promoted columns. All optional + backward-compat.
        severity: str = "info",
        result_status: str = "",
        router_id: Optional[int] = None,
        error_message: str = "",
        before: Optional[dict] = None,
        after: Optional[dict] = None,
    ) -> RadiusAuditEntry:
        tid, ip, ua = _tenant_and_request()
        new_id = None
        try:
            new_id = audit_repo.record(
                tenant_id=tid, actor=actor or "system",
                action=action, target_type=target_type,
                target_id=str(target_id),
                payload=payload or {}, ip_address=ip, user_agent=ua,
                severity=severity, result_status=result_status,
                router_id=router_id, error_message=error_message,
                before=before, after=after,
            )
        except Exception:  # noqa: BLE001
            _LOG.warning("audit record failed", exc_info=True)
        return RadiusAuditEntry(
            id=new_id, tenant_id=tid,
            actor=actor or "system", action=action,
            target_type=target_type, target_id=str(target_id),
            payload=payload or {}, created_at=datetime.utcnow(),
        )

    def recent(self, *, limit: int = 100) -> Iterable[RadiusAuditEntry]:
        tid, _, _ = _tenant_and_request()
        rows = audit_repo.recent(tid, limit=limit)
        out: list[RadiusAuditEntry] = []
        from ..db.helpers import json_load, parse_dt
        for r in rows:
            out.append(RadiusAuditEntry(
                id=r["id"], tenant_id=r["tenant_id"],
                actor=r["actor"], action=r["action"],
                target_type=r["target_type"], target_id=r["target_id"],
                payload=json_load(r["payload_json"], default={}),
                created_at=parse_dt(r["created_at"]),
            ))
        return out


_default: Optional[RadiusAuditService] = None


def get_audit_service() -> RadiusAuditService:
    global _default
    if _default is None:
        _default = RadiusAuditService()
    return _default
