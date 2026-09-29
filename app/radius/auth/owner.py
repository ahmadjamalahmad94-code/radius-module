"""المالك والشريك («شريك/مالك») — مصدرٌ واحد لفحص «هل هذا الحساب بمقام المالك؟».

ثلاث درجات (الأعلى يشمل ما تحته):

1. **المالك الأصليّ** (``is_original_owner``): مالكو لوحة التراخيص المعيَّنون، أو
   أصغر معرّف مدير إن لم تُزامَن مجموعة. **محميّ**: لا يعدّله ولا يعطّله ولا يحذفه
   ولا يغيّر كلمة مروره أحدٌ غيرُه — ولا حتى شريك.
2. **الشريك** (``admins.is_co_owner``، migration 186): «شركاء بالشبكة… كذا حد مالك
   بكل الصلاحيات». يأخذ **كلّ** ما يأخذه المالك: تجاوز RBAC + الأفعال المقصورة
   على المالك (``__super__``: الإبطال، الإعدادات، التوكنات، النسخ الاحتياطيّ، إدارة
   المدراء والأدوار…). يمنحه/يسحبه المالك أو شريكٌ آخر فقط.
3. **«مدير عام / سوبر يوزر»** = دور النظام ``super_admin`` (علَم ``is_super_admin``
   في التطبيق يُسنِد هذا الدور): **كل الصلاحيات غير المقصورة على المالك** — كل
   مفاتيح RBAC + كل المنح الدقيقة (أفعال/رؤية/أقسام) + إدارة المدراء والأدوار
   (عبر ``admins.*``). لا يصل للأفعال المقصورة على المالك أدناه (``OWNER_ONLY``).

``is_owner_like`` هو ما تقرؤه الجلسة (``session["is_super_admin"]``) عبر
``admins_repo.admin_is_owner`` / ``is_primary_owner`` — فالشريك يمرّ في كل مكانٍ
يمرّ فيه المالك تلقائيًّا.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# ── الأفعال المقصورة على المالك/الشريك عمدًا (لا يصلها دور «مدير عام») ──
# (توثيق؛ الإنفاذ في جدولَي _PERM_GUARDED/_NAV_PERM بقيمة "__super__" وفي فحوص
# المسارات. كل endpoint «__super__» غير مذكور في SUPER_DELEGABLE أدناه = مالك فقط.)
OWNER_ONLY: dict[str, str] = {
    "system_settings": "إعدادات النظام (متغيّرات البيئة والأسرار)",
    "backups_*": "النسخ الاحتياطيّ والاستعادة (تحمل كل البيانات والأسرار)",
    "finance_ledger_void": "إبطال قيد ماليّ",
    "credit_dashboard / credit_recharge / سقوف ائتمان المدير": "شحن رصيد المدراء وسقوف الدَّين",
    "data_reset_* / demo_cleanup_* / migration": "تصفير البيانات وترحيلها",
    "cards_reconcile_accounting_*": "تسوية محاسبة البطاقات",
    "tenants_*": "الشبكات (المستأجرون)",
    "license_connect_* / license_file_config / system_update": "الترخيص والتحديث",
    "sections_admin_*": "إدارة أقسام الواجهة",
    "setup_wizard_page": "معالج الإعداد",
    "payments_lab / collection_hub / payment_collection_*": "بوابات الدفع واعتماد التحصيل",
    "vpn_accounts_* / wg_data_* / remote_device_access_* / mt_remote_*": "أنفاق الإدارة والوصول عن بُعد",
    "tool_set_speeds / tool_maintenance / tool_general_adj / tool_test_auth": "أدوات الصيانة الجماعيّة",
    "settings_rotate_store_key": "تدوير مفتاح المتجر",
    "co_owner / is_super_admin": "منح/سحب الشراكة («شريك/مالك») ودور «مدير عام»",
}

# ── endpointات «__super__» التي يصلها دور «مدير عام» عبر مفتاح RBAC ──
# منطقٌ لا جدول: الحارس يقبل صاحب المفتاح بدل «المالك فقط». مع حماية التصعيد في
# المسارات نفسها (لا يُسند دورًا يفوق صلاحياته، ولا يمسّ حسابات المالك/الشركاء).
SUPER_DELEGABLE: dict[str, str] = {
    "admins_create": "admins.create",
    "admins_update": "admins.edit",
    "admins_delete": "admins.delete",
    "roles_list": "admins.view",
    "roles_new": "admins.edit",
    "roles_edit": "admins.edit",
    "roles_grants": "admins.edit",
    "roles_create": "admins.edit",
    "roles_update": "admins.edit",
    "roles_save": "admins.edit",
    "roles_grants_save": "admins.edit",
    "roles_delete": "admins.delete",
}


def super_delegate_perm(endpoint: str) -> Optional[str]:
    """مفتاح RBAC البديل لـendpoint مقصور على المالك يصله «مدير عام»، أو None."""
    name = endpoint.split(".", 1)[1] if endpoint.startswith("radius.") else endpoint
    return SUPER_DELEGABLE.get(name)


def _current_admin_id() -> Optional[int]:
    try:
        from flask import session
        aid = session.get("admin_id")
        return int(aid) if aid else None
    except Exception:  # noqa: BLE001 — خارج سياق طلب
        return None


def _admin_id_of(admin: Any) -> Optional[int]:
    """معرّف الحساب من كائن ``Admin`` أو رقم أو None (= مدير الجلسة الحاليّ)."""
    if admin is None:
        return _current_admin_id()
    if isinstance(admin, bool):
        return None
    if isinstance(admin, int):
        return admin or None
    aid = getattr(admin, "id", None)
    try:
        return int(aid) if aid else None
    except (TypeError, ValueError):
        return None


def is_owner_like(admin: Any = None) -> bool:
    """هل الحساب بمقام المالك (مالك أصليّ أو شريك)؟ — المسند الواحد للّوحة والـAPI.

    يقبل كائن ``Admin`` أو معرّفًا رقميًّا أو لا شيء (= مدير الجلسة الحاليّ).
    يُقرأ من قاعدة البيانات دائمًا (``admins_repo.is_primary_owner``: مجموعة مالكي
    لوحة التراخيص/أصغر معرّف، أو ``admins.is_co_owner`` لحسابٍ مفعَّل غير محذوف)،
    فلا يمنح علَمُ ``is_super_admin`` الخام ولا دور «مدير عام» مقامَ المالك.
    False بأمان عند أيّ خطأ."""
    if admin is not None and not isinstance(admin, int) and getattr(admin, "id", None) is None:
        return False            # كائن بلا معرّف (probe) ليس مالكًا
    aid = _admin_id_of(admin)
    if not aid:
        return False
    try:
        from ..db.repos import admins_repo
        return bool(admins_repo.is_primary_owner(int(aid)))
    except Exception:  # noqa: BLE001
        return False


def is_owner_like_id(admin_id: Optional[int]) -> bool:
    """``is_owner_like`` بالمعرّف (False لحسابٍ مفقود)."""
    if not admin_id:
        return False
    return is_owner_like(int(admin_id))


def is_original_owner(admin_id: Optional[int]) -> bool:
    try:
        from ..db.repos import admins_repo
        return bool(admins_repo.is_original_owner(admin_id))
    except Exception:  # noqa: BLE001
        return False


def is_co_owner(admin_id: Optional[int]) -> bool:
    try:
        from ..db.repos import admins_repo
        return bool(admins_repo.is_co_owner(admin_id))
    except Exception:  # noqa: BLE001
        return False


class OwnerGuardError(PermissionError):
    """رفضٌ برسالة عربيّة (403) لتعديل حساب مالك/شريك أو تصعيد صلاحيات."""


def _perms_of(admin_id: int) -> frozenset[str]:
    from ..db.repos import admins_repo
    a = admins_repo.get_admin(int(admin_id))
    return frozenset(admins_repo.admin_permissions(a)) if a else frozenset()


def _role_perms(role_id: int) -> frozenset[str]:
    from ..core.types import Admin
    from ..db.repos import admins_repo
    probe = Admin(id=None, username="", password_hash="", role_id=int(role_id))
    return frozenset(admins_repo.admin_permissions(probe))


def assert_can_modify_admin(actor_id: Optional[int], target_id: int, *,
                            deleting: bool = False,
                            new_role_id: Optional[int] = None,
                            co_owner_change: bool = False,
                            super_change: bool = False) -> None:
    """حُرّاس إدارة حسابات المدراء (ويب + API) — يرفع ``OwnerGuardError``.

    • المالك الأصليّ لا يمسّه أحدٌ غيره (تعديل/تعطيل/كلمة مرور/حذف/خفض).
    • حساب بمقام المالك (شريك) لا يمسّه إلّا مالكٌ أو شريك.
    • منح/سحب الشراكة أو «مدير عام» = المالك أو الشريك فقط.
    • غير المالك لا يُسند دورًا يحمل صلاحيةً لا يملكها هو (لا تصعيد).
    ``actor_id=None`` = اعتماد رئيسيّ غير مربوط بحساب (توكن بيئة) = مالك."""
    actor_owner = actor_id is None or is_owner_like(actor_id)
    tid = int(target_id)
    is_self = actor_id is not None and int(actor_id) == tid
    if actor_id is not None and not is_self and is_original_owner(tid):
        if deleting:
            raise OwnerGuardError("لا يمكن حذف المالك الأصليّ للشبكة.")
        raise OwnerGuardError("حساب المالك الأصليّ محميّ — لا يعدّله إلّا صاحبه.")
    if (co_owner_change or super_change) and not actor_owner:
        raise OwnerGuardError(
            "منح صلاحيات المالك (شريك) أو «مدير عام» مقصورٌ على المالك أو الشريك.")
    if not actor_owner and not is_self and is_owner_like(tid):
        raise OwnerGuardError("لا يمكنك تعديل حساب المالك أو الشريك.")
    if new_role_id and not actor_owner:
        from ..db.repos import admins_repo
        if admins_repo.get_role(int(new_role_id)) is None:
            raise OwnerGuardError("الدور المحدد غير موجود.")
        missing = sorted(_role_perms(int(new_role_id)) - _perms_of(int(actor_id)))
        if missing:
            raise OwnerGuardError(
                "لا يمكنك إسناد دورٍ يحمل صلاحيات لا تملكها: " + "، ".join(missing[:6]))


def assert_role_within_actor(actor_id: Optional[int], perms: Iterable[str]) -> None:
    """غير المالك لا يحفظ دورًا بصلاحياتٍ لا يملكها (لا تصعيد عبر محرّر الأدوار)."""
    if actor_id is None or is_owner_like(actor_id):
        return
    missing = sorted(set(perms or ()) - set(_perms_of(int(actor_id))))
    if missing:
        raise OwnerGuardError(
            "لا يمكنك منح صلاحيات لا تملكها: " + "، ".join(missing[:6]))


def can_modify_admin(actor: Any, target: Any) -> bool:
    """هل يجوز لـ``actor`` تعديل/تعطيل/خفض/تغيير كلمة مرور/حذف ``target``؟
    (صيغة الكائنات — ``assert_can_modify_admin`` هي صيغة المعرّفات مع الرسائل.)

    • المالك الأصليّ لا يمسّه إلّا هو نفسه — ولا حتى شريك أو مالكٌ أصليّ آخر.
    • حسابٌ بمقام المالك (شريك) لا يمسّه إلّا مالكٌ أو شريك.
    • غير ذلك مسموح هنا (تبقى فحوص الصلاحيات/التصعيد على المسار).
    ``actor=None`` = اعتمادٌ رئيسيّ غير مربوط بحساب (توكن بيئة) = مقام المالك."""
    if target is None:
        return True
    tid = _admin_id_of(target)
    if not tid:
        return True
    aid = None if actor is None else _admin_id_of(actor)
    if actor is not None and not aid:
        return False            # فاعلٌ مجهول لا يمسّ أحدًا
    if aid is not None and int(aid) == int(tid):
        return True             # حسابه هو
    if aid is not None and is_original_owner(tid):
        return False
    if not is_owner_like(tid):
        return True
    return aid is None or is_owner_like(aid)


__all__ = [
    "OWNER_ONLY", "SUPER_DELEGABLE", "super_delegate_perm",
    "is_owner_like", "is_owner_like_id", "is_original_owner", "is_co_owner",
    "OwnerGuardError", "assert_can_modify_admin", "assert_role_within_actor",
    "can_modify_admin",
]
