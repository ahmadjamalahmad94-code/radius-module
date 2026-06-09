"""مستودع جلسات الدفع (payment_checkouts) — مختبر الدفع الإلكتروني.

يخزّن جلسة دفع واحدة لكل عملية شراء عبر مزوّد دفع (محاكاة اليوم،
بوابات حقيقية لاحقًا). المبالغ بأصغر وحدة عملة (amount_minor) كأعداد
صحيحة. رمز التحقق لا يُخزَّن نصًا — sha256 فقط في otp_hash؛ المحاكاة
تكشف الرمز للوحة المختبر عبر metadata_json (وسم demo_otp واضح).

لا يكتب هذا المستودع أي قيد محاسبي ولا يستدعي AccountingService —
الترحيل المالي الحقيقي يأتي مع ربط مزوّد فعلي (انظر payments_lab).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso

CHECKOUT_STATUSES = {"pending", "otp_sent", "paid", "failed", "expired"}

# صلاحية رمز التحقق — 5 دقائق (عقد المحاكاة؛ المزوّد الحقيقي يفرض مدّته)
OTP_TTL_MINUTES = 5


def hash_otp(otp: str) -> str:
    """sha256 لرمز التحقق — لا يُخزَّن الرمز نصًا في عمود otp_hash أبدًا."""
    return hashlib.sha256((otp or "").strip().encode("utf-8")).hexdigest()


def new_reference(prefix: str = "CHK") -> str:
    """مرجع جلسة فريد قصير قابل للقراءة (للفواتير والمطابقة)."""
    return f"{prefix}-{secrets.token_hex(5).upper()}"


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["metadata"] = json_load(d.get("metadata_json"), {}) or {}
    return d


class PaymentCheckoutRepository:
    """CRUD جلسات الدفع — كل القراءات والكتابات تمرّ من هنا."""

    def create(self, *, tenant_id: int, provider: str, reference: str,
               amount_minor: int, currency: str,
               subscriber_username: Optional[str] = None,
               status: str = "pending",
               otp_hash: Optional[str] = None,
               otp_expires_at: Optional[str] = None,
               metadata: Optional[dict[str, Any]] = None) -> dict:
        if status not in CHECKOUT_STATUSES:
            raise ValueError("status")
        amount_minor = int(amount_minor)
        if amount_minor <= 0:
            raise ValueError("amount_minor")
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO payment_checkouts(
                    tenant_id, provider, reference, subscriber_username,
                    amount_minor, currency, status, otp_hash, otp_expires_at,
                    created_at, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(tenant_id), provider, reference,
                    (subscriber_username or None),
                    amount_minor, (currency or "ILS").upper()[:8],
                    status, otp_hash, otp_expires_at,
                    now_iso(), json_dump(metadata or {}),
                ),
            )
            row = conn.execute(
                "SELECT * FROM payment_checkouts WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        return _row_to_dict(row)

    def get(self, tenant_id: int, checkout_id: int) -> Optional[dict]:
        row = db().execute(
            "SELECT * FROM payment_checkouts WHERE tenant_id = ? AND id = ?",
            (int(tenant_id), int(checkout_id)),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_reference(self, reference: str) -> Optional[dict]:
        row = db().execute(
            "SELECT * FROM payment_checkouts WHERE reference = ?",
            (reference,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list(self, tenant_id: int, *, status: str = "", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM payment_checkouts WHERE tenant_id = ?"
        params: list[Any] = [int(tenant_id)]
        if status:
            if status not in CHECKOUT_STATUSES:
                raise ValueError("status")
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = db().execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def set_otp(self, checkout_id: int, *, otp_hash: str,
                ttl_minutes: int = OTP_TTL_MINUTES,
                metadata: Optional[dict[str, Any]] = None) -> None:
        """يسجّل رمز تحقق جديد (إرسال/إعادة إرسال) وينقل الحالة otp_sent."""
        expires = (datetime.utcnow() + timedelta(minutes=int(ttl_minutes))).isoformat() + "Z"
        with transaction() as conn:
            if metadata is not None:
                conn.execute(
                    "UPDATE payment_checkouts SET otp_hash = ?, otp_expires_at = ?, "
                    "status = 'otp_sent', metadata_json = ? WHERE id = ?",
                    (otp_hash, expires, json_dump(metadata), int(checkout_id)),
                )
            else:
                conn.execute(
                    "UPDATE payment_checkouts SET otp_hash = ?, otp_expires_at = ?, "
                    "status = 'otp_sent' WHERE id = ?",
                    (otp_hash, expires, int(checkout_id)),
                )

    def update_status(self, checkout_id: int, status: str, *,
                      paid: bool = False) -> None:
        if status not in CHECKOUT_STATUSES:
            raise ValueError("status")
        with transaction() as conn:
            if paid:
                # عند الدفع نمسح otp_hash — انتهى دوره ولا داعي لإبقائه
                conn.execute(
                    "UPDATE payment_checkouts SET status = ?, paid_at = ?, "
                    "otp_hash = NULL WHERE id = ?",
                    (status, now_iso(), int(checkout_id)),
                )
            else:
                conn.execute(
                    "UPDATE payment_checkouts SET status = ? WHERE id = ?",
                    (status, int(checkout_id)),
                )

    def merge_metadata(self, checkout_id: int, patch: dict[str, Any]) -> None:
        """يدمج مفاتيح جديدة في metadata_json (قراءة-تعديل-كتابة بسيطة).

        قيمة None في الـ patch تعني حذف المفتاح — يُستخدم لمسح demo_otp
        بعد الدفع حتى لا يبقى الرمز في البيانات الوصفية.
        """
        with transaction() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM payment_checkouts WHERE id = ?",
                (int(checkout_id),),
            ).fetchone()
            meta = json_load(row["metadata_json"] if row else None, {}) or {}
            for key, value in (patch or {}).items():
                if value is None:
                    meta.pop(key, None)
                else:
                    meta[key] = value
            conn.execute(
                "UPDATE payment_checkouts SET metadata_json = ? WHERE id = ?",
                (json_dump(meta), int(checkout_id)),
            )

    def expire_stale(self, tenant_id: int) -> int:
        """يحوّل كل جلسة otp_sent انتهت صلاحية رمزها إلى expired (تنظيف كسول)."""
        now = now_iso()
        with transaction() as conn:
            cur = conn.execute(
                "UPDATE payment_checkouts SET status = 'expired' "
                "WHERE tenant_id = ? AND status IN ('pending','otp_sent') "
                "AND otp_expires_at IS NOT NULL AND otp_expires_at < ?",
                (int(tenant_id), now),
            )
        return cur.rowcount or 0


__all__ = [
    "PaymentCheckoutRepository",
    "CHECKOUT_STATUSES",
    "OTP_TTL_MINUTES",
    "hash_otp",
    "new_reference",
]
