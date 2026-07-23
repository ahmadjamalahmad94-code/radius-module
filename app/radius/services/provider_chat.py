"""MT32 — شات المزوّد ↔ الشبكة.

خيطٌ واحد لكل شبكة: طرفاه ``provider`` (لوحة المزوّد) و``network`` (لوحة
الشبكة). رسائل نصّية خالصة، بلا مال ولا مرفقات — التحديث بـpolling خفيف
عبر ``after_id``.

**العزل**: كل دالة تأخذ ``tenant_id`` صراحةً وتُقيّد به كل استعلام. لا
توجد دالة تقرأ «كل الرسائل» إلا ``unread_by_tenant`` (للمزوّد وحده،
وتُرجع أعدادًا لا محتوى).
"""
from __future__ import annotations

from typing import Any, Optional

from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict

__all__ = ["ProviderChatError", "post_message", "list_messages", "mark_read",
           "unread_count", "unread_by_tenant", "last_activity_by_tenant"]

SENDERS = ("provider", "network")
_MAX_BODY = 4000
_MAX_LIMIT = 300


class ProviderChatError(ValueError):
    """خطأ تحقّق آمن (رسالة عربية تُعرَض للمستخدم)."""


def _row(row) -> dict[str, Any]:
    """صفٌّ + ``created_local`` (توقيت اللوحة) — يستهلكه القالب والـJS معًا،
    فلا يَعرض أحدهما UTC خامًّا والآخر محلّيًّا."""
    out = row_to_dict(row)
    try:
        from ..core.system_config import to_local
        out["created_local"] = to_local(out.get("created_at") or "")
    except Exception:  # noqa: BLE001
        out["created_local"] = out.get("created_at") or ""
    return out


def post_message(*, tenant_id: int, sender: str, body: str,
                 sender_name: str = "", attachment: dict[str, Any] | None = None
                 ) -> dict[str, Any]:
    """يُضيف رسالة للخيط. الطرف المُرسِل يقرأ رسالته تلقائيًّا.

    ``attachment`` (اختياريّ): dict بـpath/name/mime/size من ``save_attachment``.
    مع مرفقٍ يُسمَح بجسمٍ فارغ (صورةٌ بلا تعليق رسالةٌ صحيحة)."""
    if sender not in SENDERS:
        raise ProviderChatError("طرف غير معروف.")
    text = (body or "").strip()[:_MAX_BODY]
    if not text and not attachment:
        raise ProviderChatError("لا يمكن إرسال رسالة فارغة.")
    tid = int(tenant_id)
    if tid <= 0:
        raise ProviderChatError("الشبكة غير محدّدة.")
    att = attachment or {}
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO provider_chat_messages"
            " (tenant_id, sender, sender_name, body, created_at,"
            "  read_by_provider, read_by_network,"
            "  attachment_path, attachment_name, attachment_mime, attachment_size)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, sender, (sender_name or "")[:120], text, now_iso(),
             1 if sender == "provider" else 0,
             1 if sender == "network" else 0,
             str(att.get("path", ""))[:300], str(att.get("name", ""))[:200],
             str(att.get("mime", ""))[:100], int(att.get("size", 0) or 0)))
        mid = int(cur.lastrowid)
    row = db().execute(
        "SELECT * FROM provider_chat_messages WHERE id=? AND tenant_id=?",
        (mid, tid)).fetchone()
    return _row(row)


def list_messages(*, tenant_id: int, after_id: int = 0,
                  limit: int = 100) -> list[dict[str, Any]]:
    """رسائل خيط شبكةٍ واحدة، تصاعديًّا. ``after_id`` للـpolling."""
    tid = int(tenant_id)
    lim = max(1, min(int(limit or 100), _MAX_LIMIT))
    rows = db().execute(
        "SELECT * FROM provider_chat_messages WHERE tenant_id=? AND id>? "
        "ORDER BY id ASC LIMIT ?", (tid, int(after_id or 0), lim)).fetchall()
    return [_row(r) for r in rows]


def mark_read(*, tenant_id: int, side: str) -> int:
    """يُعلّم رسائل الطرف الآخر مقروءةً لهذا الطرف. يُرجع عدد ما تغيّر."""
    if side not in SENDERS:
        raise ProviderChatError("طرف غير معروف.")
    col = "read_by_provider" if side == "provider" else "read_by_network"
    with transaction() as conn:
        cur = conn.execute(
            f"UPDATE provider_chat_messages SET {col}=1 "
            f"WHERE tenant_id=? AND {col}=0", (int(tenant_id),))
        return int(cur.rowcount or 0)


def unread_count(*, tenant_id: int, side: str) -> int:
    col = "read_by_provider" if side == "provider" else "read_by_network"
    row = db().execute(
        f"SELECT COUNT(*) AS n FROM provider_chat_messages "
        f"WHERE tenant_id=? AND {col}=0", (int(tenant_id),)).fetchone()
    return int(row["n"] if row else 0)


