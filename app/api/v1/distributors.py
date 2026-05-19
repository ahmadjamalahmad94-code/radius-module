"""Distributor / scoped manager API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "scoped visibility model",
    "assigned batch ownership",
    "debt/profit ledger accounts",
    "permission enforcement tests",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/distributors", "distributors_list",
                       methods=["GET"], domain="distributors",
                       operation="list",
                       planned_slice="R2 distributor foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/distributors", "distributors_create",
                       methods=["POST"], domain="distributors",
                       operation="create",
                       planned_slice="R2 distributor foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/distributors/<int:distributor_id>/summary",
                       "distributors_summary", methods=["GET"],
                       domain="distributors", operation="summary",
                       planned_slice="R2 distributor foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/distributors/<int:distributor_id>/settle",
                       "distributors_settle", methods=["POST"],
                       domain="distributors", operation="settle",
                       planned_slice="R2 distributor foundation",
                       required_work=_WORK)
