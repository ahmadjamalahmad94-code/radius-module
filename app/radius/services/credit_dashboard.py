"""لوحة شحن الرصيد — السلطة الموحّدة (للمالك) على أرصدة وديون وسلف
المدراء *و* الموزّعين معًا.

«صفر مسار مالٍ موازٍ»: هذه الخدمة تنسّق بين النماذج القائمة المدقَّقة فقط
ولا تخترع دفترًا جديدًا:

  • المدراء — محفظة WalletService (owner_type=manager) = الرصيد، و
    :class:`ManagerCreditService` (manager_credit_ledger + سقوف admins) للدين
    (دين) والسلف (سلف) والسقوف. الشحن يسدّد الدين أولًا (debt_settle) ثم
    يقيّد الباقي في المحفظة.
  • الموزّعون — محفظة WalletService (owner_type=distributor) = الرصيد (نفس
    مسار شحن «المشغّلين» القائم)، و``distributors.debt_balance`` /
    ``credit_limit`` للدين والسقف. الشحن يخفض الدين أولًا (دفتر الموزّع
    المدقَّق) ثم يقيّد الباقي في المحفظة. الموزّعون لا يملكون مفهوم «سلف».

كل قرش يمرّ عبر WalletService.credit (wallet_transactions + ledger_entries +
business_events) أو دفاتر الدين القائمة — فيظهر في الحركات والمحاسبة. لا حقول
تجميلية: كل عمود حقيقي ومربوط.
"""
from __future__ import annotations

from typing import Any

from ..core.system_config import default_currency
from ..db.connection import db
from ..db.repos import admins_repo, operations_repo
from .business_os_finance import (
    WalletService,
    minor_to_money,
    money_to_minor,
)
from .manager_credit import ManagerCreditService
from .manager_distributor_ops import ManagerDistributorOpsService


class CreditDashboardError(ValueError):
    """خطأ تحقّق آمن (رسالة Toast) لعمليات لوحة شحن الرصيد."""


