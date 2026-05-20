from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, request

from ...radius.db.repos import api_tokens_repo, audit_repo
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _serialize_token(record: dict) -> dict:
    return {
        "id": record["id"],
        "tenant_id": record["tenant_id"],
        "name": record["name"],
        "scopes": list(record.get("scopes") or []),
        "last_used_at": record.get("last_used_at"),
        "expires_at": record.get("expires_at"),
        "revoked": bool(record.get("revoked")),
        "created_by": record.get("created_by") or 0,
        "created_at": record.get("created_at"),
    }


def _parse_expires_at(raw):
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValueError("expires_at must be ISO string")
    return datetime.fromisoformat(raw.replace("Z", ""))


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/tokens",
        "tokens_list",
        require_api_token(tokens_list),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/tokens",
        "tokens_create",
        require_api_token(tokens_create),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/tokens/<int:token_id>/revoke",
        "tokens_revoke",
        require_api_token(tokens_revoke),
        methods=["POST"],
    )


def tokens_list():
    items = [_serialize_token(t) for t in api_tokens_repo.list_tokens(_tid())]
    return ok({"items": items, "count": len(items)})


def tokens_create():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return fail("validation_error", "name is required", status=422)
    scopes = body.get("scopes")
    if scopes is None:
        scopes = ["admin:full"]
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        return fail("validation_error", "scopes must be a list of strings", status=422)
    try:
        expires_at = _parse_expires_at(body.get("expires_at"))
    except ValueError as exc:
        return fail("validation_error", str(exc), status=422)
    record, plain = api_tokens_repo.create_token(
        tenant_id=_tid(),
        name=name,
        scopes=scopes,
        created_by=int(getattr(g, "admin_id", 0) or 0),
        expires_at=expires_at,
    )
    audit_repo.record(
        tenant_id=_tid(),
        actor=_actor(),
        action="create",
        target_type="api_token",
        target_id=str(record["id"]),
        payload={"name": name, "scopes": scopes},
    )
    data = _serialize_token(record)
    data["token"] = plain
    data["token_shown_once"] = True
    return ok(data, status=201)


def tokens_revoke(token_id: int):
    existing = next(
        (t for t in api_tokens_repo.list_tokens(_tid()) if int(t["id"]) == int(token_id)),
        None,
    )
    if not existing:
        return fail("not_found", "token not found", status=404)
    api_tokens_repo.revoke_token(_tid(), token_id)
    audit_repo.record(
        tenant_id=_tid(),
        actor=_actor(),
        action="revoke",
        target_type="api_token",
        target_id=str(token_id),
        payload={"name": existing["name"]},
    )
    return ok({"id": token_id, "revoked": True})
