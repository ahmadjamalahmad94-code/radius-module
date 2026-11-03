"""«مالك الواجهة» — مَن يملك واجهةً (front / tenant) غير الواجهة الأساسيّة للخادم.

قرار المالك 02/11/2026 (فرح نت، واجهة «كافي ١»): أدمن الواجهة يدير واجهته كاملةً،
ومنها الحذف النهائيّ (بلا رجعة) للحزم والبطاقات — داخل واجهته فقط. لا يصير مالكًا
للخادم ولا شريكًا (هذا يكشف له كلّ الواجهات)، ولا يمسّ الواجهة الأساسيّة أبدًا.

التعيين صريح لا مُستنتَج: ``tenant_settings(tenant_id, 'front_owner_admin_ids')`` =
معرّفات مفصولة بفواصل. يكون المدير مالك الواجهة T حين يجتمع:
  * T ليست الواجهة الأساسيّة (المعرّف 1) — تلك لمالك الخادم وحده؛
  * معرّفه في قائمة T؛
  * حسابه مفعَّل وغير محذوف؛
  * له عضويّة ``active`` في T.
أيّ خطأ في القراءة → False (الرفض هو الأمان).
"""
from __future__ import annotations

from typing import Optional

SETTING_KEY = "front_owner_admin_ids"
PRIMARY_TENANT_ID = 1


def front_owner_ids(tenant_id: int) -> set[int]:
    """المعرّفات المعيَّنة مالكين للواجهة ``tenant_id`` (مجموعة فارغة إن لم يُعيَّن أحد)."""
    from ..db.repos import tenants_repo
    raw = tenants_repo.get_setting(int(tenant_id), SETTING_KEY, "") or ""
    out: set[int] = set()
    for part in raw.replace(" ", "").split(","):
        if part.isdigit() and int(part) > 0:
            out.add(int(part))
    return out


def is_front_owner(admin_id: Optional[int], tenant_id: Optional[int]) -> bool:
    """هل ``admin_id`` مالك الواجهة ``tenant_id``؟ (False للواجهة الأساسيّة دائمًا)."""
    try:
        aid, tid = int(admin_id or 0), int(tenant_id or 0)
        if aid <= 0 or tid <= PRIMARY_TENANT_ID:
            return False
        if aid not in front_owner_ids(tid):
            return False
        from ..db.connection import db
        conn = db()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(admins)")}
        deleted = " AND deleted_at IS NULL" if "deleted_at" in cols else ""
        if conn.execute("SELECT 1 FROM admins WHERE id=? AND enabled=1" + deleted, (aid,)).fetchone() is None:
            return False
        return conn.execute(
            "SELECT 1 FROM tenant_memberships WHERE admin_id=? AND tenant_id=? AND status='active'",
            (aid, tid),
        ).fetchone() is not None
    except Exception:  # noqa: BLE001 — الرفض هو الأمان
        return False


def session_can_delete_permanently() -> bool:
    """الحذف النهائيّ في الواجهة الحاليّة: مالك الخادم/الشريك، أو مالك هذه الواجهة."""
    from flask import has_request_context, session
    if not has_request_context():
        return False
    if session.get("is_super_admin"):
        return True
    # المالك 03/11: صلاحية «حذف نهائيّ» في المسمّى الوظيفي تكفي (لا مالكَ فقط)
    if "cards.purge" in (session.get("permissions") or ()):
        return True
    return is_front_owner(session.get("admin_id"), session.get("tenant_id"))
