"""D09 — «هل يرى/يتصرّف هذا المدير بهذا المشترك؟» — مسندٌ واحد للويب والـAPI.

قبل هذا كان نطاق المِلكية يُطبَّق في قائمة الويب وحدها؛ فمديرٌ بلا «عرض كل
المشتركين» يفتح 360/التعديل ويحذف مشترك مديرٍ آخر بالعنوان، والـAPI يُرجع الكل.
الآن كل مسار (قائمة/360/ملف/تعديل/أفعال/بحث الشحن/نظرة عامّة/تصدير، ويب + API)
يسأل هنا.

القاعدة (بالترتيب):
  1. المالك/الشريك (``is_owner_like``) واعتمادٌ رئيسيّ غير مربوط بحساب → الكل.
  2. «عرض كل المشتركين» الفعّال → الكل: التجاوز الفرديّ للمدير (صفحته) إن ضُبط،
     وإلّا مفتاح الدور ``scope.view_all_subscribers`` (دور «مدير عام» يملكه)،
     وإلّا الافتراض (لا).
  3. غير ذلك: مشتركوه (``manager_id``) ∪ مشتركو حِزم موزّعيه (``distributors.admin_id``
     = المدير المالك) ∪ — إن كان الحساب نفسه موزّعًا (``distributors.login_admin_id``)
     — مشتركو الحِزم المُسنَدة إليه.
"""
from __future__ import annotations

from typing import Iterable, Optional


def _current_admin_id() -> Optional[int]:
    try:
        from flask import session
        aid = session.get("admin_id")
        return int(aid) if aid else None
    except Exception:  # noqa: BLE001
        return None


def can_view_all_subscribers(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    """«عرض كل المشتركين» الفعّال: مالك/شريك، أو تجاوز المدير > مفتاح الدور > لا."""
    if not admin_id:
        return False
    from ..auth.owner import is_owner_like
    if is_owner_like(int(admin_id)):
        return True
    try:
        from .manager_distributor_ops import ManagerDistributorOpsService
        return bool(ManagerDistributorOpsService(tenant_id=int(tenant_id or 1)).has_permission(
            entity_type="manager", entity_id=int(admin_id),
            permission="can_view_all_subscribers"))
    except Exception:  # noqa: BLE001 — fail-closed: نطاقه فقط
        return False


def scope_admin_id(admin_id: Optional[int] = None, *, tenant_id: int = 1) -> Optional[int]:
    """معرّف المدير الذي يُقصَر عليه النطاق، أو None = يرى الكل.
    ``admin_id=None`` = المدير الحاليّ في الجلسة (None بلا جلسة = الكل، كما كان)."""
    aid = admin_id if admin_id is not None else _current_admin_id()
    if not aid:
        return None
    if can_view_all_subscribers(int(aid), tenant_id=tenant_id):
        return None
    return int(aid)


def _distributor_batch_ids(admin_id: int, tenant_id: int) -> list[int]:
    """حِزم الموزّع إن كان هذا الحساب هو الموزّع نفسه (login_admin_id)."""
    try:
        from ..db.repos import operations_repo
        dist = operations_repo.get_distributor_by_admin(int(tenant_id), int(admin_id))
        if not dist:
            return []
        return [int(b) for b in operations_repo.assigned_batch_ids(int(tenant_id), int(dist["id"]))]
    except Exception:  # noqa: BLE001
        return []


def owner_scope_clause(admin_id: int, *, tenant_id: int = 1) -> tuple[str, list]:
    """شرط SQL (يبدأ بـ `` AND``) لنطاق المدير على جدول subscribers."""
    from ..db.repos.subscribers_repo import _owner_scope_sql
    clause, vals = _owner_scope_sql(int(admin_id))
    batches = _distributor_batch_ids(int(admin_id), int(tenant_id))
    if batches:
        marks = ",".join("?" for _ in batches)
        clause = clause[:-1] + f" OR card_batch_id IN ({marks}))"
        vals = [*vals, *batches]
    return clause, vals


def subscriber_accessible(admin_id: Optional[int] = None, *, username: str = "",
                          subscriber_id: Optional[int] = None,
                          tenant_id: int = 1) -> bool:
    """هل يرى/يتصرّف المدير بهذا المشترك؟ مشتركٌ غير موجود → True (ليُرجع
    المسار 404 كالعادة، لا 403 يكشف شيئًا)."""
    scope = scope_admin_id(admin_id, tenant_id=tenant_id)
    if scope is None:
        return True
    from ..db.connection import db
    if subscriber_id is not None:
        where, key = "id = ?", int(subscriber_id)
    elif username:
        where, key = "username = ?", str(username)
    else:
        return True
    exists = db().execute(
        f"SELECT 1 FROM subscribers WHERE tenant_id = ? AND {where} LIMIT 1",
        (int(tenant_id), key)).fetchone()
    if not exists:
        return True
    clause, vals = owner_scope_clause(scope, tenant_id=tenant_id)
    row = db().execute(
        f"SELECT 1 FROM subscribers WHERE tenant_id = ? AND {where}" + clause + " LIMIT 1",
        [int(tenant_id), key, *vals]).fetchone()
    return row is not None


def filter_accessible(usernames: Iterable[str], admin_id: Optional[int] = None, *,
                      tenant_id: int = 1) -> list[str]:
    """الأسماء التي يصلها المدير من قائمة (للعمليّات الجماعيّة)."""
    names = [u for u in usernames if u]
    if scope_admin_id(admin_id, tenant_id=tenant_id) is None:
        return names
    return [u for u in names
            if subscriber_accessible(admin_id, username=u, tenant_id=tenant_id)]


OUT_OF_SCOPE_AR = "هذا المشترك ليس ضمن نطاقك (مشتركو مدير آخر)."

__all__ = ["can_view_all_subscribers", "scope_admin_id", "owner_scope_clause",
           "subscriber_accessible", "filter_accessible", "OUT_OF_SCOPE_AR"]
