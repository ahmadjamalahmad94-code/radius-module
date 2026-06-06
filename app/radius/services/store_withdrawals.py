"""store_withdrawals — سحب رصيد المتجر (ثقة الزبون).

الزبون يطلب سحبًا (اسمه + رقم الحساب الذي نحوّل إليه + المبلغ: كله أو
جزء). المدير ينفّذ التحويل البنكي **يدويًا** ثم يؤكّد في اللوحة فيُخصم
الرصيد آليًا.

⚠️ لا حركة مال تلقائية: الخصم يحدث **فقط** عند تأكيد المدير، عبر خدمة
الرصيد (WalletService.debit) التي تمنع الرصيد السالب بنيويًا — فلا
يُسحب أكثر من الرصيد. idempotency صارمة: تحويل الحالة من pending ذرّيًا
بحارس rowcount قبل الخصم، فلا يُخصم مرتين. عند فشل الخصم (نقص الرصيد
لتغيّره بعد الطلب) نعيد الطلب pending برسالة واضحة بلا خصم معلّق.
"""
from __future__ import annotations

from typing import Any

from ..core.system_config import default_currency
from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import (
    BusinessOSValidationError,
    EventService,
    WalletService,
    minor_to_money,
    money_to_minor,
)
from .card_users_marketplace import CardUsersMarketplaceService
from .store_deposits import _notify_customer


class StoreWithdrawalError(ValueError):
    """خطأ تحقّق آمن في طلب السحب (رسائل عربية)."""


_STATUS_AR = {
    "pending": "بانتظار التنفيذ",
    "confirmed": "نُفِّذ — خُصم الرصيد",
    "rejected": "مرفوض",
}


