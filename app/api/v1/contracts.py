"""مساعدات عقود API لخارطة العملاء.

هذه المسارات تثبّت شكل API المستقبلي بدون ادعاء أن التنفيذ التجاري جاهز.
"""
from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, jsonify

from ..auth import require_api_token
from ..responses import _meta


def not_implemented_contract(
    *,
    domain: str,
    operation: str,
    message: str,
    planned_slice: str,
    required_work: list[str] | None = None,
):
    meta = _meta()
    meta.update({"domain": domain, "status": "planned"})
    return jsonify({
        "ok": False,
        "error": {
            "code": "not_implemented",
            "message": message,
            "details": {
                "domain": domain,
                "operation": operation,
                "planned_slice": planned_slice,
                "required_work": required_work or [],
            },
        },
        "meta": meta,
    }), 501


def contract_view(
    *,
    domain: str,
    operation: str,
    planned_slice: str,
    required_work: list[str] | None = None,
) -> Callable:
    message = f"عقد API الخاص بـ {domain} غير مفعّل لهذا المسار."

    def _view(**_route_values):
        return not_implemented_contract(
            domain=domain,
            operation=operation,
            message=message,
            planned_slice=planned_slice,
            required_work=required_work,
        )

    _view.__name__ = f"{domain}_{operation}".replace(".", "_").replace("-", "_")
    return _view


def add_contract_route(
    bp: Blueprint,
    rule: str,
    endpoint: str,
    *,
    methods: list[str],
    domain: str,
    operation: str,
    planned_slice: str,
    required_work: list[str] | None = None,
) -> None:
    bp.add_url_rule(
        rule,
        endpoint,
        require_api_token(contract_view(
            domain=domain,
            operation=operation,
            planned_slice=planned_slice,
            required_work=required_work,
        )),
        methods=methods,
    )
