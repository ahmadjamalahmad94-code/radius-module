"""Customer service report API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "ledger-backed report service",
    "date-range validation",
    "immutable financial source data",
    "distributor/accounting aggregation tests",
]


def register(bp: Blueprint) -> None:
    for slug, operation in (
        ("sales", "sales"),
        ("payments", "payments"),
        ("activations", "activations"),
        ("card-sales", "card_sales"),
        ("profit-loss", "profit_loss"),
        ("distributor-debts", "distributor_debts"),
    ):
        add_contract_route(bp, f"/reports/{slug}", f"reports_{operation}",
                           methods=["GET"], domain="reports",
                           operation=operation,
                           planned_slice="R3 reports foundation",
                           required_work=_WORK)
