"""تواصل المشغّل ← لوحة التراخيص (تذاكر/شكاوى) عبر الجسر.

يَحفظ نسخة محلّية (provider_messages) ثم يُمرّر الرسالة للوحة التراخيص عبر
AdminPanelClient.post_support_ticket (أفضل-جهد، لا يكسر عند فشل الجسر)، ثم
يُسقط إشعارًا محلّيًّا يؤكّد الإرسال فيظهر في مركز الإشعارات/الجرس.
"""
from __future__ import annotations

import logging
from typing import Any

from ..db.repos import provider_messages_repo
from . import notifications as _notif

_LOG = logging.getLogger(__name__)


class ProviderCommsService:
    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        from .admin_panel_client import AdminPanelClient
        return AdminPanelClient()

    def submit_ticket(self, tenant_id: int, *, subject: str, body: str,
                      kind: str = "ticket", category: str = "general",
                      priority: str = "normal",
                      created_by: str = "") -> dict[str, Any]:
        """يُنشئ رسالة محلّية + يُمرّرها للوحة، ويُرجع {message_id, bridge,
        notification_id, bridge_status}."""
        msg_id = provider_messages_repo.create(
            tenant_id, kind=kind, subject=subject, body=body,
            category=category, priority=priority, created_by=created_by)

        bridge: dict[str, Any] = {"ok": False, "status": "skipped"}
        try:
            bridge = self._get_client().post_support_ticket(
                tenant_id=tenant_id, subject=subject, body=body,
                category=category, priority=priority, local_ref=str(msg_id))
        except Exception as exc:  # noqa: BLE001 — فشل الجسر لا يُفقِد الرسالة محلّيًّا
            _LOG.exception("provider ticket forward failed")
            bridge = {"ok": False, "status": "error", "error": str(exc)}

        ok = bool(isinstance(bridge, dict) and bridge.get("ok"))
        status = "sent" if ok else "failed"
        ref = str((bridge or {}).get("ref") or (bridge or {}).get("ticket_id") or "")
        provider_messages_repo.set_bridge_status(
            tenant_id, msg_id, status=status, ref=ref)

        # إشعار محلّي يؤكّد الإرسال (يظهر في الجرس/المركز).
        nbody = ("تم تسليم رسالتك إلى لوحة التراخيص."
                 if ok else
                 "حُفظت رسالتك محلّيًّا وستُرسَل عند توفّر الاتصال باللوحة.")
        nid = _notif.notify(
            tenant_id, type="support",
            severity="success" if ok else "warning",
            title=f"طلب دعم: {subject}"[:120], body=nbody,
            link="/admin/radius/notifications",
            dedup_key=f"provider_msg:{msg_id}", source="local")

        return {"message_id": msg_id, "bridge": bridge,
                "bridge_status": status, "notification_id": nid}
