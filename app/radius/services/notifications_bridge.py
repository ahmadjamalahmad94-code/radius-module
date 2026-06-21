"""جسر استيعاب الإشعارات من لوحة التراخيص (poll-based).

لوحة التراخيص تُصدر الإشعارات (ترخيص/فوترة/خدمة)؛ هذه الوحدة تَسحبها عبر
الجسر، تُخزّنها في panel_notifications (source='bridge')، ثم تُرسل ack
بمراجعها كي تتوقّف اللوحة عن إعادة إرسالها. إزالة التكرار بمفتاح
dedup_key='bridge:<ref>'، فإعادة السحب لا تُنشئ تكرارًا.

العامل admin_bridge_sync_worker يستدعي sync_once في دورته. الاستيعاب
(ingest) منفصل وقابل للاختبار بحقن قائمة عناصر مباشرة بلا شبكة.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..db.repos import notifications_repo

_LOG = logging.getLogger(__name__)

_VALID_SEVERITY = {"info", "success", "warning", "critical"}
_VALID_TYPE = {"license", "subscription", "service", "support", "billing", "system"}


def _coerce_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """يحوّل عنصرًا قادمًا من اللوحة إلى وسائط notifications_repo.create.

    متسامح مع اختلاف الأسماء (title/subject, body/message, link/url). يتطلّب
    عنوانًا غير فارغ على الأقل. يُرجع None لو العنصر غير صالح.
    """
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or item.get("subject") or "").strip()
    if not title:
        return None
    ntype = str(item.get("type") or "system").strip().lower()
    if ntype not in _VALID_TYPE:
        ntype = "system"
    sev = str(item.get("severity") or "info").strip().lower()
    if sev not in _VALID_SEVERITY:
        sev = "info"
    ref = str(item.get("id") or item.get("ref") or item.get("uid") or "").strip()
    body = str(item.get("body") or item.get("message") or "").strip()
    link = str(item.get("link") or item.get("url") or "").strip()
    # مفتاح إزالة التكرار: مرجع اللوحة لو وُجد، وإلّا توقيع من النوع+العنوان.
    dedup = f"bridge:{ref}" if ref else f"bridge:{ntype}:{title}"
    return {
        "type": ntype, "severity": sev, "title": title, "body": body,
        "link": link, "dedup_key": dedup, "source": "bridge", "source_ref": ref,
    }


class NotificationBridgeService:
    """يسحب الإشعارات من اللوحة، يُخزّنها، ويُرسل ack. آمن الفشل."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        from .admin_panel_client import AdminPanelClient
        return AdminPanelClient()

    def ingest(self, tenant_id: int, items: list[dict[str, Any]]) -> dict[str, Any]:
        """يُخزّن قائمة عناصر إشعارات (من اللوحة) محلّيًّا. قابل للاختبار وحده.

        يُرجع {ingested, seen, refs} حيث refs مراجع اللوحة الصالحة للـ ack.
        """
        ingested, refs = 0, []
        for it in (items or []):
            mapped = _coerce_item(it)
            if not mapped:
                continue
            ref = mapped.get("source_ref") or ""
            if ref:
                refs.append(ref)
            try:
                nid = notifications_repo.create(tenant_id, **mapped)
            except Exception:  # noqa: BLE001 — عنصر تالف لا يُسقط الدفعة
                _LOG.exception("notification ingest item failed")
                continue
            if nid is not None:
                ingested += 1
        return {"ingested": ingested, "seen": len(items or []), "refs": refs}

    def sync_once(self, tenant_id: int = 1) -> dict[str, Any]:
        client = self._get_client()
        try:
            resp = client.poll_notifications(tenant_id=tenant_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("notifications poll failed")
            return {"ok": False, "status": "error", "error": str(exc), "ingested": 0}
        if not isinstance(resp, dict) or not resp.get("ok"):
            return {"ok": False, "status": (resp or {}).get("status", "unknown"),
                    "ingested": 0}
        items = resp.get("notifications") or resp.get("items") or []
        result = self.ingest(tenant_id, items)
        # ack ما خُزّن كي تتوقّف اللوحة عن إعادة الإرسال (أفضل-جهد).
        if result["refs"]:
            try:
                client.ack_notifications(refs=result["refs"], tenant_id=tenant_id)
            except Exception:  # noqa: BLE001
                _LOG.exception("notifications ack failed")
        return {"ok": True, "status": "ok", "ingested": result["ingested"],
                "acked": len(result["refs"])}
