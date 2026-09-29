"""Read models for the Business OS Finance Center web screens."""
from __future__ import annotations

from typing import Any

from ..core.system_config import default_currency
from ..db.connection import db
from ..db.helpers import json_load
from ..db.repos import accounting_repo
from .business_os_finance import LedgerService, WalletService, minor_to_money


def _scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = db().execute(sql, params).fetchone()
    if not row:
        return 0
    return row[0]


def _table_exists(name: str) -> bool:
    row = db().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _minor_sum(table: str, column: str, where: str = "tenant_id=?", params: tuple[Any, ...] = (1,)) -> str:
    if not _table_exists(table):
        return "0.00"
    value = _scalar(f"SELECT COALESCE(SUM({column}), 0) FROM {table} WHERE {where}", params)
    return minor_to_money(value or 0)


def _real_sum(table: str, column: str, where: str = "tenant_id=?", params: tuple[Any, ...] = (1,)) -> str:
    if not _table_exists(table):
        return "0.00"
    value = _scalar(f"SELECT COALESCE(SUM({column}), 0) FROM {table} WHERE {where}", params)
    return f"{float(value or 0):.2f}"


# ── تعريب مصدر الإيراد (source_type في revenue_records) ──────────────────
# قاموس كامل يغطّي كل قيم source_type التي تُدرَج فعليًا في الجدول
# (card_user_purchase وcard_batch من الخدمات، وmanual_sale من التسجيل
# اليدوي/الاختبارات) إضافةً للقيم القديمة المحتملة — حتى لا يسقط أي مفتاح
# كنص إنجليزي خام. أي مفتاح غير معروف يُعرض كوصف عربي عام لا بالإنجليزية.
_REVENUE_SOURCE_LABELS: dict[str, str] = {
    "card_user_purchase": "شراء بطاقة",
    "card_batch": "دفعة بطاقات",
    "card_sale": "بيع بطاقات",
    "manual_sale": "مبيعة يدوية",
    "manual": "تسجيل يدوي",
    "subscriber_payment": "دفعة مشترك",
    "payment": "دفعة",
    "renewal": "تجديد اشتراك",
    "invoice": "فاتورة",
    "voucher": "كوبون",
    "topup": "شحن رصيد",
    "subscriber_quota_topup": "شحن رصيد",
    "wallet": "محفظة",
}


def _revenue_entity_name(source_type: str, source_id: Any, tenant_id: int) -> str:
    """الاسم الحقيقي للكيان المرتبط بسجل الإيراد بدل المعرّف الرقمي.

    لكل نوع مصدر نَصِل لاسم بشري واضح (اسم المشتري + اسم الباقة لشراء
    بطاقة، اسم باقة الدفعة لدفعة البطاقات). أي تعذّر في الوصل يُرجع نصًّا
    فارغًا فيكتفي العرض بالتسمية العربية للنوع — لا يكسر الصفحة أبدًا ولا
    يُظهر معرّفًا خامًا.
    """
    try:
        sid = int(source_id)
    except (TypeError, ValueError):
        return ""
    if sid <= 0:
        return ""
    try:
        if source_type == "card_user_purchase":
            row = db().execute(
                "SELECT cu.display_name AS buyer, p.name AS package "
                "FROM card_user_purchases cup "
                "LEFT JOIN card_users cu ON cu.id = cup.card_user_id "
                "LEFT JOIN card_marketplace_packages p ON p.id = cup.package_id "
                "WHERE cup.id = ? AND cup.tenant_id = ?",
                (sid, int(tenant_id)),
            ).fetchone()
            if row:
                buyer = (row["buyer"] or "").strip()
                package = (row["package"] or "").strip()
                if buyer and package:
                    return f"{buyer} — {package}"
                return buyer or package
        elif source_type == "card_batch":
            row = db().execute(
                "SELECT package_name, batch_code FROM card_batches "
                "WHERE id = ? AND tenant_id = ?",
                (sid, int(tenant_id)),
            ).fetchone()
            if row:
                return (row["package_name"] or "").strip() or (row["batch_code"] or "").strip()
    except Exception:  # noqa: BLE001 — وصل عرضي فقط، لا يكسر الصفحة
        return ""
    return ""


def revenue_source_display(source_type: str, source_id: Any, tenant_id: int) -> str:
    """نص المصدر النهائي للعرض: «تسمية النوع بالعربية — الاسم الحقيقي».

    صفر إنجليزي وصفر معرّف رقمي خام: النوع يُترجم من القاموس، والاسم
    يُحلّ من قاعدة البيانات. النوع غير المعروف يُعرض «مصدر إيراد آخر».
    """
    base = _REVENUE_SOURCE_LABELS.get((source_type or "").strip(), "")
    name = _revenue_entity_name((source_type or "").strip(), source_id, tenant_id)
    if base and name:
        return f"{base} — {name}"
    if base:
        return base
    if name:
        return name
    return "مصدر إيراد آخر"


