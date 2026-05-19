"""Recycle bin / soft-delete API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "additive archive columns",
    "restore policy",
    "financial void/reversal policy",
    "delete compatibility migration",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/recycle-bin", "recycle_bin_list",
                       methods=["GET"], domain="recycle_bin",
                       operation="list",
                       planned_slice="R2 archive foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/recycle-bin/<entity_type>/<int:entity_id>/archive",
                       "recycle_bin_archive", methods=["POST"],
                       domain="recycle_bin", operation="archive",
                       planned_slice="R2 archive foundation",
                       required_work=_WORK)
    add_contract_route(bp, "/recycle-bin/<entity_type>/<int:entity_id>/restore",
                       "recycle_bin_restore", methods=["POST"],
                       domain="recycle_bin", operation="restore",
                       planned_slice="R2 archive foundation",
                       required_work=_WORK)
