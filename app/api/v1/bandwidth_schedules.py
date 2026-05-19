"""Time-based / pressure-based bandwidth schedule API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "schedule table",
    "overlap validation",
    "timezone policy",
    "worker/apply logs",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/bandwidth-schedules",
                       "bandwidth_schedules_list", methods=["GET"],
                       domain="bandwidth_schedules", operation="list",
                       planned_slice="R3 bandwidth schedules",
                       required_work=_WORK)
    add_contract_route(bp, "/bandwidth-schedules",
                       "bandwidth_schedules_create", methods=["POST"],
                       domain="bandwidth_schedules", operation="create",
                       planned_slice="R3 bandwidth schedules",
                       required_work=_WORK)
    add_contract_route(bp, "/bandwidth-schedules/<int:schedule_id>/apply",
                       "bandwidth_schedules_apply", methods=["POST"],
                       domain="bandwidth_schedules", operation="apply",
                       planned_slice="R3 bandwidth schedules",
                       required_work=_WORK)
