"""fix3 (F01 F10 / F07 M2) — «رؤية كل حِزم البطاقات» مسندٌ واحد للويب والـAPI.

كان يُطبَّق في قائمة الحِزم على الويب وحدها؛ فالعناوين المباشرة
(``/cards/batches/<id>/…``) والـAPI (``/api/v1/cards/batches…``) و«آخر الحزم» في
لوحة التحكّم تُظهر حِزم كل المدراء والموزّعين.

القاعدة:
  1. المالك/الشريك أو اعتمادٌ رئيسيّ غير مربوط → الكل.
  2. «عرض كل حزم البطاقات» الفعّال (``can_view_all_card_batches`` ← مفتاح الدور
     ``scope.view_all_cards``) → الكل — إلّا لدخول الموزّع فهو مقصور دائمًا.
  3. غير ذلك: حِزمه (``manager_id``) ∪ حِزم موزّعيه (``distributors.admin_id``)
     ∪ حِزمٌ أنشأها دخول موزّعٍ يملكه ∪ — إن كان الحساب نفسه دخولَ موزّع —
     حِزم موزّعه والحِزم المُسنَدة إليه.
"""
from __future__ import annotations

from typing import Optional


def can_view_all_card_batches(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    if not admin_id:
        return False
    from ..auth.owner import is_owner_like
    if is_owner_like(int(admin_id)):
        return True
    from .subscriber_scope import is_distributor_login
    if is_distributor_login(admin_id, tenant_id=tenant_id):
        return False
    try:
        from .manager_distributor_ops import ManagerDistributorOpsService
        return bool(ManagerDistributorOpsService(tenant_id=int(tenant_id or 1)).has_permission(
            entity_type="manager", entity_id=int(admin_id),
            permission="can_view_all_card_batches"))
    except Exception:  # noqa: BLE001 — fail-closed
        return False


def batch_scope_admin_id(admin_id: Optional[int] = None, *, tenant_id: int = 1) -> Optional[int]:
    """None = يرى كل الحِزم؛ وإلّا معرّف المدير. ``admin_id=None`` = مدير الطلب."""
    if admin_id is None:
        from .subscriber_scope import request_admin_id, request_is_owner_session
        if request_is_owner_session():
            return None
        admin_id = request_admin_id()
    if not admin_id:
        return None
    if can_view_all_card_batches(int(admin_id), tenant_id=tenant_id):
        return None
    return int(admin_id)


def batch_scope_clause(scope: int, *, alias: str = "b") -> tuple[str, list]:
    """شرط SQL (بلا « AND» في أوّله) على جدول card_batches باسم ``alias``."""
    a = alias
    o = int(scope)
    clause = (
        f"({a}.manager_id = ?"
        f" OR {a}.manager_id IN (SELECT dl.login_admin_id FROM distributors dl"
        f" WHERE dl.admin_id = ? AND dl.login_admin_id IS NOT NULL)"
        f" OR {a}.distributor_id IN (SELECT dd.id FROM distributors dd"
        f" WHERE dd.tenant_id = {a}.tenant_id AND (dd.admin_id = ? OR dd.login_admin_id = ?))"
        f" OR {a}.id IN (SELECT ca.batch_id FROM card_batch_assignments ca"
        f" JOIN distributors da ON da.tenant_id = ca.tenant_id AND da.id = ca.distributor_id"
        f" WHERE ca.tenant_id = {a}.tenant_id AND ca.status = 'assigned'"
        f" AND da.login_admin_id = ?))"
    )
    return clause, [o, o, o, o, o]


def batch_scope_sql(*, alias: str = "b", column: Optional[str] = None,
                    scope: Optional[int] = None, tenant_id: int = 1,
                    use_request: bool = True) -> tuple[str, list]:
    """`` AND <شرط>`` لقصر استعلامٍ على حِزم المدير، أو ``("", [])`` حين يرى الكل.
    ``column`` = عمود معرّف الحزمة في جدولٍ آخر (مثل ``cards.batch_id``)."""
    if scope is None and use_request:
        scope = batch_scope_admin_id(None, tenant_id=tenant_id)
    if scope is None:
        return "", []
    if column:
        clause, vals = batch_scope_clause(scope, alias="sb")
        return (f" AND {column} IN (SELECT sb.id FROM card_batches sb WHERE sb.tenant_id = ?"
                f" AND {clause})", [int(tenant_id), *vals])
    clause, vals = batch_scope_clause(scope, alias=alias)
    return " AND " + clause, vals


def batch_accessible(batch_id, admin_id: Optional[int] = None, *, tenant_id: int = 1) -> bool:
    """هل يرى المدير هذه الحزمة؟ حزمةٌ غير موجودة → True (يُرجع المسار 404)."""
    scope = batch_scope_admin_id(admin_id, tenant_id=tenant_id)
    if scope is None:
        return True
    from ..db.connection import db
    try:
        bid = int(batch_id)
    except (TypeError, ValueError):
        return True
    exists = db().execute("SELECT 1 FROM card_batches WHERE tenant_id = ? AND id = ?",
                          (int(tenant_id), bid)).fetchone()
    if not exists:
        return True
    clause, vals = batch_scope_clause(scope, alias="b")
    row = db().execute(
        "SELECT 1 FROM card_batches b WHERE b.tenant_id = ? AND b.id = ? AND " + clause,
        [int(tenant_id), bid, *vals]).fetchone()
    return row is not None


OUT_OF_SCOPE_BATCH_AR = "هذه الحزمة ليست ضمن نطاقك (حزم مدير أو موزّع آخر)."

__all__ = ["can_view_all_card_batches", "batch_scope_admin_id", "batch_scope_clause",
           "batch_scope_sql", "batch_accessible", "OUT_OF_SCOPE_BATCH_AR"]
