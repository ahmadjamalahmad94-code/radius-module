"""Payments / partial payments API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "payment records table",
    "partial allocation rules",
    "discount/custom price policy",
    "ledger posting service",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/payments", "payments_list", methods=["GET"],
                       domain="payments", operation="list",
                       planned_slice="R2 payments foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/payments", "payments_create", methods=["POST"],
                       domain="payments", operation="create",
                       planned_slice="R2 payments foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/payments/<int:payment_id>/void",
                       "payments_void", methods=["POST"],
                       domain="payments", operation="void",
                       planned_slice="R2 payments foundation",
                       required_work=_WORK)
