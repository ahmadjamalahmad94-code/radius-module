"""Hub 1 — الفواتير والكوبونات (Invoices & Vouchers) consolidated page.

UI-ONLY consolidation: this hub renders the existing invoices and
vouchers data side-by-side as tabs, with the "create" actions in
floating <dialog> modals. It does NOT change any invoice/voucher
business logic — every POST still targets the original endpoints in
saas_modules.py (inv_create, inv_status, vch_generate, vch_revoke),
and reads go through the same repos (invoices_repo, vouchers_repo).

See docs/finance_hubs/FINANCE_HUBS_PLAN.md (Hub 1).
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, session

from ..db.repos import invoices_repo, plans_repo, subscribers_repo, vouchers_repo

_BASE = "/finance/billing"
_TABS = ("invoices", "vouchers")


def register_finance_billing_routes(bp: Blueprint) -> None:
    bp.add_url_rule(_BASE, "billing_hub", billing_hub, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def billing_hub():
    tid = _tid()
    tab = request.args.get("tab", "invoices").strip()
    if tab not in _TABS:
        tab = "invoices"
    status = request.args.get("status") or None

    invoices = invoices_repo.list_all(tid, status=status if tab == "invoices" else None, limit=500)
    vouchers = vouchers_repo.list_all(tid, status=status if tab == "vouchers" else None, limit=500)
    plans = plans_repo.list_plans(tid, limit=500)

    return render_template(
        "radius/finance_billing.html",
        active="invoices",
        tab=tab,
        status=status or "",
        invoices=invoices,
        inv_stats=invoices_repo.stats(tid),
        vouchers=vouchers,
        vch_stats=vouchers_repo.stats(tid),
        plans=plans,
        plans_map={p.id: p for p in plans},
        # Subscribers are only needed to populate the "new invoice"
        # modal's picker (hbsel search filters client-side). The previous
        # limit=500 silently dropped half the base with ~1000 subscribers,
        # so the picker looked incomplete — raise the cap to cover it.
        subs=subscribers_repo.list_subscribers(tid, limit=2000),
    )
