"""Service for the internal Company Inventory & Expenses notebook.

Encapsulates the business rules:
- Remaining stock = signed-quantity sum across an item's movements.
- Usage may NOT exceed remaining stock (no negative inventory).
- Costs/amounts are informational only.
- NOTHING here writes to the ledger, payments, customer/distributor
  balances, card sales, subscriptions, revenue, or profit.

All mutations are audited via the existing RadiusAuditService with
Arabic summaries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..db.repos import company_inventory_repo as repo
from .audit import get_audit_service


class CompanyInventoryError(ValueError):
    """Validation error surfaced to the UI as an Arabic flash message."""


# ── input coercion / validation ──────────────────────────────────


def _clean_text(value: Optional[str], *, max_len: int = 500) -> str:
    return (value or "").strip()[:max_len]


def _require_text(value: Optional[str], field_msg: str, *, max_len: int = 500) -> str:
    cleaned = _clean_text(value, max_len=max_len)
    if not cleaned:
        raise CompanyInventoryError(field_msg)
    return cleaned


def _parse_positive(value, field_msg: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CompanyInventoryError(field_msg) from exc
    if parsed <= 0:
        raise CompanyInventoryError(field_msg)
    return parsed


def _parse_optional_number(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_signed(value, field_msg: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CompanyInventoryError(field_msg) from exc
    if parsed == 0:
        raise CompanyInventoryError(field_msg)
    return parsed


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _status_for(remaining: float, threshold: Optional[float]) -> str:
    if remaining <= 0:
        return "نفد"
    if threshold is not None and threshold > 0 and remaining <= threshold:
        return "منخفض"
    return "متوفر"


class CompanyInventoryService:
    """Tenant-scoped operations. The caller passes tenant_id + actor."""

    def __init__(self) -> None:
        self._audit = get_audit_service()

    # ── items ────────────────────────────────────────────────────

    def create_item(
        self,
        *,
        tenant_id: int,
        actor: str,
        name: str,
        category: str = "",
        unit: str = "",
        low_stock_threshold=None,
        notes: str = "",
    ) -> dict:
        name = _require_text(name, "اسم الصنف مطلوب.", max_len=120)
        if repo.get_item_by_name(tenant_id=tenant_id, name=name):
            raise CompanyInventoryError("هذا الصنف موجود مسبقًا.")
        threshold = _parse_optional_number(low_stock_threshold)
        if threshold is not None and threshold < 0:
            threshold = None
        item = repo.create_item(
            tenant_id=tenant_id,
            name=name,
            category=_clean_text(category, max_len=80),
            unit=_clean_text(unit, max_len=40),
            low_stock_threshold=threshold,
            notes=_clean_text(notes, max_len=500),
        )
        self._audit.record(
            actor=actor,
            action="company_inventory.item.create",
            target_type="company_inventory_item",
            target_id=str(item.get("id")),
            payload={"summary": f"تم إنشاء صنف مخزون: {name}", "name": name},
        )
        return item

    def deactivate_item(self, *, tenant_id: int, actor: str, item_id: int) -> None:
        item = repo.get_item(tenant_id=tenant_id, item_id=item_id)
        if not item:
            raise CompanyInventoryError("الصنف غير موجود.")
        repo.set_item_active(tenant_id=tenant_id, item_id=item_id, is_active=False)
        self._audit.record(
            actor=actor,
            action="company_inventory.item.deactivate",
            target_type="company_inventory_item",
            target_id=str(item_id),
            payload={"summary": f"تم تعطيل صنف: {item.get('name')}"},
        )

    def _resolve_item(self, *, tenant_id: int, item_id=None, item_name: str = "",
                      category: str = "", unit: str = "", actor: str = "system") -> dict:
        """Find an item by id or name; create it when only a (new) name
        is supplied — used by the incoming form so the admin can add
        stock for a brand-new item in one step."""
        if item_id:
            item = repo.get_item(tenant_id=tenant_id, item_id=int(item_id))
            if not item:
                raise CompanyInventoryError("الصنف غير موجود.")
            return item
        name = _require_text(item_name, "اختر صنفًا أو أدخل اسم صنف جديد.",
                             max_len=120)
        existing = repo.get_item_by_name(tenant_id=tenant_id, name=name)
        if existing:
            return existing
        return self.create_item(
            tenant_id=tenant_id, actor=actor, name=name,
            category=category, unit=unit,
        )

    # ── incoming stock ───────────────────────────────────────────

    def add_incoming(
        self,
        *,
        tenant_id: int,
        actor: str,
        item_id=None,
        item_name: str = "",
        category: str = "",
        unit: str = "",
        quantity=None,
        unit_cost=None,
        supplier: str = "",
        reference: str = "",
        movement_date: str = "",
        notes: str = "",
        created_by_admin_id: Optional[int] = None,
    ) -> dict:
        qty = _parse_positive(quantity, "أدخل كمية صحيحة أكبر من صفر.")
        item = self._resolve_item(
            tenant_id=tenant_id, item_id=item_id, item_name=item_name,
            category=category, unit=unit, actor=actor,
        )
        u_cost = _parse_optional_number(unit_cost)
        total = round(u_cost * qty, 2) if u_cost is not None else None
        move = repo.add_movement(
            tenant_id=tenant_id,
            item_id=int(item["id"]),
            movement_type="incoming",
            quantity=qty,
            unit_cost=u_cost,
            total_cost=total,
            supplier=_clean_text(supplier, max_len=120),
            reference=_clean_text(reference, max_len=120),
            movement_date=_clean_text(movement_date, max_len=40) or _today(),
            notes=_clean_text(notes, max_len=500),
            created_by_admin_id=created_by_admin_id,
        )
        self._audit.record(
            actor=actor,
            action="company_inventory.incoming.add",
            target_type="company_inventory_item",
            target_id=str(item["id"]),
            payload={
                "summary": f"وارد مخزون: {qty:g} {item.get('unit') or ''} "
                           f"من {item.get('name')}",
                "quantity": qty,
                "total_cost": total,
            },
        )
        return move

    # ── usage / issue ────────────────────────────────────────────

    def record_usage(
        self,
        *,
        tenant_id: int,
        actor: str,
        item_id: int,
        quantity=None,
        usage_reason: str = "",
        location: str = "",
        technician: str = "",
        related_customer_id=None,
        movement_date: str = "",
        notes: str = "",
        created_by_admin_id: Optional[int] = None,
    ) -> dict:
        item = repo.get_item(tenant_id=tenant_id, item_id=int(item_id))
        if not item:
            raise CompanyInventoryError("الصنف غير موجود.")
        qty = _parse_positive(quantity, "أدخل كمية صحيحة أكبر من صفر.")
        remaining = repo.remaining_for_item(tenant_id=tenant_id, item_id=int(item_id))
        if qty > remaining:
            unit = item.get("unit") or ""
            raise CompanyInventoryError(
                f"الكمية المطلوبة غير متوفرة في المخزون. "
                f"المتبقي الحالي: {remaining:g} {unit}."
            )
        cust_id = None
        if related_customer_id not in (None, ""):
            try:
                cust_id = int(related_customer_id)
            except (TypeError, ValueError):
                cust_id = None
        move = repo.add_movement(
            tenant_id=tenant_id,
            item_id=int(item_id),
            movement_type="usage",
            quantity=qty,
            usage_reason=_clean_text(usage_reason, max_len=200),
            location=_clean_text(location, max_len=120),
            technician=_clean_text(technician, max_len=120),
            related_customer_id=cust_id,
            movement_date=_clean_text(movement_date, max_len=40) or _today(),
            notes=_clean_text(notes, max_len=500),
            created_by_admin_id=created_by_admin_id,
        )
        self._audit.record(
            actor=actor,
            action="company_inventory.usage.add",
            target_type="company_inventory_item",
            target_id=str(item_id),
            payload={
                "summary": f"صرف مخزون: {qty:g} {item.get('unit') or ''} "
                           f"من {item.get('name')}",
                "quantity": qty,
                "remaining_after": remaining - qty,
            },
        )
        return move

    # ── company expenses ─────────────────────────────────────────

    def add_expense(
        self,
        *,
        tenant_id: int,
        actor: str,
        title: str,
        amount=None,
        category: str = "",
        expense_date: str = "",
        paid_to: str = "",
        payment_method: str = "",
        reference: str = "",
        notes: str = "",
        created_by_admin_id: Optional[int] = None,
    ) -> dict:
        title = _require_text(title, "عنوان المصروف مطلوب.", max_len=160)
        amt = _parse_positive(amount, "أدخل مبلغًا صحيحًا أكبر من صفر.")
        expense = repo.add_expense(
            tenant_id=tenant_id,
            title=title,
            amount=amt,
            category=_clean_text(category, max_len=80),
            expense_date=_clean_text(expense_date, max_len=40) or _today(),
            paid_to=_clean_text(paid_to, max_len=120),
            payment_method=_clean_text(payment_method, max_len=60),
            reference=_clean_text(reference, max_len=120),
            notes=_clean_text(notes, max_len=500),
            created_by_admin_id=created_by_admin_id,
        )
        self._audit.record(
            actor=actor,
            action="company_expense.add",
            target_type="company_expense",
            target_id=str(expense.get("id")),
            payload={"summary": f"مصروف شركة: {title} ({amt:g})", "amount": amt},
        )
        return expense

    # ── read models ──────────────────────────────────────────────

    def overview(self, *, tenant_id: int) -> list[dict]:
        """Per-item stock summary with status badge."""
        items = repo.list_items(tenant_id=tenant_id)
        aggregates = repo.item_aggregates(tenant_id=tenant_id)
        out: list[dict] = []
        for item in items:
            agg = aggregates.get(int(item["id"]), {})
            remaining = float(agg.get("remaining", 0) or 0)
            out.append({
                "id": item["id"],
                "name": item["name"],
                "category": item.get("category") or "",
                "unit": item.get("unit") or "",
                "purchased": float(agg.get("purchased", 0) or 0),
                "used": float(agg.get("used", 0) or 0),
                "remaining": remaining,
                "incoming_value": float(agg.get("incoming_value", 0) or 0),
                "last_movement": agg.get("last_movement"),
                "low_stock_threshold": item.get("low_stock_threshold"),
                "status": _status_for(remaining, item.get("low_stock_threshold")),
            })
        return out

    def summary_cards(self, *, tenant_id: int) -> dict:
        """Top-of-page KPI numbers."""
        overview = self.overview(tenant_id=tenant_id)
        move_tot = repo.movement_totals(tenant_id=tenant_id)
        exp_tot = repo.expense_totals(tenant_id=tenant_id)
        low_or_out = sum(1 for r in overview if r["status"] in ("منخفض", "نفد"))
        return {
            "incoming_value": float(move_tot.get("incoming_value", 0) or 0),
            "expenses_total": float(exp_tot.get("total", 0) or 0),
            "items_count": len(overview),
            "low_or_out_count": low_or_out,
        }

    def reports(
        self,
        *,
        tenant_id: int,
        date_from: str = "",
        date_to: str = "",
        item_id=None,
        category: str = "",
        movement_type: str = "",
        expense_category: str = "",
    ) -> dict:
        """Monthly-style summaries + filtered record lists."""
        # Default window: current calendar month.
        if not date_from and not date_to:
            month_start = datetime.now(timezone.utc).strftime("%Y-%m-01")
            date_from = month_start
        move_tot = repo.movement_totals(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        exp_tot = repo.expense_totals(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        by_cat = repo.expenses_by_category(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        movements = repo.list_movements(
            tenant_id=tenant_id,
            item_id=int(item_id) if item_id else None,
            movement_type=movement_type if movement_type in repo.MOVEMENT_TYPES else "",
            date_from=date_from,
            date_to=date_to,
            limit=100,
        )
        expenses = repo.list_expenses(
            tenant_id=tenant_id,
            category=expense_category,
            date_from=date_from,
            date_to=date_to,
            limit=100,
        )
        return {
            "date_from": date_from,
            "date_to": date_to,
            "incoming_value": float(move_tot.get("incoming_value", 0) or 0),
            "usage_quantity": float(move_tot.get("usage_quantity", 0) or 0),
            "expenses_total": float(exp_tot.get("total", 0) or 0),
            "expenses_by_category": by_cat,
            "movements": movements,
            "expenses": expenses,
        }

    def items_for_select(self, *, tenant_id: int) -> list[dict]:
        return repo.list_items(tenant_id=tenant_id)

    def recent_movements(self, *, tenant_id: int, limit: int = 15) -> list[dict]:
        return repo.list_movements(tenant_id=tenant_id, limit=limit)

    # ── per-tab record lists (display tables) ────────────────────

    def incoming_records(self, *, tenant_id: int, limit: int = 200) -> list[dict]:
        return repo.list_movements(
            tenant_id=tenant_id, movement_type="incoming", limit=limit
        )

    def usage_records(self, *, tenant_id: int, limit: int = 200) -> list[dict]:
        return repo.list_movements(
            tenant_id=tenant_id, movement_type="usage", limit=limit
        )

    def expense_records(self, *, tenant_id: int, limit: int = 200) -> list[dict]:
        return repo.list_expenses(tenant_id=tenant_id, limit=limit)
