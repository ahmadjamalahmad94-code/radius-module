"""شات المتجر المتقدّم — رسائل خفيفة بين الزبون (صفحة المتجر) والمدير
(لوحة سوق البطاقات). نص + إرفاق صورة (وصل/مشكلة)، بلا أي مال — رسائل
خالصة. التحديث بـpolling خفيف عبر معرّف آخر رسالة (after_id).

الخدمة لا تحفظ الصور بنفسها — نقطة الـAPI تتولّى الرفع وتمرّر مسارًا
محفوظًا مسبقًا (image_path). كل الاستعلامات مُحدّدة بـself.tenant_id
وتستخدم معاملات SQL (لا حقن قيم نصيًا).
"""
from __future__ import annotations

from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import EventService


__all__ = ["StoreChatError", "StoreChatService"]


VALID_SENDERS = ("customer", "admin")
_MAX_BODY = 2000      # سقف طول الرسالة (يُقتَطع لا يُرفض)
_MAX_LIMIT = 200      # سقف صفحة التحميل


class StoreChatError(ValueError):
    """Raised for safe store-chat validation errors (Arabic messages)."""


def _image_url(image_path: str) -> str:
    """يبني رابط عرض الصورة من مسارها المخزّن. الصور تُحفَظ تحت static،
    فإن كان المسار مطلقًا (يبدأ بـ/ أو static/) نتركه، وإلا نسبقه بـ/static/."""
    path = str(image_path or "").strip()
    if not path:
        return ""
    if path.startswith("/") or path.startswith("static/"):
        return path if path.startswith("/") else "/" + path
    return "/static/" + path


def _row(row) -> dict[str, Any]:
    out = row_to_dict(row)
    out["image_url"] = _image_url(out.get("image_path") or "")
    return out


