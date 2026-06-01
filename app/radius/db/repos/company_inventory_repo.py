"""Repository for the internal Company Inventory & Expenses notebook.

THREE independent tables only. These functions never read or write
the accounting ledger, payments, customer/distributor balances, card
sales, subscriptions, revenue, or profit. Costs/amounts persisted
here are informational only.

Remaining-stock rule (see docs/company_inventory_expenses/):
    remaining = Σ signed_quantity
where signed_quantity is:
    incoming   → +quantity
    usage      → −quantity
    adjustment → +quantity (the stored value may itself be negative)
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso

MOVEMENT_TYPES = {"incoming", "usage", "adjustment"}


# ── helpers ──────────────────────────────────────────────────────


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in db().execute(sql, params).fetchall()]


def _row(sql: str, params: tuple = ()) -> Optional[dict]:
    r = db().execute(sql, params).fetchone()
    return dict(r) if r else None


# ── items ────────────────────────────────────────────────────────


def create_item(
    *,
    tenant_id: int,
    name: str,
    category: str = "",
    unit: str = "",
    low_stock_threshold: Optional[float] = None,
    notes: str = "",
) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO company_inventory_items
                (tenant_id, name, category, unit, low_stock_threshold,
                 notes, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (tenant_id, name, category, unit, low_stock_threshold,
             notes or None, now, now),
        )
        item_id = cur.lastrowid
    return get_item(tenant_id=tenant_id, item_id=int(item_id)) or {}


def get_item(*, tenant_id: int, item_id: int) -> Optional[dict]:
    return _row(
        "SELECT * FROM company_inventory_items WHERE id = ? AND tenant_id = ?",
        (item_id, tenant_id),
    )


def get_item_by_name(*, tenant_id: int, name: str) -> Optional[dict]:
    return _row(
        "SELECT * FROM company_inventory_items WHERE tenant_id = ? AND name = ?",
        (tenant_id, name),
    )


def list_items(*, tenant_id: int, include_inactive: bool = False) -> list[dict]:
    if include_inactive:
        return _rows(
            "SELECT * FROM company_inventory_items WHERE tenant_id = ? "
            "ORDER BY name COLLATE NOCASE",
            (tenant_id,),
        )
    return _rows(
        "SELECT * FROM company_inventory_items WHERE tenant_id = ? AND is_active = 1 "
        "ORDER BY name COLLATE NOCASE",
        (tenant_id,),
    )


def set_item_active(*, tenant_id: int, item_id: int, is_active: bool) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE company_inventory_items SET is_active = ?, updated_at = ? "
            "WHERE id = ? AND tenant_id = ?",
            (1 if is_active else 0, now_iso(), item_id, tenant_id),
        )


# ── movements ────────────────────────────────────────────────────


def add_movement(
    *,
    tenant_id: int,
    item_id: int,
    movement_type: str,
    quantity: float,
    unit_cost: Optional[float] = None,
    total_cost: Optional[float] = None,
    supplier: str = "",
    reference: str = "",
    usage_reason: str = "",
    location: str = "",
    technician: str = "",
    related_customer_id: Optional[int] = None,
    movement_date: str = "",
    notes: str = "",
    created_by_admin_id: Optional[int] = None,
) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO company_inventory_movements
                (tenant_id, item_id, movement_type, quantity, unit_cost,
                 total_cost, supplier, reference, usage_reason, location,
                 technician, related_customer_id, movement_date, notes,
                 created_by_admin_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id, item_id, movement_type, quantity, unit_cost,
                total_cost, supplier or None, reference or None,
                usage_reason or None, location or None, technician or None,
                related_customer_id, movement_date or now, notes or None,
                created_by_admin_id, now, now,
            ),
        )
        move_id = cur.lastrowid
    return _row(
        "SELECT * FROM company_inventory_movements WHERE id = ?", (int(move_id),)
    ) or {}


def remaining_for_item(*, tenant_id: int, item_id: int) -> float:
    """Signed-quantity sum for one item (see module docstring)."""
    row = db().execute(
        """
        SELECT COALESCE(SUM(
            CASE movement_type
                WHEN 'incoming'   THEN quantity
                WHEN 'usage'      THEN -quantity
                WHEN 'adjustment' THEN quantity
                ELSE 0
            END), 0) AS remaining
        FROM company_inventory_movements
        WHERE tenant_id = ? AND item_id = ?
        """,
        (tenant_id, item_id),
    ).fetchone()
    return float(row["remaining"] or 0)


def item_aggregates(*, tenant_id: int) -> dict[int, dict]:
    """Per-item purchased / used / remaining / last_movement, keyed by
    item_id. One query for the whole overview table."""
    rows = _rows(
        """
        SELECT
            item_id,
            COALESCE(SUM(CASE WHEN movement_type = 'incoming'
                              THEN quantity ELSE 0 END), 0) AS purchased,
            COALESCE(SUM(CASE WHEN movement_type = 'usage'
                              THEN quantity ELSE 0 END), 0) AS used,
            COALESCE(SUM(CASE movement_type
                              WHEN 'incoming'   THEN quantity
                              WHEN 'usage'      THEN -quantity
                              WHEN 'adjustment' THEN quantity
                              ELSE 0 END), 0) AS remaining,
            COALESCE(SUM(CASE WHEN movement_type = 'incoming'
                              THEN COALESCE(total_cost, 0) ELSE 0 END), 0)
                AS incoming_value,
            MAX(movement_date) AS last_movement
        FROM company_inventory_movements
        WHERE tenant_id = ?
        GROUP BY item_id
        """,
        (tenant_id,),
    )
    return {int(r["item_id"]): r for r in rows}


def list_movements(
    *,
    tenant_id: int,
    item_id: Optional[int] = None,
    movement_type: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
) -> list[dict]:
    clauses = ["m.tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if item_id is not None:
        clauses.append("m.item_id = ?")
        params.append(item_id)
    if movement_type:
        clauses.append("m.movement_type = ?")
        params.append(movement_type)
    if date_from:
        clauses.append("m.movement_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("m.movement_date <= ?")
        params.append(date_to)
    params.append(limit)
    return _rows(
        f"""
        SELECT m.*, i.name AS item_name, i.unit AS item_unit,
               i.category AS item_category
        FROM company_inventory_movements m
        LEFT JOIN company_inventory_items i ON i.id = m.item_id
        WHERE {' AND '.join(clauses)}
        ORDER BY m.movement_date DESC, m.id DESC
        LIMIT ?
        """,
        tuple(params),
    )


def movement_totals(*, tenant_id: int, date_from: str = "", date_to: str = "") -> dict:
    """Incoming value + usage quantity within an optional date range."""
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if date_from:
        clauses.append("movement_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("movement_date <= ?")
        params.append(date_to)
    where = " AND ".join(clauses)
    row = db().execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN movement_type = 'incoming'
                              THEN COALESCE(total_cost, 0) ELSE 0 END), 0)
                AS incoming_value,
            COALESCE(SUM(CASE WHEN movement_type = 'usage'
                              THEN quantity ELSE 0 END), 0) AS usage_quantity,
            COALESCE(SUM(CASE WHEN movement_type = 'incoming'
                              THEN 1 ELSE 0 END), 0) AS incoming_count,
            COALESCE(SUM(CASE WHEN movement_type = 'usage'
                              THEN 1 ELSE 0 END), 0) AS usage_count
        FROM company_inventory_movements
        WHERE {where}
        """,
        tuple(params),
    ).fetchone()
    return dict(row) if row else {}


