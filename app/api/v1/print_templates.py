"""Card print template API contracts."""
from __future__ import annotations

from flask import Blueprint

from .contracts import add_contract_route

_WORK = [
    "print template table",
    "template versioning",
    "safe render/export service",
    "batch/template binding",
]


def register(bp: Blueprint) -> None:
    add_contract_route(bp, "/print-templates",
                       "print_templates_list", methods=["GET"],
                       domain="print_templates", operation="list",
                       planned_slice="R3 card print templates",
                       required_work=_WORK)
    add_contract_route(bp, "/print-templates",
                       "print_templates_create", methods=["POST"],
                       domain="print_templates", operation="create",
                       planned_slice="R3 card print templates",
                       required_work=_WORK)
    add_contract_route(bp, "/print-templates/<int:template_id>/render",
                       "print_templates_render", methods=["POST"],
                       domain="print_templates", operation="render",
                       planned_slice="R3 card print templates",
                       required_work=_WORK)
