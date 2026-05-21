"""Lifecycle retention policy API."""
from __future__ import annotations

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok
from ...radius.services import lifecycle


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/lifecycle/policies", "lifecycle_policies",
                    require_api_token(lifecycle_policies), methods=["GET", "POST"])
    bp.add_url_rule("/lifecycle/policies/<int:policy_id>", "lifecycle_policy",
                    require_api_token(lifecycle_policy), methods=["PATCH"])
    bp.add_url_rule("/lifecycle/policies/<int:policy_id>/disable",
                    "lifecycle_policy_disable",
                    require_api_token(lifecycle_policy_disable), methods=["POST"])
    bp.add_url_rule("/lifecycle/preview", "lifecycle_preview",
                    require_api_token(lifecycle_preview), methods=["POST"])
    bp.add_url_rule("/lifecycle/run", "lifecycle_run",
                    require_api_token(lifecycle_run), methods=["POST"])


def _validation_error(exc: lifecycle.LifecycleValidationError):
    return fail(exc.code, exc.message, status=422)


def lifecycle_policies():
    if request.method == "GET":
        return ok({"items": lifecycle.list_policies(_tid(), entity_type=request.args.get("entity_type", ""))})
    try:
        policy = lifecycle.create_policy(_tid(), _json(), actor=_actor())
    except lifecycle.LifecycleValidationError as exc:
        return _validation_error(exc)
    return ok(policy, status=201)


def lifecycle_policy(policy_id: int):
    try:
        policy = lifecycle.update_policy(_tid(), policy_id, _json(), actor=_actor())
    except lifecycle.LifecycleValidationError as exc:
        return _validation_error(exc)
    if not policy:
        return fail("not_found", "السياسة غير موجودة.", status=404)
    return ok(policy)


def lifecycle_policy_disable(policy_id: int):
    policy = lifecycle.disable_policy(_tid(), policy_id, actor=_actor())
    if not policy:
        return fail("not_found", "السياسة غير موجودة.", status=404)
    return ok(policy)


def lifecycle_preview():
    body = _json()
    limit = max(1, min(int(body.get("limit") or 500), 2000))
    return ok(lifecycle.preview(_tid(), limit=limit))


def lifecycle_run():
    body = _json()
    limit = max(1, min(int(body.get("limit") or 500), 2000))
    return ok(lifecycle.run(_tid(), actor=_actor(), limit=limit))
