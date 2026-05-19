"""Accounting ledger API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "append-only ledger table",
    "entry source typing",
    "void/reversal entries",
    "period close policy",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/ledger", "ledger_list", methods=["GET"],
                       domain="ledger", operation="list",
                       planned_slice="R2 ledger foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/ledger/void", "ledger_void", methods=["POST"],
                       domain="ledger", operation="void",
                       planned_slice="R2 ledger foundation",
                       required_work=_WORK)