def revenue_items(tenant_id: int, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """صفوف «الإيرادات» — مصدرٌ واحد للويب (المركز المالي) و``/finance/revenue``.

    دفعات المشتركين من دفتر المحاسبة (المصدر نفسه لتقارير المبيعات؛ المعكوسة
    بحالة «voided» وصافي ربحها/حصّتها 0 — كان التطبيق يجمع ربح الدفعات
    المُلغاة) + سجلّات ``revenue_records`` (مبيعات الكروت)، الأحدث أوّلًا."""
    tid = int(tenant_id)
    window = max(int(limit), 1) + max(int(offset or 0), 0)
    items: list[dict[str, Any]] = []
    for pay in accounting_repo.payment_revenue_items(tid, limit=window):
        amount = float(pay.get("amount") or 0)
        voided = (pay.get("status") or "posted") == "voided"
        username = pay.get("username") or ""
        items.append({
            "id": int(pay["id"]),
            "source_type": "subscriber_payment",
            "source_id": pay.get("source_id"),
            "price_snapshot_id": None,
            "original_price": amount,
            "retail_price": amount,
            "wholesale_cost": 0.0,
            "collected_amount": amount,
            "debt_amount": 0.0,
            "discount_amount": 0.0,
            "net_profit": 0.0 if voided else amount,
            "company_share": 0.0 if voided else amount,
            "currency": pay.get("currency") or default_currency(),
            "status": pay.get("status") or "posted",
            "metadata": {"username": username,
                         "subscriber_id": pay.get("subscriber_id"),
                         "operator": pay.get("operator") or "",
                         "ledger_entry_id": int(pay["id"])},
            "created_at": pay.get("created_at"),
            "collected": amount,
            "source_display": ("دفعة مشترك — " + username) if username else "دفعة مشترك",
        })
    if _table_exists("revenue_records"):
        rows = db().execute(
            "SELECT * FROM revenue_records WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
            (tid, window),
        ).fetchall()
        for row in rows:
            item = dict(row)
            for key in tuple(item):
                if key.endswith("_minor"):
                    item[key[:-6]] = minor_to_money(item[key])
            item["collected"] = item.get("collected_amount", "0.00")
            item["metadata"] = json_load(item.get("metadata_json"), {})
            # نص المصدر معرَّب بالكامل مع الاسم الحقيقي بدل source_type#id الخام
            item["source_display"] = revenue_source_display(
                item.get("source_type"), item.get("source_id"), tid
            )
            items.append(item)
    items.sort(key=lambda it: str(it.get("created_at") or "").replace("T", " "), reverse=True)
    start = max(int(offset or 0), 0)
    return items[start:start + max(int(limit), 1)]


class FinanceCenterService:
    """Small query facade for finance dashboard and section pages."""

    def dashboard(self, *, tenant_id: int = 1) -> dict[str, Any]:
        tenant = int(tenant_id)
        wallet_count = int(_scalar("SELECT COUNT(*) FROM wallets WHERE tenant_id=?", (tenant,)) or 0)
        ledger_count = int(_scalar("SELECT COUNT(*) FROM ledger_entries WHERE tenant_id=?", (tenant,)) or 0)
        revenue_count = int(_scalar("SELECT COUNT(*) FROM revenue_records WHERE tenant_id=?", (tenant,)) or 0)
        loan_count = int(_scalar("SELECT COUNT(*) FROM loan_entries WHERE tenant_id=?", (tenant,)) or 0) if _table_exists("loan_entries") else 0
        open_loan_count = int(_scalar("SELECT COUNT(*) FROM loan_entries WHERE tenant_id=? AND status='open'", (tenant,)) or 0) if _table_exists("loan_entries") else 0
        # الإيراد/الربح/التحصيل من المصدر نفسه لـ/api/v1/finance/revenue وتقرير
        # «دفعات المستفيدين»: دفعات الدفتر (صافية من الإلغاء) + سجلّات الكروت.
        # كانت تقرأ revenue_records وحده (الدفعات لا تكتب فيه) ⇒ «0 ₪» دائمًا.
        rev = accounting_repo.revenue_summary(tenant)
        payment_rows = int(_scalar(
            "SELECT COUNT(*) FROM accounting_ledger_entries WHERE tenant_id=? "
            "AND entry_type='payment' AND status='posted'", (tenant,)) or 0)
        # الديون/السلف المفتوحة = **المتبقّي** (القيمة − التسويات المُرحَّلة)، لا
        # القيمة الأصليّة — التسوية الجزئيّة تُبقي السلفة مفتوحة بباقيها.
        loans_t = (accounting_repo.loan_totals(tenant, status="open")
                   if _table_exists("loan_entries") else
                   {"outstanding": 0.0, "total_amount": 0.0, "by_currency": [], "mixed_currency": False})
        return {
            "wallet_count": wallet_count,
            "wallet_balance": _minor_sum("wallets", "balance_minor", params=(tenant,)),
            "ledger_entries": ledger_count,
            "ledger_total": _minor_sum("ledger_entries", "amount_minor", "tenant_id=? AND voided_at IS NULL", (tenant,)),
            "total_revenue": f"{rev['revenue']:.2f}",
            "total_collections": f"{rev['payments']:.2f}",
            "total_debts": f"{float(loans_t['outstanding'] or 0):.2f}",
            "total_loans": f"{float(loans_t['outstanding'] or 0):.2f}",
            "total_loans_original": f"{float(loans_t['total_amount'] or 0):.2f}",
            "total_profit": f"{rev['profit']:.2f}",
            "distributor_shares": _minor_sum("profit_shares", "share_amount_minor", "tenant_id=? AND beneficiary_type='distributor'", (tenant,)),
            "revenue_records": payment_rows + revenue_count,
            "payment_transactions": int(rev["transactions"]),
            "loan_count": loan_count,
            "open_loan_count": open_loan_count,
            # لكلّ عملة رقمها (لا سعر صرف): revenue/profit/payments لكلّ عملة،
            # والمتبقّي من السلف المفتوحة لكلّ عملة.
            "revenue_by_currency": rev["by_currency"],
            "loans_by_currency": loans_t.get("by_currency") or [],
            "mixed_currency": bool(rev["mixed_currency"] or loans_t.get("mixed_currency")),
        }

    def wallets(self, *, tenant_id: int = 1, limit: int = 100) -> list[dict[str, Any]]:
        return WalletService().list_wallets(tenant_id=tenant_id, limit=limit)

    def wallet_transactions(self, *, tenant_id: int = 1, wallet_id: int, limit: int = 25) -> list[dict[str, Any]]:
        return WalletService().list_transactions(tenant_id=tenant_id, wallet_id=wallet_id, limit=limit)

    def ledger(self, *, tenant_id: int = 1, entry_type: str = "", limit: int = 200) -> list[dict[str, Any]]:
        return LedgerService().list_entries(tenant_id=tenant_id, entry_type=entry_type, limit=limit)

    def revenue(self, *, tenant_id: int = 1, limit: int = 200) -> list[dict[str, Any]]:
        return revenue_items(int(tenant_id), limit=limit)

    def loans(self, *, tenant_id: int = 1, status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if not _table_exists("loan_entries"):
            return []
        # صفّ السلفة + settled_amount + outstanding (المتبقّي بعد التسوية الجزئيّة).
        return accounting_repo.list_loans(int(tenant_id), status=status, limit=int(limit))

    def debts(self, *, tenant_id: int = 1, limit: int = 300) -> dict[str, Any]:
        """Money owed to the operator, derived from existing records.

        No dedicated debt-cycle table exists, so the closest real "money
        owed" records are open (unsettled) loan entries. Each open loan is
        an amount lent to a subscriber that has not yet been paid back.
        This is read-only and creates no synthetic numbers.
        """
        tenant = int(tenant_id)
        if not _table_exists("loan_entries"):
            return {
                "items": [],
                "count": 0,
                "total": "0.00",
                "source": "loan_entries",
                "tenant_id": tenant,
            }
        # المتبقّي لا القيمة الأصليّة: سلفة 4.67 سُدِّد منها 2 دَينُها 2.67.
        # الإجماليّ في SQL على **كل** السلف المفتوحة (لا أوّل ``limit`` فقط).
        items = accounting_repo.list_loans(tenant, status="open", limit=int(limit))
        totals = accounting_repo.loan_totals(tenant, status="open")
        return {
            "items": items,
            "count": int(totals["open_count"]),
            "total": f"{float(totals['outstanding'] or 0):.2f}",
            "total_original": f"{float(totals['total_amount'] or 0):.2f}",
            "by_currency": totals.get("by_currency") or [],
            "mixed_currency": bool(totals.get("mixed_currency")),
            "source": "loan_entries",
            "tenant_id": tenant,
        }