# ── expenses ─────────────────────────────────────────────────────


def add_expense(
    *,
    tenant_id: int,
    title: str,
    amount: float,
    category: str = "",
    expense_date: str = "",
    paid_to: str = "",
    payment_method: str = "",
    reference: str = "",
    notes: str = "",
    created_by_admin_id: Optional[int] = None,
) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO company_expenses
                (tenant_id, title, category, amount, expense_date, paid_to,
                 payment_method, reference, notes, created_by_admin_id,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id, title, category, amount, expense_date or now,
                paid_to or None, payment_method or None, reference or None,
                notes or None, created_by_admin_id, now, now,
            ),
        )
        exp_id = cur.lastrowid
    return _row(
        "SELECT * FROM company_expenses WHERE id = ?", (int(exp_id),)
    ) or {}


def list_expenses(
    *,
    tenant_id: int,
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
) -> list[dict]:
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if category:
        clauses.append("category = ?")
        params.append(category)
    if date_from:
        clauses.append("expense_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("expense_date <= ?")
        params.append(date_to)
    params.append(limit)
    return _rows(
        f"SELECT * FROM company_expenses WHERE {' AND '.join(clauses)} "
        "ORDER BY expense_date DESC, id DESC LIMIT ?",
        tuple(params),
    )


def expense_totals(*, tenant_id: int, date_from: str = "", date_to: str = "") -> dict:
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if date_from:
        clauses.append("expense_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("expense_date <= ?")
        params.append(date_to)
    where = " AND ".join(clauses)
    row = db().execute(
        f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count "
        f"FROM company_expenses WHERE {where}",
        tuple(params),
    ).fetchone()
    return dict(row) if row else {}


def expenses_by_category(
    *, tenant_id: int, date_from: str = "", date_to: str = ""
) -> list[dict]:
    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant_id]
    if date_from:
        clauses.append("expense_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("expense_date <= ?")
        params.append(date_to)
    where = " AND ".join(clauses)
    return _rows(
        f"""
        SELECT COALESCE(NULLIF(category, ''), 'غير مصنّف') AS category,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*) AS count
        FROM company_expenses
        WHERE {where}
        GROUP BY COALESCE(NULLIF(category, ''), 'غير مصنّف')
        ORDER BY total DESC
        """,
        tuple(params),
    )
