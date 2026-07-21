"""MT33 — حذف شبكة (جهة) نهائيًّا من لوحة المزوّد — بحذر.

الحذف هنا **لا رجعة فيه**: يمحو كل صفوف الشبكة من كل جدولٍ يحمل
``tenant_id``، ثم عضوياتها ومدراءها المخصوصين بها وإعداداتها ومراسلاتها،
ثم صفّ الشبكة نفسه. لذلك ثلاث طبقات حماية:

1. **تأكيد نصّي**: على المزوّد كتابة ``slug`` الشبكة حرفيًّا.
2. **نسخة أمان تلقائيّة** قبل أي حذف — تبقى على القرص بعد اختفاء
   الشبكة، فيبقى للتراجع طريق (``tenant-backups/<id>/``).
3. **جهة المزوّد (1) محميّة** — لا تُحذف أبدًا.

المدراء: يُحذف من كانت هذه **عضويّته الوحيدة** (مالك الشبكة ومدراؤها).
من له عضوية في شبكةٍ أخرى يبقى، وتُنزَع عضويّته من المحذوفة فقط —
فلا يفقد المزوّد أو مديرٌ مشترك حسابه بحذف شبكة.
"""
from __future__ import annotations

from typing import Any

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db, transaction

__all__ = ["TenantDeleteError", "preview_tenant_deletion", "delete_tenant"]

# جداول البنية التي نتولّاها يدويًّا بعد مسح جداول البيانات.
_STRUCTURAL = ("tenant_settings", "tenant_memberships", "provider_chat_messages")


class TenantDeleteError(ValueError):
    """خطأ حذفٍ آمن (رسالة عربية تُعرَض للمزوّد)."""


def _tenant_or_raise(tenant_id: int):
    from ..db.repos import tenants_repo
    t = tenants_repo.get_tenant(int(tenant_id))
    if not t:
        raise TenantDeleteError("الشبكة غير موجودة.")
    if int(t.id) == int(DEFAULT_TENANT_ID):
        raise TenantDeleteError("لا يمكن حذف جهة المزوّد الأساسيّة.")
    return t


def preview_tenant_deletion(tenant_id: int) -> dict[str, Any]:
    """ماذا سيُحذف؟ أعدادٌ تُعرَض للمزوّد قبل التأكيد."""
    t = _tenant_or_raise(tenant_id)
    tid = int(t.id)
    c = db()
    counts: dict[str, int] = {}
    for table, label in (("subscribers", "مشترك"), ("cards", "بطاقة"),
                         ("card_batches", "حزمة كروت"), ("access_plans", "باقة"),
                         ("nas_devices", "راوتر"), ("distributors", "موزّع"),
                         ("radacct", "جلسة")):
        try:
            row = c.execute(f"SELECT COUNT(*) n FROM {table} WHERE tenant_id=?",
                            (tid,)).fetchone()
            if row and int(row["n"]):
                counts[label] = int(row["n"])
        except Exception:  # noqa: BLE001 — جدول غائب لا يكسر المعاينة
            continue
    admins = [dict(r) for r in c.execute(
        "SELECT a.id, a.username, a.full_name FROM admins a "
        "JOIN tenant_memberships m ON m.admin_id = a.id WHERE m.tenant_id=?",
        (tid,)).fetchall()]
    doomed = [a for a in admins if _only_membership(int(a["id"]), tid)]
    return {"tenant": t, "counts": counts, "admins": admins,
            "admins_to_delete": doomed}


def _only_membership(admin_id: int, tenant_id: int) -> bool:
    """هل هذه الشبكة هي عضويّته الوحيدة؟"""
    row = db().execute(
        "SELECT COUNT(*) n FROM tenant_memberships WHERE admin_id=? AND tenant_id<>?",
        (int(admin_id), int(tenant_id))).fetchone()
    return int(row["n"] if row else 0) == 0


def delete_tenant(tenant_id: int, *, confirm_slug: str, actor: str = "") -> dict[str, Any]:
    """يحذف الشبكة نهائيًّا بعد أخذ نسخة أمان. يُرجع ملخّص ما حُذف."""
    t = _tenant_or_raise(tenant_id)
    tid = int(t.id)
    if (confirm_slug or "").strip() != t.slug:
        raise TenantDeleteError(
            f"للتأكيد اكتب معرّف الشبكة حرفيًّا: {t.slug}")

    # (2) نسخة أمان قبل أيّ حذف — تبقى بعد اختفاء الشبكة.
    backup_name = ""
    try:
        from . import tenant_backup
        backup_name = tenant_backup.export_tenant(tid, actor=f"{actor} (قبل الحذف)")["name"]
    except Exception as e:  # noqa: BLE001
        raise TenantDeleteError(
            f"تعذّر أخذ نسخة أمان قبل الحذف، فأُلغي الحذف: {e}") from e

    doomed = [int(a["id"]) for a in preview_tenant_deletion(tid)["admins_to_delete"]]
    deleted_rows = 0
    with transaction() as conn:
        # المفاتيح الأجنبيّة مؤجَّلة: نمحو جداول الجهة بأي ترتيب ثم نتّسق
        # عند الـCOMMIT (نفس علاج الاستعادة في tenant_backup).
        conn.execute("PRAGMA defer_foreign_keys = ON")
        from . import tenant_backup
        for table in tenant_backup.tenant_tables():
            cur = conn.execute(f'DELETE FROM "{table}" WHERE tenant_id = ?', (tid,))
            deleted_rows += int(cur.rowcount or 0)
        for table in _STRUCTURAL:
            try:
                cur = conn.execute(f'DELETE FROM "{table}" WHERE tenant_id = ?', (tid,))
                deleted_rows += int(cur.rowcount or 0)
            except Exception:  # noqa: BLE001 — جدول غائب (هجرة لم تُطبَّق)
                continue
        for aid in doomed:
            conn.execute("DELETE FROM admins WHERE id = ?", (aid,))
        conn.execute("DELETE FROM tenants WHERE id = ?", (tid,))

    try:  # كي يتوقّف رابط /<slug>/... فورًا
        from ..middleware.tenant_path import invalidate_slug_cache
        invalidate_slug_cache()
    except Exception:  # noqa: BLE001
        pass
    try:
        from .audit import get_audit_service
        get_audit_service().record(
            actor=actor, action="delete", target_type="tenant", target_id=str(tid),
            payload={"slug": t.slug, "name": t.name, "rows": deleted_rows,
                     "admins_deleted": len(doomed), "safety_backup": backup_name})
    except Exception:  # noqa: BLE001 — سجلّ لا يُسقط عمليّة تمّت
        pass
    return {"slug": t.slug, "name": t.name, "rows": deleted_rows,
            "admins_deleted": len(doomed), "safety_backup": backup_name}
