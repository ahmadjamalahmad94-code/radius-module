"""Company Inventory & Expenses — internal Finance notebook web routes.

Isolated internal operational tracking. These routes do NOT touch the
ledger, payments, customer/distributor balances, card sales,
subscriptions, revenue, or profit. See
docs/company_inventory_expenses/COMPANY_INVENTORY_EXPENSES.md.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.company_inventory import CompanyInventoryError, CompanyInventoryService

_BASE = "/finance/company-inventory-expenses"


def register_company_inventory_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        _BASE, "company_inventory", company_inventory_page, methods=["GET"]
    )
    bp.add_url_rule(
        _BASE + "/items", "company_inventory_item_create",
        company_inventory_item_create, methods=["POST"],
    )
    bp.add_url_rule(
        _BASE + "/incoming", "company_inventory_incoming",
        company_inventory_incoming, methods=["POST"],
    )
    bp.add_url_rule(
        _BASE + "/usage", "company_inventory_usage",
        company_inventory_usage, methods=["POST"],
    )
    bp.add_url_rule(
        _BASE + "/expenses", "company_expense_add",
        company_expense_add, methods=["POST"],
    )
    bp.add_url_rule(
        _BASE + "/items/<int:item_id>/deactivate",
        "company_inventory_item_deactivate",
        company_inventory_item_deactivate, methods=["POST"],
    )


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "system"


def _admin_id():
    return session.get("admin_id")


def _field(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _svc() -> CompanyInventoryService:
    return CompanyInventoryService()


def _redirect(tab: str = ""):
    target = url_for("radius.company_inventory")
    if tab:
        target = f"{target}#{tab}"
    return redirect(target)


# ── page ─────────────────────────────────────────────────────────


def company_inventory_page():
    svc = _svc()
    tid = _tid()
    reports = svc.reports(
        tenant_id=tid,
        date_from=request.args.get("date_from", "").strip(),
        date_to=request.args.get("date_to", "").strip(),
        item_id=request.args.get("item_id") or None,
        movement_type=request.args.get("movement_type", "").strip(),
        expense_category=request.args.get("expense_category", "").strip(),
    )
    return render_template(
        "radius/company_inventory_expenses.html",
        active="company_inventory",
        summary=svc.summary_cards(tenant_id=tid),
        overview=svc.overview(tenant_id=tid),
        items=svc.items_for_select(tenant_id=tid),
        reports=reports,
        filters={
            "date_from": request.args.get("date_from", "").strip(),
            "date_to": request.args.get("date_to", "").strip(),
            "item_id": request.args.get("item_id", "").strip(),
            "movement_type": request.args.get("movement_type", "").strip(),
            "expense_category": request.args.get("expense_category", "").strip(),
        },
    )


# ── actions ──────────────────────────────────────────────────────


def company_inventory_item_create():
    try:
        _svc().create_item(
            tenant_id=_tid(),
            actor=_actor(),
            name=_field("name"),
            category=_field("category"),
            unit=_field("unit"),
            low_stock_threshold=_field("low_stock_threshold") or None,
            notes=_field("notes"),
        )
        flash("تم إنشاء الصنف.", "success")
    except CompanyInventoryError as exc:
        flash(str(exc), "error")
    return _redirect("inventory")


def company_inventory_incoming():
    try:
        _svc().add_incoming(
            tenant_id=_tid(),
            actor=_actor(),
            item_id=_field("item_id") or None,
            item_name=_field("item_name"),
            category=_field("category"),
            unit=_field("unit"),
            quantity=_field("quantity"),
            unit_cost=_field("unit_cost") or None,
            supplier=_field("supplier"),
            reference=_field("reference"),
            movement_date=_field("movement_date"),
            notes=_field("notes"),
            created_by_admin_id=_admin_id(),
        )
        flash("تم تسجيل وارد المخزون.", "success")
    except CompanyInventoryError as exc:
        flash(str(exc), "error")
    return _redirect("incoming")


def company_inventory_usage():
    try:
        _svc().record_usage(
            tenant_id=_tid(),
            actor=_actor(),
            item_id=int(_field("item_id") or 0),
            quantity=_field("quantity"),
            usage_reason=_field("usage_reason"),
            location=_field("location"),
            technician=_field("technician"),
            related_customer_id=_field("related_customer_id") or None,
            movement_date=_field("movement_date"),
            notes=_field("notes"),
            created_by_admin_id=_admin_id(),
        )
        flash("تم تسجيل صرف المخزون.", "success")
    except CompanyInventoryError as exc:
        flash(str(exc), "error")
    return _redirect("usage")


def company_expense_add():
    try:
        _svc().add_expense(
            tenant_id=_tid(),
            actor=_actor(),
            title=_field("title"),
            amount=_field("amount"),
            category=_field("category"),
            expense_date=_field("expense_date"),
            paid_to=_field("paid_to"),
            payment_method=_field("payment_method"),
            reference=_field("reference"),
            notes=_field("notes"),
            created_by_admin_id=_admin_id(),
        )
        flash("تم تسجيل المصروف.", "success")
    except CompanyInventoryError as exc:
        flash(str(exc), "error")
    return _redirect("expenses")


def company_inventory_item_deactivate(item_id: int):
    try:
        _svc().deactivate_item(tenant_id=_tid(), actor=_actor(), item_id=item_id)
        flash("تم تعطيل الصنف.", "success")
    except CompanyInventoryError as exc:
        flash(str(exc), "error")
    return _redirect("inventory")