def unread_by_tenant() -> dict[int, int]:
    """للمزوّد وحده: عدد غير المقروء لكل شبكة (أعداد لا محتوى)."""
    out: dict[int, int] = {}
    for r in db().execute(
            "SELECT tenant_id, COUNT(*) AS n FROM provider_chat_messages "
            "WHERE read_by_provider=0 GROUP BY tenant_id"):
        out[int(r["tenant_id"])] = int(r["n"])
    return out


def last_activity_by_tenant() -> dict[int, str]:
    """للمزوّد: آخر وقت رسالة لكل شبكة (لترتيب القائمة)."""
    out: dict[int, str] = {}
    for r in db().execute(
            "SELECT tenant_id, MAX(created_at) AS t FROM provider_chat_messages "
            "GROUP BY tenant_id"):
        out[int(r["tenant_id"])] = str(r["t"] or "")
    return out


def thread_summary(tenant_id: int) -> dict[str, Any]:
    """ملخّص خيط شبكة: العدد الكليّ وآخر رسالة."""
    tid = int(tenant_id)
    total = db().execute(
        "SELECT COUNT(*) AS n FROM provider_chat_messages WHERE tenant_id=?",
        (tid,)).fetchone()
    last: Optional[Any] = db().execute(
        "SELECT * FROM provider_chat_messages WHERE tenant_id=? "
        "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    return {"total": int(total["n"] if total else 0),
            "last": _row(last) if last else None}


# ── مرفقات (MT43) ──────────────────────────────────────────────────
# مبدأ الأمن: المستخدم لا يَختار المسار ولا اسم الملفّ على القرص. نحن
# نُولّد اسمًا عشوائيًّا بامتدادٍ من allowlist، ونُخزّنه تحت مجلّدٍ باسم
# tenant_id. المسار المحفوظ في القاعدة نسبيّ، والتقديم يَقرؤه من القاعدة
# لا من الطلب — فلا اجتياز مسار ولا وصول عبر الشبكات.

import os as _os
import secrets as _secrets
from pathlib import Path as _Path

# نوع → امتداد آمن. لا HTML/SVG/JS (تُنفَّذ في المتصفّح)، لا تنفيذيّات.
_ALLOWED_MIME = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "application/pdf": ".pdf",
    "text/plain": ".txt", "application/zip": ".zip",
}
_MAX_ATTACH_BYTES = 8 * 1024 * 1024   # ٨ م.ب — يكفي لصورة/مستند دعم


def _attach_root(tenant_id: int) -> _Path:
    from ..db.connection import db_path
    d = _Path(db_path()).parent / "chat-attachments" / str(int(tenant_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_attachment(*, tenant_id: int, file_storage) -> dict[str, Any]:
    """يَحفظ ملفًّا مُرفَقًا ويُعيد {path,name,mime,size} للـpost_message.

    ``file_storage`` = werkzeug FileStorage من request.files. يَرفض ما
    خرج عن allowlist النوع أو تجاوز الحجم — بأخطاء عربيّة صريحة."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ProviderChatError("لا ملفّ مُرفَق.")
    mime = (file_storage.mimetype or "").split(";")[0].strip().lower()
    ext = _ALLOWED_MIME.get(mime)
    if not ext:
        raise ProviderChatError("نوع الملفّ غير مسموح. المسموح: صور، PDF، نصّ، ZIP.")

    data = file_storage.read()
    size = len(data)
    if size == 0:
        raise ProviderChatError("الملفّ فارغ.")
    if size > _MAX_ATTACH_BYTES:
        raise ProviderChatError("حجم الملفّ يتجاوز ٨ ميغابايت.")

    fname = _secrets.token_hex(16) + ext           # اسمٌ نَختاره نحن لا المستخدم
    dest = _attach_root(tenant_id) / fname
    with open(dest, "wb") as fh:
        fh.write(data)

    orig = _os.path.basename(str(file_storage.filename))[:200]  # للعرض فقط
    return {"path": fname, "name": orig, "mime": mime, "size": size}


def attachment_fspath(*, tenant_id: int, stored_path: str) -> Optional[_Path]:
    """المسار الفعليّ لمرفقٍ للتقديم — أو None إن خرج عن مجلّد الشبكة.

    حارس اجتياز المسار: نُطبّع الناتج ونَتأكّد أنّه يقع **داخل** جذر
    مرفقات الشبكة. أيّ «..» أو مسار مطلق يَسقط هنا."""
    name = _os.path.basename((stored_path or "").strip())   # يُجرّد أيّ مسار
    if not name:
        return None
    root = _attach_root(tenant_id).resolve()
    fp = (root / name).resolve()
    if root not in fp.parents:
        return None
    return fp if fp.is_file() else None


def message_by_id(*, tenant_id: int, message_id: int) -> Optional[dict[str, Any]]:
    row = db().execute(
        "SELECT * FROM provider_chat_messages WHERE id=? AND tenant_id=?",
        (int(message_id), int(tenant_id))).fetchone()
    return _row(row) if row else None
