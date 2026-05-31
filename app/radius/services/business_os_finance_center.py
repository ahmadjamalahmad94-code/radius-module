"""Read models for the Business OS Finance Center web screens."""
from __future__ import annotations

from typing import Any

from ..db.connection import db
from ..db.helpers import json_load
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


class FinanceCenterService:
    """Small query facade for finance dashboard and section pages."""

    def dashboard(self, *, tenant_id: int = 1) -> dict[str, Any]:
        tenant = int(tenant_id)
        wallet_count = int(_scalar("SELECT COUNT(*) FROM wallets WHERE tenant_id=?", (tenant,)) or 0)
        ledger_count = int(_scalar("SELECT COUNT(*) FROM ledger_entries WHERE tenant_id=?", (tenant,)) or 0)
        revenue_count = int(_scalar("SELECT COUNT(*) FROM revenue_records WHERE tenant_id=?", (tenant,)) or 0)
        loan_count = int(_scalar("SELECT COUNT(*) FROM loan_entries WHERE tenant_id=?", (tenant,)) or 0) if _table_exists("loan_entries") else 0
        open_loan_count = int(_scalar("SELECT COUNT(*) FROM loan_entries WHERE tenant_id=? AND status='open'", (tenant,)) or 0) if _table_exists("loan_entries") else 0
        return {
            "wallet_count": wallet_count,
            "wallet_balance": _minor_sum("wallets", "balance_minor", params=(tenant,)),
            "ledger_entries": ledger_count,
            "ledger_total": _minor_sum("ledger_entries", "amount_minor", "tenant_id=? AND voided_at IS NULL", (tenant,)),
            "total_revenue": _minor_sum("revenue_records", "collected_amount_minor", params=(tenant,)),
            "total_collections": _real_sum("payment_transactions", "amount", "tenant_id=? AND status='posted'", (tenant,)),
            "total_debts": "0.00",
            "total_loans": _real_sum("loan_entries", "amount", "tenant_id=? AND status='open'", (tenant,)),
            "total_profit": _minor_sum("revenue_records", "net_profit_minor", params=(tenant,)),
            "distributor_shares": _minor_sum("profit_shares", "share_amount_minor", "tenant_id=? AND beneficiary_type='distributor'", (tenant,)),
            "revenue_records": revenue_count,
            "loan_count": loan_count,
            "open_loan_count": open_loan_count,
        }

    def wallets(self, *, tenant_id: int = 1, limit: int = 100) -> list[dict[str, Any]]:
        return WalletService().list_wallets(tenant_id=tenant_id, limit=limit)

    def wallet_transactions(self, *, tenant_id: int = 1, wallet_id: int, limit: int = 25) -> list[dict[str, Any]]:
        return WalletService().list_transactions(tenant_id=tenant_id, wallet_id=wallet_id, limit=limit)

    def ledger(self, *, tenant_id: int = 1, entry_type: str = "", limit: int = 200) -> list[dict[str, Any]]:
        return LedgerService().list_entries(tenant_id=tenant_id, entry_type=entry_type, limit=limit)

    def revenue(self, *, tenant_id: int = 1, limit: int = 200) -> list[dict[str, Any]]:
        rows = db().execute(
            "SELECT * FROM revenue_records WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
            (int(tenant_id), int(limit)),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in tuple(item):
                if key.endswith("_minor"):
                    item[key[:-6]] = minor_to_money(item[key])
            item["collected"] = item.get("collected_amount", "0.00")
            item["metadata"] = json_load(item.get("metadata_json"), {})
            items.append(item)
        return items

    def loans(self, *, tenant_id: int = 1, status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if not _table_exists("loan_entries"):
            return []
        sql = "SELECT * FROM loan_entries WHERE tenant_id=?"
        params: list[Any] = [int(tenant_id)]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [dict(row) for row in db().execute(sql, tuple(params)).fetchall()]

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
        rows = db().execute(
            "SELECT * FROM loan_entries WHERE tenant_id=? AND status='open' "
            "ORDER BY id DESC LIMIT ?",
            (tenant, int(limit)),
        ).fetchall()
        items: list[dict[str, Any]] = []
        total = 0.0
        for row in rows:
            item = dict(row)
            try:
                total += float(item.get("amount") or 0)
            except (TypeError, ValueError):
                pass
            items.append(item)
        return {
            "items": items,
            "count": len(items),
            "total": f"{total:.2f}",
            "source": "loan_entries",
            "tenant_id": tenant,
        }