class CreditDashboardService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.ops = ManagerDistributorOpsService(tenant_id=self.tenant_id)
        self.credit = ManagerCreditService(tenant_id=self.tenant_id)
        self.wallets = WalletService()

    # ── قراءة: نظرة شاملة ────────────────────────────────────────────────
    def _last_recharge_map(self) -> dict[tuple[str, int], dict[str, Any]]:
        """أحدث «شحن» لكل مشغّل من manager_distributor_operations.

        نلتقط owner_recharge (مسار اللوحة الجديد) و wallet_recharge (مسار ملف
        المشغّل القديم) معًا حتى يبقى «آخر شحن» صادقًا أيًّا كان مصدره."""
        out: dict[tuple[str, int], dict[str, Any]] = {}
        rows = db().execute(
            """
            SELECT entity_type, entity_id, amount_minor, created_at
            FROM manager_distributor_operations
            WHERE tenant_id=? AND operation_key IN ('owner_recharge','wallet_recharge')
            ORDER BY id DESC
            """,
            (self.tenant_id,),
        ).fetchall()
        for row in rows:
            key = (str(row["entity_type"]), int(row["entity_id"]))
            if key in out:
                continue
            out[key] = {
                "amount": minor_to_money(row["amount_minor"] or 0),
                "at": row["created_at"],
            }
        return out

    def _manager_rows(self, last: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for m in self.ops.list_scope(entity_type="manager"):
            mid = int(m["id"])
            caps = self.credit.get_caps(mid)
            is_owner = self.credit.is_uncapped(mid)
            balance_minor = self.credit.wallet_balance_minor(mid)
            debt_minor = self.credit.current_debt_minor(mid)
            loans_minor = self.credit.current_advances_minor(mid)
            rows.append({
                "entity_type": "manager",
                "id": mid,
                "name": m.get("full_name") or m.get("username") or f"#{mid}",
                "username": m.get("username") or "",
                "status": m.get("status") or "",
                "is_owner": is_owner,
                "balance_minor": balance_minor,
                "balance": minor_to_money(balance_minor),
                "debt_minor": debt_minor,
                "debt": minor_to_money(debt_minor),
                "loans_minor": loans_minor,
                "loans": minor_to_money(loans_minor),
                "has_loans": True,
                "debt_cap_enabled": caps["debt_cap_enabled"],
                "debt_cap": minor_to_money(caps["debt_cap_minor"]),
                "loan_cap_enabled": caps["loan_cap_enabled"],
                "loan_cap": minor_to_money(caps["loan_cap_minor"]),
                "can_give_loan": self.ops.has_permission(
                    entity_type="manager", entity_id=mid, permission="can_give_loan"),
                "last_recharge": last.get(("manager", mid)),
            })
        return rows

    def _distributor_rows(self, last: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for d in self.ops.list_scope(entity_type="distributor"):
            did = int(d["id"])
            dist = operations_repo.get_distributor(self.tenant_id, did) or {}
            balance_minor = int(self.ops.wallet_for(
                entity_type="distributor", entity_id=did).get("balance_minor") or 0)
            debt_minor = money_to_minor(dist.get("debt_balance") or 0)
            cap_minor = money_to_minor(dist.get("credit_limit") or 0)
            rows.append({
                "entity_type": "distributor",
                "id": did,
                "name": d.get("full_name") or d.get("username") or f"#{did}",
                "username": d.get("username") or "",
                "status": d.get("status") or "",
                "is_owner": False,
                "balance_minor": balance_minor,
                "balance": minor_to_money(balance_minor),
                "debt_minor": debt_minor,
                "debt": minor_to_money(debt_minor),
                # الموزّعون لا يملكون مفهوم «سلف» مستقلًّا — تُعرض «—».
                "loans_minor": 0,
                "loans": minor_to_money(0),
                "has_loans": False,
                "debt_cap_enabled": cap_minor > 0,
                "debt_cap": minor_to_money(cap_minor),
                "loan_cap_enabled": False,
                "loan_cap": minor_to_money(0),
                "can_give_loan": False,
                "last_recharge": last.get(("distributor", did)),
            })
        return rows

    @staticmethod
    def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
        bal = sum(int(r["balance_minor"]) for r in rows)
        debt = sum(int(r["debt_minor"]) for r in rows)
        loans = sum(int(r["loans_minor"]) for r in rows)
        return {
            "count": len(rows),
            "balance_minor": bal,
            "balance": minor_to_money(bal),
            "debt_minor": debt,
            "debt": minor_to_money(debt),
            "loans_minor": loans,
            "loans": minor_to_money(loans),
        }

    def overview(self) -> dict[str, Any]:
        last = self._last_recharge_map()
        managers = self._manager_rows(last)
        distributors = self._distributor_rows(last)
        m_totals = self._totals(managers)
        d_totals = self._totals(distributors)
        grand = {
            "balance": minor_to_money(m_totals["balance_minor"] + d_totals["balance_minor"]),
            "debt": minor_to_money(m_totals["debt_minor"] + d_totals["debt_minor"]),
            "loans": minor_to_money(m_totals["loans_minor"] + d_totals["loans_minor"]),
        }
        return {
            "currency": default_currency(),
            "managers": managers,
            "distributors": distributors,
            "manager_totals": m_totals,
            "distributor_totals": d_totals,
            "grand_totals": grand,
        }

    def _credit_wallet(self, *, entity_type: str, entity_id: int, minor: int,
                       actor_id: int | None, method: str, note: str,
                       payment_status: str) -> int | None:
        """يقيّد مبلغًا في محفظة المشغّل (المدير/الموزّع) عبر المسار المدقَّق
        الوحيد (wallet_transactions + ledger_entries + business_events). يُدرج
        ``payment_status`` (paid|debt) في وصف الحركة كي تظهر صحيحة في الحركات
        والمحاسبة. يُرجع معرّف حركة المحفظة."""
        wallet = self.ops.wallet_for(entity_type=entity_type, entity_id=entity_id)
        status_ar = "دين" if payment_status == "debt" else "مدفوع"
        res = self.wallets.credit(
            tenant_id=self.tenant_id, wallet_id=int(wallet["id"]),
            amount=minor_to_money(minor), actor_type="admin", actor_id=actor_id,
            reference_type="owner_recharge",
            notes=f"[{status_ar}] {method}: {note}".strip(": "),
            metadata={"method": method, "note": note, "source": "credit_dashboard",
                      "payment_status": payment_status},
        )
        return int((res.get("transaction") or {}).get("id") or 0) or None

    # ── كتابة: شحن (مدفوع = يسدّد الدين أولًا · دين = رصيد على الحساب) ──────
    def recharge(self, *, entity_type: str, entity_id: int, amount: Any,
                 method: str = "cash", note: str = "", actor: str = "system",
                 actor_id: int | None = None,
                 payment_status: str = "paid") -> dict[str, Any]:
        etype = self._entity_type(entity_type)
        entity_id = int(entity_id)
        try:
            amount_minor = money_to_minor(amount)
        except Exception as exc:  # noqa: BLE001
            raise CreditDashboardError("المبلغ غير صالح") from exc
        if amount_minor <= 0:
            raise CreditDashboardError("المبلغ يجب أن يكون أكبر من صفر.")
        method = (method or "cash").strip()[:40] or "cash"
        note = (note or "").strip()[:300]
        # أيّ قيمة غير «debt» تسقط للوضع الآمن «مدفوع» (لا يُنشئ ديناً).
        payment_status = "debt" if str(payment_status or "").strip().lower() == "debt" else "paid"
        on_account = payment_status == "debt"

        settled_minor = 0
        credited_minor = 0
        debt_recorded_minor = 0
        tx_id: int | None = None

        if etype == "manager":
            if admins_repo.get_admin(entity_id) is None:
                raise CreditDashboardError("المدير غير موجود.")
            if on_account:
                # رصيد على الحساب: يُضاف للمحفظة للاستخدام *و* يُسجَّل ديناً على المدير.
                debt_recorded_minor = self.credit.record_debt(
                    entity_id, amount_minor, actor=actor,
                    reference_type="on_account_credit",
                    notes=note or "رصيد على الحساب (دين)",
                )
                credited_minor = amount_minor
                tx_id = self._credit_wallet(
                    entity_type="manager", entity_id=entity_id, minor=amount_minor,
                    actor_id=actor_id, method=method, note=note, payment_status=payment_status)
            else:
                settled_minor = self.credit.settle_debt(
                    entity_id, amount_minor, actor=actor,
                    reference_type="owner_recharge", notes=note or "شحن رصيد من المالك",
                )
                remainder = amount_minor - settled_minor
                if remainder > 0:
                    credited_minor = remainder
                    tx_id = self._credit_wallet(
                        entity_type="manager", entity_id=entity_id, minor=remainder,
                        actor_id=actor_id, method=method, note=note, payment_status=payment_status)
        else:  # distributor
            if not operations_repo.get_distributor(self.tenant_id, entity_id):
                raise CreditDashboardError("الموزّع غير موجود.")
            if on_account:
                # رصيد على الحساب: debit في دفتر الموزّع يرفع debt_balance، والرصيد
                # القابل للصرف يُضاف للمحفظة (المسار المدقَّق نفسه).
                operations_repo.post_distributor_ledger(
                    self.tenant_id, entity_id, entry_type="on_account_credit",
                    direction="debit", amount=float(minor_to_money(amount_minor)),
                    currency=default_currency(), actor=actor,
                    notes=note or "رصيد على الحساب (دين)", related_type="owner_recharge",
                )
                debt_recorded_minor = amount_minor
                credited_minor = amount_minor
                tx_id = self._credit_wallet(
                    entity_type="distributor", entity_id=entity_id, minor=amount_minor,
                    actor_id=actor_id, method=method, note=note, payment_status=payment_status)
            else:
                entry = operations_repo.settle_distributor_debt(
                    self.tenant_id, entity_id, amount=minor_to_money(amount_minor),
                    currency=default_currency(), actor=actor,
                    notes=note or "شحن رصيد من المالك", related_type="owner_recharge",
                )
                settled_minor = money_to_minor(entry.get("settled") or 0)
                remainder = amount_minor - settled_minor
                if remainder > 0:
                    credited_minor = remainder
                    tx_id = self._credit_wallet(
                        entity_type="distributor", entity_id=entity_id, minor=remainder,
                        actor_id=actor_id, method=method, note=note, payment_status=payment_status)

        self.ops.log_recharge(
            entity_type=etype, entity_id=entity_id, amount=minor_to_money(amount_minor),
            settled=minor_to_money(settled_minor), credited=minor_to_money(credited_minor),
            method=method, note=note, actor=actor, reference_id=tx_id,
            payment_status=payment_status, debt_recorded=minor_to_money(debt_recorded_minor),
        )
        return {
            "entity_type": etype,
            "entity_id": entity_id,
            "amount": minor_to_money(amount_minor),
            "payment_status": payment_status,
            "settled_debt": minor_to_money(settled_minor),
            "credited_wallet": minor_to_money(credited_minor),
            "debt_recorded": minor_to_money(debt_recorded_minor),
        }

    @staticmethod
    def _entity_type(entity_type: str) -> str:
        etype = str(entity_type or "").strip().lower()
        if etype not in {"manager", "distributor"}:
            raise CreditDashboardError("نوع المشغّل غير معروف.")
        return etype
