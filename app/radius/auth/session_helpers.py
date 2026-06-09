"""قراءة/كتابة الـ admin الحالي من الـ session."""
from __future__ import annotations

from typing import Optional

from flask import session

from ..core.types import Admin
from ..stores.admins_store import AdminsStore
from ..stores.tenants_store import TenantsStore


def _resolve_is_super(admin: Admin) -> bool:
    """هل يُعامَل هذا المسؤول كـ super؟ يكفي علم admin.is_super_admin، أو
    كونه «المدير الرئيسي» (أصغر معرّف admin = المالك) — «المدير الرئيسي =
    وصول كامل دائماً»، فلا يُحجب أبدًا حتى لو أُلغي العلم سهوًا (تعديل
    يدوي/مزامنة ترخيص). لا نكسر الدخول إن تعذّر الاستعلام — نسقط للعلم."""
    if bool(getattr(admin, "is_super_admin", False)):
        return True
    try:
        from ..db.repos import admins_repo
        pid = admins_repo.primary_admin_id()
        return pid is not None and admin.id == pid
    except Exception:  # noqa: BLE001
        return False


def set_current_admin(admin: Admin, tenant_id: int) -> None:
    session["admin_id"] = admin.id
    session["admin_user"] = admin.username
    session["admin_name"] = admin.full_name or admin.username
    session["is_super_admin"] = _resolve_is_super(admin)
    session["tenant_id"] = tenant_id
    # لغة الواجهة المفضّلة للمسؤول (i18n) — يقرأها منتقي اللغة في الأولوية 2.
    # '' = لا تفضيل، فيسقط المنتقي للإعداد العام ثم العربية.
    session["admin_locale"] = getattr(admin, "locale", "") or ""
    # حمّل صلاحيات الدور في الجلسة حتى تعمل فحوصات RBAC (الحارس + SafetyGate).
    try:
        from ..services.admins import get_admins_service
        session["permissions"] = list(get_admins_service().permissions_of(admin))
    except Exception:
        session["permissions"] = []
    session.permanent = True


def clear_current_admin() -> None:
    for k in ("admin_id", "admin_user", "admin_name", "is_super_admin", "tenant_id", "permissions", "admin_locale"):
        session.pop(k, None)


def current_admin_id() -> Optional[int]:
    aid = session.get("admin_id")
    return int(aid) if aid else None


def current_admin() -> Optional[Admin]:
    aid = current_admin_id()
    if not aid:
        return None
    return AdminsStore.instance().get_admin(aid)


def is_super_admin() -> bool:
    return bool(session.get("is_super_admin"))


def admin_tenants() -> list:
    """يُرجع كل الـ tenants التي للأدمن صلاحية فيها."""
    if is_super_admin():
        return TenantsStore.instance().list()
    aid = current_admin_id()
    if not aid:
        return []
    return TenantsStore.instance().tenants_for_admin(aid)
