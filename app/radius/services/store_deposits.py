"""store_deposits — الدفع/الشحن شبه الآلي للمتجر المتقدّم.

نموذج الثقة: الزبون يحوّل المال يدويًا إلى محفظة المدير (جوالي باي /
بنك / PalPay) ويرفع وصلًا ضمن «طلب إيداع» (deposit_requests). المدير
يتحقّق من حسابه ثم:
  • يؤكّد → يُضاف المبلغ المدّعى إلى محفظة الزبون آليًا.
  • يعتمد مبلغًا مختلفًا (adjusted) → يُضاف المبلغ الفعلي الواصل.
  • يرفض → لا حركة مال.

⚠️ لا حركة مال تلقائية: الرصيد يُضاف **فقط** عند تأكيد المدير، عبر
خدمة الرصيد الموجودة (WalletService.credit — نفس آلية شحن البطاقة)،
مع أثر تدقيق (قيد + حدث) و**idempotency** صارمة: تحويل الحالة من
pending ذرّيًا بحارس rowcount قبل أي ائتمان، فلا يُضاف الرصيد مرتين
مهما تكرّر نداء التأكيد أو تسابق مديران.

محافظ الاستلام (store_payment_methods) كلها بيانات المدير المركزية —
يعرضها المتجر للزبون لينسخ الرقم/يمسح QR قبل التحويل.
"""
from __future__ import annotations

from typing import Any

from ..core.system_config import default_currency
from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import (
    EventService,
    WalletService,
    minor_to_money,
    money_to_minor,
)
from .card_users_marketplace import CardUsersMarketplaceService
from .store_uploads import store_image_url


class StoreDepositError(ValueError):
    """خطأ تحقّق آمن في الإيداع أو محافظ الاستلام (رسائل عربية)."""


VALID_METHODS = ("jawaly_pay", "bank", "palpay", "other")

_METHOD_AR = {
    "jawaly_pay": "جوالي باي",
    "bank": "تحويل بنكي",
    "palpay": "PalPay",
    "other": "قناة أخرى",
}

_STATUS_AR = {
    "pending": "بانتظار المراجعة",
    "confirmed": "مؤكَّد — أُضيف الرصيد",
    "adjusted": "مؤكَّد بمبلغ معدَّل",
    "rejected": "مرفوض",
}