class StoreChatService:
    """طبقة خدمة شات المتجر — كل العمليات مُحدّدة بالمستأجر."""

    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.events = EventService()

    def post_message(
        self,
        *,
        card_user_id: int,
        sender: str,
        body: str = "",
        image_path: str = "",
        admin_actor: str = "",
    ) -> dict[str, Any]:
        """يدرج رسالة واحدة في خيط الزبون. يتطلّب نصًا أو صورة على الأقل،
        ويقصّ النص عند 2000 حرف. الزبون يقرأ رسالته تلقائيًا (وتبقى غير
        مقروءة للمدير) والعكس صحيح. تسجيل الحدث أفضل-جهد لرسائل الزبون."""
        snd = str(sender or "").strip().lower()
        if snd not in VALID_SENDERS:
            raise StoreChatError("مرسِل غير صالح.")
        text = str(body or "").strip()
        img = str(image_path or "").strip()
        if not text and not img:
            raise StoreChatError("اكتب رسالة أو أرفق صورة.")
        if len(text) > _MAX_BODY:
            text = text[:_MAX_BODY]
        if snd == "customer":
            read_by_admin, read_by_customer = 0, 1
        else:
            read_by_admin, read_by_customer = 1, 0
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO store_chat_messages(
                    tenant_id, card_user_id, sender, body, image_path,
                    admin_actor, read_by_admin, read_by_customer, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    int(card_user_id),
                    snd,
                    text,
                    img,
                    str(admin_actor or ""),
                    read_by_admin,
                    read_by_customer,
                    now,
                ),
            )
            message_id = int(cur.lastrowid)
        if snd == "customer":
            try:
                self.events.record_event(
                    tenant_id=self.tenant_id,
                    category="card",
                    event_key="store_chat.customer_message",
                    message="أرسل زبون رسالة في شات المتجر.",
                    target_type="card_user",
                    target_id=int(card_user_id),
                )
            except Exception:  # noqa: BLE001 — تسجيل الحدث لا يكسر إرسال الرسالة
                pass
            # تنبيه المالك برسالة دعم جديدة — مجمَّع لكل زبون (تنبيه واحد
            # للخيط يتجدّد بالرسائل اللاحقة) فلا إغراق.
            try:
                from .store_alerts import notify_chat
                notify_chat(self.tenant_id, int(card_user_id))
            except Exception:  # noqa: BLE001
                pass
        else:
            # ردّ المدير على الخيط ⇒ حُلّ تنبيه الشات لهذا الزبون.
            try:
                from .store_alerts import resolve_chat
                resolve_chat(self.tenant_id, int(card_user_id))
            except Exception:  # noqa: BLE001
                pass
        return self._get(message_id)

    def list_thread(
        self,
        *,
        card_user_id: int,
        after_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """يعيد رسائل الخيط تصاعديًا بالمعرّف، فقط ما هو أحدث من after_id
        (للـpolling الخفيف). الشكل: {items, last_id}."""
        after = max(0, int(after_id or 0))
        cap = max(1, min(_MAX_LIMIT, int(limit or 100)))
        rows = db().execute(
            """
            SELECT * FROM store_chat_messages
            WHERE tenant_id=? AND card_user_id=? AND id>?
            ORDER BY id ASC LIMIT ?
            """,
            (self.tenant_id, int(card_user_id), after, cap),
        ).fetchall()
        items = [_row(row) for row in rows]
        last_id = items[-1]["id"] if items else after
        return {"items": items, "last_id": int(last_id)}

    def mark_read(self, *, card_user_id: int, reader: str) -> int:
        """يُعلّم رسائل الطرف الآخر كمقروءة. القارئ admin يقرأ رسائل
        customer والعكس. يعيد عدد الصفوف المتأثّرة."""
        rdr = str(reader or "").strip().lower()
        if rdr not in VALID_SENDERS:
            raise StoreChatError("قارئ غير صالح.")
        if rdr == "admin":
            sql = (
                "UPDATE store_chat_messages SET read_by_admin=1 "
                "WHERE tenant_id=? AND card_user_id=? AND sender='customer' "
                "AND read_by_admin=0"
            )
        else:
            sql = (
                "UPDATE store_chat_messages SET read_by_customer=1 "
                "WHERE tenant_id=? AND card_user_id=? AND sender='admin' "
                "AND read_by_customer=0"
            )
        with transaction() as conn:
            cur = conn.execute(sql, (self.tenant_id, int(card_user_id)))
            affected = int(cur.rowcount or 0)
        # قراءة المدير لخيط الزبون ⇒ حُلّ تنبيه الشات (عولج الخيط).
        if rdr == "admin":
            try:
                from .store_alerts import resolve_chat
                resolve_chat(self.tenant_id, int(card_user_id))
            except Exception:  # noqa: BLE001
                pass
        return affected

    def unread_for_customer(self, *, card_user_id: int) -> int:
        """عدد رسائل المدير غير المقروءة لدى هذا الزبون (شارة الإشعار)."""
        row = db().execute(
            """
            SELECT COUNT(*) AS n FROM store_chat_messages
            WHERE tenant_id=? AND card_user_id=? AND sender='admin'
              AND read_by_customer=0
            """,
            (self.tenant_id, int(card_user_id)),
        ).fetchone()
        return int(row["n"] if row else 0)

    def list_threads(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """صندوق وارد المدير — صفّ لكل زبون له رسائل، مع اسم الزبون وجواله،
        وقت/نص آخر رسالة، الإجمالي، وعدد غير المقروء للمدير. الأحدث أولًا."""
        cap = max(1, min(_MAX_LIMIT, int(limit or 100)))
        rows = db().execute(
            """
            SELECT
                m.card_user_id                       AS card_user_id,
                cu.display_name                      AS display_name,
                cu.mobile                            AS mobile,
                MAX(m.created_at)                    AS last_message_at,
                (SELECT lm.body FROM store_chat_messages lm
                  WHERE lm.tenant_id=m.tenant_id
                    AND lm.card_user_id=m.card_user_id
                  ORDER BY lm.id DESC LIMIT 1)        AS last_body,
                COUNT(*)                             AS total_count,
                SUM(CASE WHEN m.sender='customer' AND m.read_by_admin=0
                         THEN 1 ELSE 0 END)          AS unread_admin_count
            FROM store_chat_messages m
            LEFT JOIN card_users cu
              ON cu.tenant_id=? AND cu.id=m.card_user_id
            WHERE m.tenant_id=?
            GROUP BY m.card_user_id, cu.display_name, cu.mobile
            ORDER BY last_message_at DESC
            LIMIT ?
            """,
            (self.tenant_id, self.tenant_id, cap),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = row_to_dict(row)
            item["total_count"] = int(item.get("total_count") or 0)
            item["unread_admin_count"] = int(item.get("unread_admin_count") or 0)
            out.append(item)
        return out

    def thread_for_admin(
        self,
        *,
        card_user_id: int,
        after_id: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """عرض خيط الزبون للمدير — نفس شكل list_thread (تفويض مباشر)."""
        return self.list_thread(
            card_user_id=card_user_id, after_id=after_id, limit=limit
        )

    # ───────────────────────── internals ─────────────────────────
    def _get(self, message_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM store_chat_messages WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(message_id)),
        ).fetchone()
        if not row:
            raise StoreChatError("الرسالة غير موجودة.")
        return _row(row)
