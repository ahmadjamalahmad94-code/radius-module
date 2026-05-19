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
        self._adapter.disconnect(username, session_id=session_id)
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_DISCONNECT,
            target_type="session",
            target_id=username,
            payload={"session_id": session_id or ""},
        )


def get_online_sessions_service() -> OnlineSessionsService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service

    return OnlineSessionsService(get_radius_adapter(), audit=get_audit_service())
