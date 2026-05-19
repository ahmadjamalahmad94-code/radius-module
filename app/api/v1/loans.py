"""Subscriber loans / credit API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "loan records table",
    "settlement records table",
    "max loan limits",
    "approval and audit policy",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/loans", "loans_list", methods=["GET"],
                       domain="loans", operation="list",
                       planned_slice="R2 loans foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/loans", "loans_create", methods=["POST"],
                       domain="loans", operation="create",
                       planned_slice="R2 loans foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/loans/<int:loan_id>", "loans_get", methods=["GET"],
                       domain="loans", operation="get",
                       planned_slice="R2 loans foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/loans/<int:loan_id>/settle", "loans_settle",
                       methods=["POST"], domain="loans", operation="settle",
                       planned_slice="R2 loans foundation",
                       required_work=_WORK)
