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
    """المدير خلف الطلب الحاليّ: اعتماد الـAPI (``g.admin_id``؛ 0 = اعتماد رئيسيّ
    غير مربوط ⇒ None = الكل) وإلّا جلسة الويب. خارج طلب ⇒ None."""
    try:
        from flask import g, has_request_context, session
        if not has_request_context():
            return None
        if getattr(g, "_api_authed", False):
            aid = int(getattr(g, "admin_id", 0) or 0)
            return aid or None
        aid = session.get("admin_id")
        return int(aid) if aid else None
    except Exception:  # noqa: BLE001
        return None


request_admin_id = _current_admin_id


def request_is_owner_session() -> bool:
    """A WEB request whose session is owner-level (``session['is_super_admin']``
    — set at login by ``_resolve_is_super``: owner / co-owner). The panel guard
    already bypasses on it; every scope helper must agree with the guard."""
    try:
        from flask import g, has_request_context, session
        if not has_request_context() or getattr(g, "_api_authed", False):
            return False
        return bool(session.get("is_super_admin"))
    except Exception:  # noqa: BLE001
        return False


def is_distributor_login(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    """هل هذا الحساب هو نفسه موزّع (``distributors.login_admin_id``)؟"""
    if not admin_id:
        return False
    try:
        from ..db.repos import operations_repo
        return operations_repo.get_distributor_by_admin(int(tenant_id), int(admin_id)) is not None
    except Exception:  # noqa: BLE001
        return False


def can_view_all_subscribers(admin_id: Optional[int], *, tenant_id: int = 1) -> bool:
    """«عرض كل المشتركين» الفعّال: مالك/شريك، أو تجاوز المدير > مفتاح الدور > لا.
    دخول الموزّع مقصورٌ دائمًا على ما يخصّه (حتى بدور «مدير عام») — نفس قاعدة الـAPI."""
    if not admin_id:
        return False
    from ..auth.owner import is_owner_like
    if is_owner_like(int(admin_id)):
        return True
    if is_distributor_login(admin_id, tenant_id=tenant_id):
        return False
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
    if admin_id is None and request_is_owner_session():
        return None
    aid = admin_id if admin_id is not None else _current_admin_id()
    if not aid:
        return None
    if can_view_all_subscribers(int(aid), tenant_id=tenant_id):
        return None
    return int(aid)


def owner_scope_clause(admin_id: int, *, tenant_id: int = 1) -> tuple[str, list]:
    """شرط SQL (يبدأ بـ `` AND``) لنطاق المدير على جدول subscribers — المسند
    الواحد ``subscribers_repo._owner_scope_sql`` (يشمل حِزم دخول الموزّع)."""
    from ..db.repos.subscribers_repo import _owner_scope_sql
    return _owner_scope_sql(int(admin_id))


def current_scope_admin_id(*, tenant_id: int = 1) -> Optional[int]:
    """نطاق الطلب الحاليّ (ويب أو API): None = يرى الكل، وإلّا معرّف المدير."""
    return scope_admin_id(None, tenant_id=tenant_id)


def scoped_subscribers_subquery(scope: Optional[int], *, tenant_id: int = 1,
                                select: str = "username") -> tuple[str, list]:
    """``SELECT <select> FROM subscribers WHERE tenant_id=? AND <النطاق>`` —
    لربط أيّ جدول (جلسات/مدفوعات/دفتر/أحداث/إشعارات…) بمشتركي المدير."""
    clause, vals = owner_scope_clause(int(scope), tenant_id=tenant_id)
    return (f"SELECT {select} FROM subscribers WHERE tenant_id = ?" + clause,
            [int(tenant_id), *vals])


def scope_sql(column: str, *, by: str = "username", scope: Optional[int] = None,
              tenant_id: int = 1, use_request: bool = True) -> tuple[str, list]:
    """الشرط الموحّد لقصر أيّ استعلام على مشتركي المدير.

    ``column`` = عمود الجدول الذي يشير للمشترك (``username`` أو ``s.id``…)،
    ``by`` = ``username`` | ``id`` (ما يطابقه العمود في subscribers).
    يُرجع ``("", [])`` حين يرى المدير الكل (مالك/شريك/«عرض كل المشتركين»).
    ``scope=None`` + ``use_request`` = نطاق الطلب الحاليّ."""
    if scope is None and use_request:
        scope = current_scope_admin_id(tenant_id=tenant_id)
    if scope is None:
        return "", []
    sub, vals = scoped_subscribers_subquery(scope, tenant_id=tenant_id, select=by)
    return f" AND {column} IN ({sub})", vals


def entity_scope_sql(type_col: str, id_col: str, *, actor_type_col: str = "",
                     actor_id_col: str = "", scope=None, tenant_id: int = 1,
                     use_request: bool = True) -> tuple[str, list]:
    """Business-OS rows keyed by (entity type, id) — wallets, ledger_entries,
    business_events: a scoped manager sees his subscribers' rows, his own
    (manager) rows, his distributors' rows and — when given — rows he acted in.
    ``("", [])`` when he sees everything."""
    if scope is None and use_request:
        scope = current_scope_admin_id(tenant_id=tenant_id)
    if scope is None:
        return "", []
    o = int(scope)
    sub, sv = scoped_subscribers_subquery(o, tenant_id=tenant_id, select="id")
    parts = [f"({type_col} = 'subscriber' AND {id_col} IN ({sub}))",
             f"({type_col} IN ('manager', 'admin') AND {id_col} = ?)",
             f"({type_col} = 'distributor' AND {id_col} IN (SELECT dx.id FROM distributors dx"
             " WHERE dx.admin_id = ? OR dx.login_admin_id = ?))"]
    vals: list = [*sv, o, o, o]
    if actor_type_col and actor_id_col:
        parts.append(f"({actor_type_col} IN ('manager', 'admin') AND {actor_id_col} = ?)")
        vals.append(o)
    return " AND (" + " OR ".join(parts) + ")", vals


def admin_actor_labels(admin_id: int) -> list[str]:
    """How an admin appears in ``audit_log.actor``: his username / display name
    (web) and ``api-token:<id>`` of every token he minted (API/app)."""
    labels: list[str] = []
    try:
        from ..db.connection import db
        row = db().execute("SELECT username, full_name FROM admins WHERE id = ?",
                           (int(admin_id),)).fetchone()
        if row:
            labels += [str(v) for v in (row["username"], row["full_name"]) if v]
        for t in db().execute("SELECT id FROM api_tokens WHERE created_by = ?",
                              (int(admin_id),)).fetchall():
            labels.append(f"api-token:{int(t['id'])}")
    except Exception:  # noqa: BLE001
        pass
    return sorted(set(labels)) or ["\x00"]


def audit_scope_sql(*, scope=None, tenant_id: int = 1, use_request: bool = True,
                    target_type_col: str = "target_type", target_id_col: str = "target_id",
                    actor_col: str = "actor") -> tuple[str, list]:
    """``audit_log`` rows a scoped manager may read: events about HIS subscribers
    (``target_type`` user/subscriber → username) and events HE performed."""
    if scope is None and use_request:
        scope = current_scope_admin_id(tenant_id=tenant_id)
    if scope is None:
        return "", []
    sub, sv = scoped_subscribers_subquery(int(scope), tenant_id=tenant_id, select="username")
    labels = admin_actor_labels(int(scope))
    marks = ",".join("?" for _ in labels)
    return (f" AND (({target_type_col} IN ('user', 'subscriber') AND {target_id_col} IN ({sub}))"
            f" OR {actor_col} IN ({marks}))", [*sv, *labels])


def accessible_usernames(scope: int, *, tenant_id: int = 1) -> set[str]:
    """أسماء مشتركي المدير (للفلترة في الذاكرة حين لا يمرّ الاستعلام بـSQL)."""
    from ..db.connection import db
    sql, vals = scoped_subscribers_subquery(int(scope), tenant_id=tenant_id)
    return {str(r[0]) for r in db().execute(sql, vals).fetchall() if r[0]}


_UNSET = object()


def filter_rows(rows, *, key="username", tenant_id: int = 1, scope=_UNSET) -> list:
    """يقصر صفوفًا (dict أو كائنات) على مشتركي مدير الطلب — نفس المسند.
    ``key`` = اسم الحقل الذي يحمل اسم المشترك، أو دالّة."""
    rows = list(rows or [])
    if scope is _UNSET:
        scope = current_scope_admin_id(tenant_id=tenant_id)
    if scope is None:
        return rows
    allowed = accessible_usernames(int(scope), tenant_id=tenant_id)

    def _name(r):
        if callable(key):
            return key(r)
        if isinstance(r, dict):
            return r.get(key)
        try:
            return r[key]
        except (KeyError, IndexError, TypeError):
            return getattr(r, key, None)
    return [r for r in rows if (_name(r) or "") in allowed]


def creator_manager_id(requested: Optional[int], *, creator_admin_id: Optional[int] = None,
                       tenant_id: int = 1) -> Optional[int]:
    """F02 H1 — «المدير المسؤول» لمشتركٍ جديد.

    المُنشئ غير المالك/الشريك يُختَم هو نفسه (دائمًا حين لا يرى الكل، أو حين
    لم يُحدَّد مدير) — فلا يفقد رؤية ما أنشأه أبدًا. دخول الموزّع يُختَم باسمه
    فيراه هو، ويراه مديره المالك عبر سلسلة الموزّعين (``_owner_scope_sql``).
    مديرٌ يرى الكل (مثل «مدير عام») يجوز أن يُسند لمدير آخر صراحةً.
    بلا مُنشئ معروف (مالك/اعتماد رئيسيّ/عمليّة نظام) ⇒ القيمة المطلوبة كما هي."""
    if creator_admin_id is None and request_is_owner_session():
        return requested
    aid = creator_admin_id if creator_admin_id is not None else _current_admin_id()
    if not aid:
        return requested
    try:
        from ..db.repos import admins_repo
        if admins_repo.get_admin(int(aid)) is None:
            return requested            # unknown creator id — never stamp a ghost
    except Exception:  # noqa: BLE001
        return requested
    from ..auth.owner import is_owner_like
    if is_owner_like(int(aid)):
        return requested
    try:
        req = int(requested) if requested not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        req = None
    if req and req != int(aid) and can_view_all_subscribers(int(aid), tenant_id=tenant_id):
        return req
    return int(aid)


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
           "subscriber_accessible", "filter_accessible", "OUT_OF_SCOPE_AR",
           "request_admin_id", "is_distributor_login", "current_scope_admin_id",
           "scoped_subscribers_subquery", "scope_sql", "creator_manager_id"]