class WithdrawalRequestService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.wallets = WalletService()
        self.events = EventService()

    def _row(self, row) -> dict[str, Any]:
        out = row_to_dict(row)
        out["amount"] = minor_to_money(out.get("amount_minor"))
        out["status_ar"] = _STATUS_AR.get(str(out.get("status") or ""),
                                          out.get("status"))
        # العملة المعروضة = المضبوطة حاليًا (مصدر واحد) لا المخزّنة وقت
        # الطلب — فلا تختلط JOD/ILS عبر اللوحة وصفحة الزبون.
        out["currency"] = default_currency()
        return out

    def _wallet(self, card_user_id: int) -> dict[str, Any]:
        # نفس محفظة سوق البطاقات (مصدر واحد للرصيد).
        return CardUsersMarketplaceService(
            tenant_id=self.tenant_id
        )._wallet_for_card_user(int(card_user_id))  # noqa: SLF001

    def create_request(
        self, *, card_user_id: int, amount: Any, payee_name: str,
        payee_account: str, method: str = "", currency: str = "",
    ) -> dict[str, Any]:
        """ينشئ طلب سحب بحالة pending — لا حركة مال. يتحقّق أن المبلغ
        موجب ولا يتجاوز الرصيد الحالي (حارس تجربة؛ الخصم الفعلي وحارسه
        البنيوي عند التأكيد)."""
        CardUsersMarketplaceService(tenant_id=self.tenant_id).get_card_user(int(card_user_id))
        if not str(payee_name or "").strip():
            raise StoreWithdrawalError("اسم صاحب الحساب مطلوب.")
        if not str(payee_account or "").strip():
            raise StoreWithdrawalError("رقم الحساب الذي نحوّل إليه مطلوب.")
        amount_minor = money_to_minor(amount)
        if amount_minor <= 0:
            raise StoreWithdrawalError("أدخل مبلغًا صحيحًا أكبر من صفر.")
        wallet = self._wallet(int(card_user_id))
        balance_minor = int(wallet.get("balance_minor") or 0)
        if amount_minor > balance_minor:
            raise StoreWithdrawalError(
                f"المبلغ المطلوب أكبر من رصيدك ({minor_to_money(balance_minor)}).")
        cur_code = str(currency or wallet.get("currency") or default_currency()).upper()[:8]
        now = now_iso()
        cur = db().execute(
            """
            INSERT INTO withdrawal_requests(
                tenant_id, card_user_id, payee_name, payee_account, method,
                amount_minor, currency, status, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (self.tenant_id, int(card_user_id), str(payee_name).strip(),
             str(payee_account).strip(), str(method or ""), amount_minor,
             cur_code, "pending", now),
        )
        request_id = int(cur.lastrowid)
        self.events.record_event(
            tenant_id=self.tenant_id, category="financial",
            event_key="store.withdrawal_requested",
            message="طلب سحب رصيد جديد بانتظار تنفيذ المدير.",
            actor_type="card_user", actor_id=int(card_user_id),
            target_type="card_user", target_id=int(card_user_id),
            metadata={"withdrawal_request_id": request_id,
                      "amount_minor": amount_minor},
        )
        # تنبيه المالك بطلب سحب جديد بانتظار التنفيذ (أفضل-جهد).
        try:
            from .store_alerts import notify_withdrawal
            notify_withdrawal(self.tenant_id, request_id,
                              minor_to_money(amount_minor), default_currency(),
                              name=str(payee_name or ""))
        except Exception:  # noqa: BLE001
            pass
        return self.get(request_id)

    def get(self, request_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM withdrawal_requests WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(request_id)),
        ).fetchone()
        if not row:
            raise StoreWithdrawalError("طلب السحب غير موجود.")
        return self._row(row)

    def list_requests(self, *, status: str = "", card_user_id: int | None = None,
                      limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM withdrawal_requests WHERE tenant_id=?"
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
        return self.list_requests(card_user_id=int(card_user_id), limit=limit)

    def pending_count(self) -> int:
        row = db().execute(
            "SELECT COUNT(*) n FROM withdrawal_requests WHERE tenant_id=? AND status='pending'",
            (self.tenant_id,),
        ).fetchone()
        return int(row["n"] or 0)

    def confirm(self, request_id: int, *, actor: str = "admin",
                note: str = "") -> dict[str, Any]:
        """يؤكّد تنفيذ التحويل ويُخصم الرصيد آليًا. idempotent: التحويل
        من pending ذرّيٌّ بحارس rowcount قبل الخصم. عند نقص الرصيد (تغيّر
        بعد الطلب) يفشل الخصم البنيوي فنعيد الطلب pending برسالة واضحة."""
        req = self.get(request_id)
        if req["status"] != "pending":
            return req  # محسوم سابقًا → لا خصم متكرر (idempotent).
        amount_minor = int(req["amount_minor"])
        now = now_iso()
        # (1) المطالبة الذرّية بالطلب.
        with transaction() as conn:
            claimed = conn.execute(
                """
                UPDATE withdrawal_requests
                SET status='confirmed', admin_note=?, resolved_by=?, resolved_at=?
                WHERE tenant_id=? AND id=? AND status='pending'
                """,
                (str(note or ""), str(actor or "admin"), now,
                 self.tenant_id, int(request_id)),
            )
            if claimed.rowcount != 1:
                return self.get(request_id)
        # (2) الخصم (خدمة الرصيد تمنع السالب). فشل ⇒ إعادة pending.
        try:
            wallet = self._wallet(int(req["card_user_id"]))
            debit = self.wallets.debit(
                tenant_id=self.tenant_id, wallet_id=int(wallet["id"]),
                amount=minor_to_money(amount_minor),
                actor_type="admin", actor_id=None,
                reference_type="store_withdrawal", reference_id=int(request_id),
                notes=f"تنفيذ سحب المتجر #{request_id} بواسطة {actor}",
                metadata={"withdrawal_request_id": int(request_id)},
            )
        except BusinessOSValidationError as exc:
            db().execute(
                """
                UPDATE withdrawal_requests
                SET status='pending', resolved_by='', resolved_at=NULL
                WHERE tenant_id=? AND id=?
                """,
                (self.tenant_id, int(request_id)),
            )
            # "cannot go negative" ⇒ نقص الرصيد بعد الطلب.
            if "negative" in str(exc):
                raise StoreWithdrawalError(
                    "رصيد الزبون لم يعد يكفي لهذا السحب (تغيّر بعد الطلب).") from exc
            raise
        except Exception:
            db().execute(
                """
                UPDATE withdrawal_requests
                SET status='pending', resolved_by='', resolved_at=NULL
                WHERE tenant_id=? AND id=?
                """,
                (self.tenant_id, int(request_id)),
            )
            raise
        # (3) ربط حركة الخصم (أثر تدقيق) + حدث + إشعار.
        db().execute(
            "UPDATE withdrawal_requests SET wallet_transaction_id=? WHERE tenant_id=? AND id=?",
            (int(debit["transaction"]["id"]), self.tenant_id, int(request_id)),
        )
        self.events.record_event(
            tenant_id=self.tenant_id, category="financial",
            event_key="store.withdrawal_confirmed",
            message=f"تنفيذ سحب وخصم {minor_to_money(amount_minor)} من المحفظة.",
            actor_type="admin", target_type="card_user",
            target_id=int(req["card_user_id"]),
            metadata={"withdrawal_request_id": int(request_id),
                      "amount_minor": amount_minor,
                      "wallet_transaction_id": int(debit["transaction"]["id"])},
        )
        _notify_customer(
            self.tenant_id, int(req["card_user_id"]),
            f"تم تنفيذ طلب سحبك ({minor_to_money(amount_minor)} "
            f"{default_currency()}) وخصمه من رصيدك.",
        )
        try:
            from .store_alerts import resolve_withdrawal
            resolve_withdrawal(self.tenant_id, int(request_id))
        except Exception:  # noqa: BLE001
            pass
        return self.get(request_id)

    def reject(self, request_id: int, *, actor: str = "admin",
               note: str = "") -> dict[str, Any]:
        """يرفض الطلب — لا حركة مال. idempotent."""
        now = now_iso()
        with transaction() as conn:
            claimed = conn.execute(
                """
                UPDATE withdrawal_requests
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
            event_key="store.withdrawal_rejected",
            message="رُفض طلب سحب المتجر.",
            actor_type="admin", target_type="card_user",
            target_id=int(req["card_user_id"]),
            metadata={"withdrawal_request_id": int(request_id)},
        )
        _notify_customer(
            self.tenant_id, int(req["card_user_id"]),
            "نعتذر، لم نتمكّن من تنفيذ طلب سحبك"
            + (f" — {note}" if note else "") + ". تواصل معنا للمساعدة.",
        )
        try:
            from .store_alerts import resolve_withdrawal
            resolve_withdrawal(self.tenant_id, int(request_id))
        except Exception:  # noqa: BLE001
            pass
        return req


__all__ = [
    "StoreWithdrawalError",
    "WithdrawalRequestService",
]
