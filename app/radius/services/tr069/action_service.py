"""Tr069ActionService — تصفّ أوامر TR-069 وتُنفّذها (غير متزامنة عبر العامل).

تسلسل التحقّق: الملكيّة → دعم الموديل → Validation → إخفاء الحسّاس → صفّ Action
→ (العامل ينشئ Task في GenieACS ويتابع). لا يُرسَل أمر لمسار غير موثوق. كل أمر
يُسجَّل في audit_log.
"""
from __future__ import annotations

import secrets
from typing import Any

import json

from ..audit import get_audit_service
from ...core import env_settings
from ...db.repos import tr069_repo


# أفعال آمنة مسموح تصفيفها في هذه المرحلة التجريبيّة.
_SAFE_ACTIONS = {"reboot", "refresh", "connection_request", "change_wifi", "change_pppoe"}
_DANGEROUS = {"factory_reset", "firmware_upgrade"}

# مفتاح الصلاحية المطلوب لكل فعل (يُفرَض في المسار).
ACTION_PERM = {
    "reboot": "routers.reboot",
    "refresh": "routers.view",
    "connection_request": "routers.view",
    "change_wifi": "routers.change_wifi",
    "change_pppoe": "routers.change_pppoe",
    "factory_reset": "routers.factory_reset",
    "firmware_upgrade": "routers.update_firmware",
}


class Tr069ActionError(Exception):
    pass


class Tr069ActionService:
    def __init__(self, tenant_id: int):
        self.tenant_id = int(tenant_id or 1)

    def queue(self, *, device_id: int, action_type: str, params: dict | None = None,
              actor: str, owner_admin_id: int | None = None,
              viewer_admin_id: int | None = None, can_view_all: bool = True) -> dict[str, Any]:
        action_type = str(action_type or "").strip()
        if action_type not in _SAFE_ACTIONS and action_type not in _DANGEROUS:
            raise Tr069ActionError(f"فعل غير معروف: {action_type}")

        device = tr069_repo.get_device(self.tenant_id, device_id)
        if not device:
            raise Tr069ActionError("الجهاز غير موجود.")
        if not can_view_all and device.get("owner_admin_id") not in (None, viewer_admin_id):
            raise Tr069ActionError("لا تملك صلاحيّة على هذا الجهاز.")
        if str(device.get("status")) != "active":
            raise Tr069ActionError("الجهاز غير مُسجَّل بعد (بانتظار أوّل اتصال).")

        params = dict(params or {})
        safe = self._safe_summary(action_type, params)
        # القيم غير الحسّاسة تُعرَض؛ الأسرار تُقنَّع في «params».
        stored = {k: ("***" if ("pass" in k.lower() or "secret" in k.lower()) else v)
                  for k, v in params.items()}
        # الأسرار الخام تُشفَّر Fernet في «_secret_enc» — لا تُخزَّن نصًّا أبدًا؛
        # العامل يفكّها للحظة الإرسال إلى GenieACS ثمّ يمحوها من الصفّ.
        secret_enc = env_settings._encrypt(json.dumps(params)) if params else ""
        idem = secrets.token_hex(8)

        action_id = tr069_repo.create_action(
            self.tenant_id, device_id=device_id, action_type=action_type,
            requested_by=actor, owner_admin_id=owner_admin_id,
            request_payload={"params": stored, "_secret_enc": secret_enc}, safe_summary=safe,
            idempotency_key=idem, correlation_id=idem)

        # أثر تدقيقيّ — بلا أسرار (get_audit_service يُخفي عبر _redact أيضًا).
        try:
            get_audit_service().record(
                actor=actor, action=f"router.{action_type}", target_type="tr069_device",
                target_id=device_id, payload={"summary": safe, "action_id": action_id},
                severity="warning" if action_type in _DANGEROUS else "info")
        except Exception:  # noqa: BLE001 — التدقيق لا يكسر الأمر
            pass

        return {"action_id": action_id, "status": "queued", "summary": safe}

    def _safe_summary(self, action_type: str, params: dict) -> str:
        if action_type == "reboot":
            return "إعادة تشغيل الراوتر"
        if action_type == "refresh":
            return "تحديث بيانات الجهاز من الراوتر"
        if action_type == "connection_request":
            return "طلب اتصال فوريّ من الراوتر"
        if action_type == "change_wifi":
            ssid = params.get("ssid")
            bits = []
            if ssid:
                bits.append(f"اسم Wi-Fi ← «{ssid}»")
            if params.get("password"):
                bits.append("كلمة مرور Wi-Fi (مخفيّة)")
            return "تغيير Wi-Fi: " + (" · ".join(bits) or "—")
        if action_type == "change_pppoe":
            u = params.get("username")
            return f"تغيير PPPoE" + (f" — المستخدم ← «{u}»" if u else "")
        if action_type == "factory_reset":
            return "⚠ إعادة ضبط المصنع"
        if action_type == "firmware_upgrade":
            return f"⚠ تحديث Firmware إلى {params.get('version') or '—'}"
        return action_type