class DepositRequestService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = WalletService()
        self.events = EventService()

    # ───────────────────────── محافظ الاستلام (إعدادات المدير) ─────────────────────────

    def _method_row(self, row) -> dict[str, Any]:
        out = row_to_dict(row)
        out["method_ar"] = _METHOD_AR.get(str(out.get("method") or "other"),
                                          out.get("method"))
        out["qr_image_url"] = store_image_url(out.get("qr_image_path") or "")
        out["logo_image_url"] = store_image_url(out.get("logo_image_path") or "")
        return out

    def list_payment_methods(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM store_payment_methods WHERE tenant_id=?"
        params: list[Any] = [self.tenant_id]
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY sort_order ASC, id ASC"
        return [self._method_row(r) for r in db().execute(sql, tuple(params)).fetchall()]

    def public_payment_methods(self) -> list[dict[str, Any]]:
        """شكل القنوات الذي يعرضه المتجر للزبون — بلا حقول إدارية."""
        out = []
        for m in self.list_payment_methods(active_only=True):
            out.append({
                "id": int(m.get("id") or 0),
                "method": str(m.get("method") or "other"),
                "method_ar": m.get("method_ar"),
                "label": str(m.get("label") or m.get("method_ar") or ""),
                "account_name": str(m.get("account_name") or ""),
                "account_number": str(m.get("account_number") or ""),
                "instructions": str(m.get("instructions") or ""),
                "qr_image_url": m.get("qr_image_url") or "",
                "logo_image_url": m.get("logo_image_url") or "",
            })
        return out

    def get_payment_method(self, method_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM store_payment_methods WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(method_id)),
        ).fetchone()
        if not row:
            raise StoreDepositError("قناة الاستلام غير موجودة.")
        return self._method_row(row)

    def create_payment_method(
        self, *, method: str, label: str, account_name: str = "",
        account_number: str = "", instructions: str = "",
        qr_image_path: str = "", logo_image_path: str = "", sort_order: int = 0,
    ) -> dict[str, Any]:
        m = str(method or "other").strip().lower()
        if m not in VALID_METHODS:
            m = "other"
        if not str(label or "").strip():
            raise StoreDepositError("اسم القناة مطلوب.")
        now = now_iso()
        cur = db().execute(
            """
            INSERT INTO store_payment_methods(
                tenant_id, method, label, account_name, account_number,
                instructions, qr_image_path, logo_image_path, active,
                sort_order, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (self.tenant_id, m, str(label).strip(), str(account_name or ""),
             str(account_number or ""), str(instructions or ""),
             str(qr_image_path or ""), str(logo_image_path or ""), 1,
             int(sort_order or 0), now, now),
        )
        return self.get_payment_method(int(cur.lastrowid))

    def update_payment_method(self, method_id: int, **fields: Any) -> dict[str, Any]:
        self.get_payment_method(method_id)  # وجود + نطاق المستأجر
        allowed = ("method", "label", "account_name", "account_number",
                   "instructions", "qr_image_path", "logo_image_path",
                   "active", "sort_order")
        sets, params = [], []
        for key in allowed:
            if key in fields and fields[key] is not None:
                val = fields[key]
                if key == "method":
                    val = str(val).strip().lower()
                    if val not in VALID_METHODS:
                        val = "other"
                elif key in ("active", "sort_order"):
                    val = int(val)
                else:
                    val = str(val)
                sets.append(f"{key}=?")
                params.append(val)
        if sets:
            sets.append("updated_at=?")
            params.append(now_iso())
            params.extend([self.tenant_id, int(method_id)])
            db().execute(
                f"UPDATE store_payment_methods SET {', '.join(sets)} "
                "WHERE tenant_id=? AND id=?",
                tuple(params),
            )
        return self.get_payment_method(method_id)

    def delete_payment_method(self, method_id: int) -> None:
        self.get_payment_method(method_id)
        db().execute(
            "DELETE FROM store_payment_methods WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(method_id)),
        )

    # ───────────────────────── طلبات الإيداع ─────────────────────────

    def _row(self, row) -> dict[str, Any]:
        out = row_to_dict(row)
        out["amount_claimed"] = minor_to_money(out.get("amount_claimed_minor"))
        if out.get("confirmed_amount_minor") is not None:
            out["confirmed_amount"] = minor_to_money(out.get("confirmed_amount_minor"))
        else:
            out["confirmed_amount"] = None
        out["status_ar"] = _STATUS_AR.get(str(out.get("status") or ""),
                                          out.get("status"))
        out["method_ar"] = _METHOD_AR.get(str(out.get("method") or "other"),
                                          out.get("method"))
        out["receipt_image_url"] = store_image_url(out.get("receipt_image_path") or "")
        return out

    def _wallet(self, card_user_id: int) -> dict[str, Any]:
        # نفس محفظة سوق البطاقات (تُنشأ إن غابت) — مصدر واحد للرصيد.
        return CardUsersMarketplaceService(
            tenant_id=self.tenant_id
        )._wallet_for_card_user(int(card_user_id))  # noqa: SLF001

    def create_request(
        self, *, card_user_id: int, amount_claimed: Any,
        method: str = "", payer_phone: str = "", reference: str = "",
        payer_name: str = "", receipt_image_path: str = "",
        payment_method_id: int | None = None, currency: str = "",
    ) -> dict[str, Any]:
        """ينشئ طلب إيداع بحالة pending — لا حركة مال. يتحقّق من المبلغ
        ووجود مستخدم البطاقة. لا يضيف رصيدًا (يُضاف عند التأكيد فقط)."""
        # تأكيد وجود مستخدم البطاقة (يرفع إن غاب) — نطاق المستأجر.
        CardUsersMarketplaceService(tenant_id=self.tenant_id).get_card_user(int(card_user_id))
        amount_minor = money_to_minor(amount_claimed)
        if amount_minor <= 0:
            raise StoreDepositError("أدخل مبلغًا صحيحًا أكبر من صفر.")
        m = str(method or "other").strip().lower()
        if m not in VALID_METHODS:
            m = "other"
        cur_code = str(currency or default_currency()).upper()[:8]
        now = now_iso()
        cur = db().execute(
            """
            INSERT INTO deposit_requests(
                tenant_id, card_user_id, method, payment_method_id,
                payer_phone, reference, payer_name, amount_claimed_minor,
                receipt_image_path, status, currency, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (self.tenant_id, int(card_user_id), m,
             (int(payment_method_id) if payment_method_id else None),
             str(payer_phone or ""), str(reference or ""), str(payer_name or ""),
             amount_minor, str(receipt_image_path or ""), "pending",
             cur_code, now),
        )
        request_id = int(cur.lastrowid)
        self.events.record_event(
            tenant_id=self.tenant_id, category="financial",
            event_key="store.deposit_requested",
            message="طلب إيداع جديد بانتظار مراجعة المدير.",
            actor_type="card_user", actor_id=int(card_user_id),
            target_type="card_user", target_id=int(card_user_id),
            metadata={"deposit_request_id": request_id,
                      "amount_minor": amount_minor, "method": m},
        )
        return self.get(request_id)

    def get(self, request_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM deposit_requests WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(request_id)),
        ).fetchone()
        if not row:
            raise StoreDepositError("طلب الإيداع غير موجود.")
        return self._row(row)

    def list_requests(self, *, status: str = "", card_user_id: int | None = None,
                      limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM deposit_requests WHERE tenant_id=?"
        params: list[Any] = [self.tenant_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        if card_user_id:
            sql += " AND card_user_id=?"
            params.append(int(card_user_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [self._row(r) for r in db().execute(sql, tuple(params)).fetchall()]

    def list_for_customer(self, *, card_user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """طلبات إيداع الزبون بحالتها — الزبون يرى طلباته هو فقط."""
        return self.list_requests(card_user_id=int(card_user_id), limit=limit)

    def pending_count(self) -> int:
        row = db().execute(
            "SELECT COUNT(*) n FROM deposit_requests WHERE tenant_id=? AND status='pending'",
            (self.tenant_id,),
        ).fetchone()
        return int(row["n"] or 0)

    def confirm(self, request_id: int, *, actor: str = "admin",
                confirmed_amount: Any = None, note: str = "") -> dict[str, Any]:
        """يؤكّد الإيداع ويُضيف الرصيد آليًا. confirmed_amount=None ⇒
        يُضاف المبلغ المدّعى (status=confirmed)؛ خلافه ⇒ المبلغ الفعلي
        (status=adjusted). idempotent: التحويل من pending ذرّيٌّ بحارس
        rowcount قبل أي ائتمان، فالائتمان يحدث مرة واحدة فقط."""
        req = self.get(request_id)
        if req["status"] != "pending":
            # محسوم سابقًا → لا تكرار ائتمان (idempotent).
            return req
        if confirmed_amount is None:
            amount_minor = int(req["amount_claimed_minor"])
            final_status = "confirmed"
        else:
            amount_minor = money_to_minor(confirmed_amount)
            final_status = "adjusted"
        if amount_minor <= 0:
            raise StoreDepositError("المبلغ المعتمد يجب أن يكون أكبر من صفر.")
        now = now_iso()
        # (1) المطالبة الذرّية: pending → الحالة النهائية بحارس rowcount.
        #     من يفلح هنا وحده يُكمل الائتمان؛ أي نداء/مدير آخر يجد
        #     rowcount=0 فيعيد الحالة الراهنة دون ائتمان ثانٍ.
        with transaction() as conn:
            claimed = conn.execute(
                """
                UPDATE deposit_requests
                SET status=?, confirmed_amount_minor=?, admin_note=?,
                    resolved_by=?, resolved_at=?
                WHERE tenant_id=? AND id=? AND status='pending'
                """,
                (final_status, amount_minor, str(note or ""),
                 str(actor or "admin"), now, self.tenant_id, int(request_id)),
            )
            if claimed.rowcount != 1:
                return self.get(request_id)
        # (2) الائتمان (معاملة مستقلة في خدمة الرصيد). عند أي فشل: نعيد
        #     الطلب إلى pending فيقدر المدير يعيد المحاولة بلا ائتمان معلّق.
        try:
            wallet = self._wallet(int(req["card_user_id"]))
            credit = self.wallets.credit(
                tenant_id=self.tenant_id, wallet_id=int(wallet["id"]),
                amount=minor_to_money(amount_minor),
                actor_type="admin", actor_id=None,
                reference_type="store_deposit", reference_id=int(request_id),
                notes=f"تأكيد إيداع المتجر #{request_id} بواسطة {actor}",
                metadata={"deposit_request_id": int(request_id),
                          "status": final_status},
            )
        except Exception:
            db().execute(
                """
                UPDATE deposit_requests
                SET status='pending', confirmed_amount_minor=NULL,
                    resolved_by='', resolved_at=NULL
                WHERE tenant_id=? AND id=?
                """,
                (self.tenant_id, int(request_id)),
            )
            raise
        # (3) ربط حركة الائتمان (أثر تدقيق) + حدث + إشعار الزبون.
        db().execute(
            "UPDATE deposit_requests SET wallet_transaction_id=? WHERE tenant_id=? AND id=?",
            (int(credit["transaction"]["id"]), self.tenant_id, int(request_id)),
        )
        self.events.record_event(
            tenant_id=self.tenant_id, category="financial",
            event_key="store.deposit_confirmed",
            message=f"تأكيد إيداع وإضافة {minor_to_money(amount_minor)} للمحفظة.",
            actor_type="admin", target_type="card_user",
            target_id=int(req["card_user_id"]),
            metadata={"deposit_request_id": int(request_id),
                      "amount_minor": amount_minor, "status": final_status,
                      "wallet_transaction_id": int(credit["transaction"]["id"])},
        )
        _notify_customer(
            self.tenant_id, int(req["card_user_id"]),
            f"تم تأكيد إيداعك وإضافة {minor_to_money(amount_minor)} "
            f"{req.get('currency') or ''} إلى رصيدك. شكرًا لك.",
        )
        return self.get(request_id)

    def reject(self, request_id: int, *, actor: str = "admin",
               note: str = "") -> dict[str, Any]:
        """يرفض الطلب — لا حركة مال. idempotent (حارس pending)."""
        now = now_iso()
        with transaction() as conn:
            claimed = conn.execute(
                """
                UPDATE deposit_requests
                SET status='rejected', admin_note=?, resolved_by=?, resolved_at=?
                WHERE tenant_id=? AND id=? AND status='pending'
                """,
                (str(note or ""), str(actor or "admin"), now,
                 self.tenant_id, int(request_id)),
            )
        if claimed.rowcount != 1:
            return self.get(request_id)
        req = self.get(request_id)
        self.events.record_event(
            tenant_id=self.tenant_id, category="financial",
            event_key="store.deposit_rejected",
            message="رُفض طلب إيداع المتجر.",
            actor_type="admin", target_type="card_user",
            target_id=int(req["card_user_id"]),
            metadata={"deposit_request_id": int(request_id)},
        )
        _notify_customer(
            self.tenant_id, int(req["card_user_id"]),
            "نعتذر، لم نتمكّن من تأكيد إيداعك"
            + (f" — {note}" if note else "") + ". تواصل معنا للمساعدة.",
        )
        return req


def _notify_customer(tenant_id: int, card_user_id: int, message: str) -> None:
    """إشعار خفيف للزبون يظهر في شات المتجر (رسالة من المدير/النظام).
    أفضل-جهد: لا يكسر العملية المالية أبدًا إن فشل (الجدول من
    migration 109؛ نكتب مباشرةً تفاديًا للاقتران بخدمة الشات)."""
    try:
        db().execute(
            """
            INSERT INTO store_chat_messages(
                tenant_id, card_user_id, sender, body, admin_actor,
                read_by_admin, read_by_customer, created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (int(tenant_id), int(card_user_id), "admin", str(message),
             "النظام", 1, 0, now_iso()),
        )
    except Exception:  # noqa: BLE001 — الإشعار لا يكسر التأكيد
        pass


__all__ = [
    "StoreDepositError",
    "DepositRequestService",
    "VALID_METHODS",
]
