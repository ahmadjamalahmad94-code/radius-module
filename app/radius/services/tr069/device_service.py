"""Tr069DeviceService — قراءة الأجهزة وربطها بالمشتركين مع عزل الملكيّة.

العزل داخل نسخة العميل: مدير غير مالك/سوبر لا يرى إلا أجهزة مربوطة به
(owner_admin_id) أو غير مربوطة بعد. التحقّق يقع في الخدمة لا في القالب.
"""
from __future__ import annotations

from typing import Any

from ...db.repos import tr069_repo


class Tr069DeviceService:
    def __init__(self, tenant_id: int):
        self.tenant_id = int(tenant_id or 1)

    # ── القراءة ──
    def list_devices(self, *, viewer_admin_id: int | None, can_view_all: bool,
                     status: str = "", q: str = "") -> list[dict]:
        owner = None if can_view_all else viewer_admin_id
        rows = tr069_repo.list_devices(
            self.tenant_id, owner_admin_id=owner, status=status, q=q)
        return [self._view(r) for r in rows]

    def get_device(self, device_id: int, *, viewer_admin_id: int | None,
                   can_view_all: bool) -> dict | None:
        row = tr069_repo.get_device(self.tenant_id, device_id)
        if not row:
            return None
        if not can_view_all and row.get("owner_admin_id") not in (None, viewer_admin_id):
            return None  # عزل الملكيّة — لا يُكشف وجوده
        view = self._view(row)
        view["snapshot"] = tr069_repo.get_snapshot(self.tenant_id, device_id) or {}
        view["actions"] = [self._action_view(a)
                           for a in tr069_repo.list_actions(self.tenant_id, device_id)]
        view["events"] = tr069_repo.list_events(self.tenant_id, device_id)
        return view

    def counts(self) -> dict[str, int]:
        return tr069_repo.count_devices(self.tenant_id)

    # ── الربط بالمشترك ──
    def link_subscriber(self, device_id: int, subscriber_id: int | None,
                        radius_username: str, *, actor: str, method: str = "manual",
                        viewer_admin_id: int | None = None,
                        can_view_all: bool = True) -> bool:
        row = tr069_repo.get_device(self.tenant_id, device_id)
        if not row:
            return False
        if not can_view_all and row.get("owner_admin_id") not in (None, viewer_admin_id):
            return False
        tr069_repo.update_device(
            self.tenant_id, device_id,
            subscriber_id=subscriber_id, radius_username=radius_username or "")
        tr069_repo.record_assignment(
            self.tenant_id, device_id=device_id, subscriber_id=subscriber_id,
            radius_username=radius_username or "", assigned_by=actor, method=method)
        return True

    # ── نماذج العرض (لا أسرار) ──
    def _view(self, r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": r["id"],
            "status": r.get("status") or "pending",
            "status_ar": _STATUS_AR.get(r.get("status") or "", r.get("status") or "—"),
            "provisioning_status": r.get("provisioning_status") or "none",
            "is_online": bool(r.get("is_online")),
            "is_managed": bool(r.get("is_managed")),
            "subscriber_id": r.get("subscriber_id"),
            "radius_username": r.get("radius_username") or "",
            "manufacturer": r.get("manufacturer") or "",
            "model_name": r.get("model_name") or "",
            "serial_number": r.get("serial_number") or "",
            "software_version": r.get("software_version") or "",
            "hardware_version": r.get("hardware_version") or "",
            "wan_ip": r.get("wan_ip") or "",
            "wan_mac": r.get("wan_mac") or "",
            "lan_ip": r.get("lan_ip") or "",
            "pppoe_username_detected": r.get("pppoe_username_detected") or "",
            "data_model": r.get("data_model") or "",
            "acs_device_id": r.get("acs_device_id") or "",
            "last_inform_at": r.get("last_inform_at") or "",
            "last_error_message": r.get("last_error_message") or "",
            # لا نُعيد cwmp_password_enc / conn_req_password_enc أبدًا
        }

    def _action_view(self, a: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": a["id"],
            "action_type": a.get("action_type") or "",
            "action_ar": _ACTION_AR.get(a.get("action_type") or "", a.get("action_type") or ""),
            "status": a.get("status") or "",
            "status_ar": _ACTION_STATUS_AR.get(a.get("status") or "", a.get("status") or ""),
            "safe_summary": a.get("safe_summary") or "",
            "requested_by": a.get("requested_by") or "",
            "error_message": a.get("error_message") or "",
            "created_at": a.get("created_at") or "",
            "completed_at": a.get("completed_at") or "",
        }


_STATUS_AR = {"pending": "بانتظار التسجيل", "active": "مُسجَّل", "disabled": "معطّل",
              "archived": "مؤرشف"}
_ACTION_AR = {"reboot": "إعادة تشغيل", "refresh": "تحديث البيانات",
              "change_wifi": "تغيير Wi-Fi", "change_pppoe": "تغيير PPPoE",
              "factory_reset": "ضبط المصنع", "firmware_upgrade": "تحديث Firmware",
              "connection_request": "طلب اتصال"}
_ACTION_STATUS_AR = {"pending": "بالانتظار", "queued": "بالطابور", "sent": "أُرسِل",
                     "waiting_for_inform": "بانتظار الجهاز", "acknowledged": "مُستلَم",
                     "completed": "اكتمل", "failed": "فشل", "expired": "منتهٍ",
                     "cancelled": "أُلغي"}
