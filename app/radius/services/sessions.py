"""
OnlineSessionsService — قراءة المتصلين الآن + قطع الجلسات.

R8.2: مصدر الجلسات الحيّة تحوّل من MikroTik API (متزامن، قد يستغرق
دقائق عند تعذّر الاتصال) إلى radacct (microseconds). FreeRADIUS هو
الكاتب القياسي لـ radacct بعد R1→R7. الـ legacy method `list_online`
يبقى على الـ adapter للاستخدام التشخيصي اليدوي فقط — لا يُستدعى من
render path.
"""
from __future__ import annotations

from typing import Optional, Sequence

from ..core.constants import AUDIT_ACTION_DISCONNECT
from ..core.types import OnlineSession
from ..integration.adapter import RadiusAdapter
from .audit import RadiusAuditService


class OnlineSessionsService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def list(self, *, limit: int = 200) -> Sequence[OnlineSession]:
        """R8.2: يقرأ الجلسات الحيّة من radacct مباشرة عبر adapter
        method جديدة لا تضرب الشبكة. لو الـ adapter النشط لا يدعمها
        (مثلاً ManualAdapter في الاختبارات القديمة) نرجع لـ list_online
        القديمة كـ fallback — لا blocking لأن ManualAdapter in-memory."""
        rd = getattr(self._adapter, "list_online_from_radacct", None)
        if callable(rd):
            return rd(limit=limit)
        return self._adapter.list_online(limit=limit)

    def disconnect(self, *, actor: str, username: str, session_id: Optional[str] = None) -> None:
        # Gap capture — resolve the target router BEFORE dispatch and record
        # BOTH success and failure with result_status + router_id + error, so
        # the unified MikroTik-actions feed shows «قطع اتصال / router / نجاح|فشل».
        router_id, nas_ip = self._resolve_disconnect_router(username, session_id)
        try:
            self._adapter.disconnect(username, session_id=session_id)
        except Exception as e:  # noqa: BLE001 — record the failure, then re-raise
            self._audit.record(
                actor=actor, action=AUDIT_ACTION_DISCONNECT,
                target_type="session", target_id=username,
                result_status="failed", severity="warning",
                router_id=router_id,
                error_message=str(getattr(e, "message", "") or e)[:2000],
                payload={"session_id": session_id or "", "nas_ip": nas_ip},
            )
            raise
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_DISCONNECT,
            target_type="session",
            target_id=username,
            result_status="success",
            router_id=router_id,
            payload={"session_id": session_id or "", "nas_ip": nas_ip},
        )

    def _resolve_disconnect_router(self, username: str,
                                   session_id: Optional[str]):
        """Best-effort (router_id, nas_ip) for the session being kicked, so the
        audit row carries a real router. Never raises."""
        try:
            from flask import g
            from ..core.tenant import DEFAULT_TENANT_ID
            from ..integration.radius_coa import find_all_nas_for_sessions
            from .mt_action_log import _router_id_for_ip
            tid = int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
            for s in find_all_nas_for_sessions(tid, username):
                if not session_id or s.get("session_id") == session_id:
                    nas_ip = str(s.get("nas_ip") or "")
                    return _router_id_for_ip(tid, nas_ip), nas_ip
        except Exception:  # noqa: BLE001
            pass
        return None, ""


def get_online_sessions_service() -> OnlineSessionsService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service

    return OnlineSessionsService(get_radius_adapter(), audit=get_audit_service())
