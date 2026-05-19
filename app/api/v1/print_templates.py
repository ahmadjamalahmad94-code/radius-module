"""Card print template API foundation."""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.operations import get_operations_service
    return get_operations_service()


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/print-templates",
                    "print_templates_list",
                    require_api_token(print_templates_list), methods=["GET"])
    bp.add_url_rule("/print-templates",
                    "print_templates_create",
                    require_api_token(print_templates_create), methods=["POST"])
    bp.add_url_rule("/print-templates/<int:template_id>/render",
                    "print_templates_render",
                    require_api_token(print_templates_render), methods=["POST"])


def print_templates_list():
    try:
        limit = min(int(request.args.get("limit") or 200), 1000)
        offset = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        return fail("validation_error", "limit/offset must be int", status=422)
    items = _svc().list_print_templates(tenant_id=_tid(), limit=limit, offset=offset)
    return ok({"items": items, "count": len(items)})


def print_templates_create():
    body = request.get_json(silent=True) or {}
    try:
        template = _svc().create_print_template(
            tenant_id=_tid(), actor=_actor(), data=body
        )
    except RadiusValidationError as e:
        return fail("validation_error", e.message, status=422)
    except RadiusError as e:
        return fail("internal_error", e.message, status=500)
    return ok({"template": template}, status=201)


def print_templates_render(template_id: int):
    body = request.get_json(silent=True) or {}
    try:
        result = _svc().render_print_template_preview(
            tenant_id=_tid(),
            template_id=template_id,
            sample=body.get("sample") if isinstance(body.get("sample"), dict) else None,
        )
    except RadiusNotFound as e:
        return fail("not_found", e.message, status=404)
    return ok(result)
