"""قراءة/كتابة الـ admin الحالي من الـ session."""
from __future__ import annotations

from typing import Optional

from flask import session

from ..core.types import Admin
from ..stores.admins_store import AdminsStore
from ..stores.tenants_store import TenantsStore


def _resolve_is_super(admin: Admin) -> bool:
    """قيمة ``session["is_super_admin"]`` = **مبدأ التجاوز** في اللوحة.

    قرار المالك: المبدأ الوحيد غير المقيَّد هو «المالك الرئيسي» (الحساب
    الجذر، ``primary_admin_id()``) — تجاوزٌ كامل لحُرّاس RBAC وغير محدود
    على السقوف. عمدًا **لا يكفي** علم ``is_super_admin`` وحده: دور
    «super_admin» القابل للإسناد (وأيّ تجاوز من لوحة التراخيص يَضبط العلم)
    لم يَعُد يَمنح التجاوز — بل يَمرّ صاحبه عبر فحوصات الصلاحيات العاديّة
    ويَخضع للسقوف كأيّ مدير.

    المالك = ضمن مجموعة المالكين المعيَّنة من لوحة التراخيص (username/email،
    عدّة مالكين)، أو — إن لم تُزامَن مجموعة بعد — الحساب الجذر (أصغر معرّف admin).

    fail-safe: إن تعذّر الاستعلام (خطأ قاعدة عابر) نَسقط لعلم ``is_super_admin``
    كي لا نَحبس المالك (الذي يحمله) خارج لوحته — يُعالَج داخل ``admin_is_owner``."""
    try:
        from ..db.repos import admins_repo
        return admins_repo.admin_is_owner(admin)
    except Exception:  # noqa: BLE001 — لا نكسر الدخول
        return bool(getattr(admin, "is_super_admin", False))


def set_current_admin(admin: Admin, tenant_id: int) -> None:
    session["admin_id"] = admin.id
    session["admin_user"] = admin.username
    session["admin_name"] = admin.full_name or admin.username
    session["is_super_admin"] = _resolve_is_super(admin)
    session["tenant_id"] = tenant_id
    # لغة الواجهة المفضّلة للمسؤول (i18n) — يقرأها منتقي اللغة في الأولوية 2.
    # '' = لا تفضيل، فيسقط المنتقي للإعداد العام ثم العربية.
    session["admin_locale"] = getattr(admin, "locale", "") or ""
    # ختم الجلسة: نسخة session_epoch لحظة الدخول. الحارس يقارنه كل طلب، فأي
    # تغيير لكلمة المرور (يزيد الختم) يقتل هذه الجلسة وكل الجلسات الأخرى.
    try:
        from ..db.repos import admins_repo
        session["admin_sv"] = admins_repo.session_epoch(admin.id) or 0
    except Exception:  # noqa: BLE001 — لا نكسر الدخول إن تعذّر الاستعلام
        session["admin_sv"] = 0
    # حمّل صلاحيات الدور في الجلسة حتى تعمل فحوصات RBAC (الحارس + SafetyGate).
    try:
        from ..services.admins import get_admins_service
        session["permissions"] = list(get_admins_service().permissions_of(admin))
    except Exception:
        session["permissions"] = []
    session.permanent = True


def clear_current_admin() -> None:
    for k in ("admin_id", "admin_user", "admin_name", "is_super_admin",
              "tenant_id", "permissions", "admin_locale", "admin_sv"):
        session.pop(k, None)


def session_still_valid() -> bool:
    """هل جلسة الكوكي الحاليّة ما زالت مشروعة؟

    الجلسة كوكي موقَّع يعيش في متصفّح المستخدم، فلا يُنهيه حذف الحساب ولا
    تغيير كلمة المرور من تلقاء نفسه. لذا نتحقّق خادميًّا كل طلب من:
    الحساب موجود + غير محذوف + مفعَّل + ختمه يطابق ما حُفظ لحظة الدخول.

    fail-open عند خطأ قاعدة عابر فقط (كي لا يُحبس المالك خارج لوحته)."""
    aid = current_admin_id()
    if not aid:
        return False
    try:
        from ..db.repos import admins_repo
        current = admins_repo.session_epoch(aid)
    except Exception:  # noqa: BLE001 — خطأ قاعدة عابر لا يطرد الجميع
        return True
    if current is None:          # محذوف أو معطَّل
        return False
    return int(session.get("admin_sv") or 0) == int(current)


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


def is_network_owner() -> bool:
    """MT30 — «مالك الشبكة»: المدير الذي يُنشأ مع الشبكة (دور network_admin).

    هو المبدأ **غير المقيَّد داخل شبكته**: يدير موزّعيه ومدراءه وكروته
    وأرصدته بلا حاجة لمنح دقيق (الصلاحيات الدقيقة تبقى لمن يُنشئهم هو).
    المالك الرئيسي/السوبر → True دائمًا. لا يمنح أي وصول خارج الشبكة —
    العزل بين الشبكات يتكفّل به حارس العضوية وتقييد الاستعلامات بالجهة.
    """
    if is_super_admin():
        return True
    try:
        from ..db.repos import admins_repo
        a = current_admin()
        if not a or not getattr(a, "role_id", None):
            return False
        r = admins_repo.get_role(int(a.role_id))
        return bool(r and r.name == "network_admin")
    except Exception:  # noqa: BLE001
        return False


def admin_tenants() -> list:
    """يُرجع كل الـ tenants التي للأدمن صلاحية فيها."""
    if is_super_admin():
        return TenantsStore.instance().list()
    aid = current_admin_id()
    if not aid:
        return []
    return TenantsStore.instance().tenants_for_admin(aid)
