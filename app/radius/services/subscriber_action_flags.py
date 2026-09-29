"""أعلام أفعال المشترك — مصدرٌ واحد للويب (إخفاء الأزرار) والتطبيق (actions-context).

كل علَم = قرار حارس الويب نفسه (``rbac_denial_status``) لمسار الويب المقابل،
بفحصٍ استكشافيّ لا يستهلك المعدّل اليوميّ. D17: أزرار الحذف/التفعيل/التعديل/
الشريط الجماعيّ في قائمة المشتركين وملفّه و360 ونموذجه تُعرَض حسب هذه الأعلام
نفسها التي يقرؤها التطبيق — لا زرّ يظهر ثم يُرفَض.
"""
from __future__ import annotations

from typing import Iterable, Optional

# مفتاح الفعل (قائمة التطبيق / علَم الصلاحية) → مسار الويب الذي يحكم قراره.
WEB_ENDPOINT: dict[str, str] = {
    "extend": "users_extend",
    "quota": "users_quota_topup",
    "quota_reset": "users_quota_reset_daily",
    "payment": "users_payment_create",
    "loan": "users_loan_create",
    "balance": "users_balance_add",
    "change_plan": "users_change_plan",
    "send_message": "users_send_sms",
    "send_credentials": "users_send_credentials",
    "disconnect": "online_disconnect",
    "status": "users_toggle",
    "delete": "users_delete",
    "rename": "users_update",
    "reset_password": "users_update",
    "edit": "users_update",
    # ويب فقط
    "create": "users_create",
    "temp_speed_cancel": "users_temp_speed_cancel",
    # العمليّات الجماعيّة (تتطلّب bulk.ops فوق الفعل المفرد)
    "bulk_delete": "users_bulk_delete",
    "bulk_status": "users_toggle_bulk",
    "bulk_extend": "users_extend_bulk",
    "bulk_message": "users_send_sms_bulk",
    "bulk_quota": "users_quota_topup_bulk",
    "bulk_balance": "users_balance_add_bulk",
    "bulk_payment": "users_payment_create_bulk",
    "bulk_loan": "users_loan_create_bulk",
}

APP_KEYS = ("extend", "quota", "payment", "loan", "balance", "change_plan",
            "send_message", "send_credentials", "disconnect", "status",
            "delete", "rename", "reset_password", "edit")


def action_flag(key: str, *, is_super: bool, perms, admin_id: Optional[int],
                tenant_id: int) -> bool:
    """علَم فعلٍ واحد (probe بلا تسجيل معدّل). المالك/الشريك = نعم دائمًا."""
    if is_super:
        return True
    ep = WEB_ENDPOINT.get(key)
    if not ep:
        return False
    try:
        from ..routes.blueprint import rbac_denial_status
        if rbac_denial_status(ep, "POST", is_super=False, perms=tuple(perms or ()),
                              admin_id=admin_id, tenant_id=tenant_id,
                              record_activity=False) is not None:
            return False
    except Exception:  # noqa: BLE001 — probe فاشل يُخفي الفعل
        return False
    if key in ("rename", "reset_password"):
        # حقلٌ مقفول بالتحكّم الحقليّ = الفعل مخفيّ (D21: اسم الدخول قابل للمنح).
        try:
            from . import manager_grants as _mg
            field = "username" if key == "rename" else "password"
            return not _mg.field_locked(admin_id, "subscriber", field, tenant_id=tenant_id)
        except Exception:  # noqa: BLE001
            return True
    return True


def action_flags(keys: Iterable[str] = tuple(WEB_ENDPOINT), *, is_super: bool, perms,
                 admin_id: Optional[int], tenant_id: int) -> dict[str, bool]:
    return {k: action_flag(k, is_super=is_super, perms=perms, admin_id=admin_id,
                           tenant_id=tenant_id) for k in keys}


def session_action_flags() -> dict[str, bool]:
    """أعلام المدير الحاليّ في الجلسة — مخبّأة لكل طلب (للقوالب)."""
    from flask import g, session
    cached = getattr(g, "_sub_action_flags", None)
    if cached is not None:
        return cached
    flags = action_flags(
        is_super=bool(session.get("is_super_admin")),
        perms=session.get("permissions") or (),
        admin_id=session.get("admin_id"),
        tenant_id=int(session.get("tenant_id") or 1))
    flags["any_bulk"] = any(v for k, v in flags.items() if k.startswith("bulk_"))
    g._sub_action_flags = flags
    return flags


__all__ = ["WEB_ENDPOINT", "APP_KEYS", "action_flag", "action_flags",
           "session_action_flags"]
