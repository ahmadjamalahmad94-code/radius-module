"""
Network Policy Center — JSON API.

Three sub-services share one URL tree:

  /v1/network-policy/remote-access/policies            …
  /v1/network-policy/web-block/policies                …
  /v1/network-policy/walled-garden/policies            …

Each supports CRUD + a `/preview` endpoint that returns the
rendered RouterOS script bodies (forward + rollback) plus a
plan summary. Apply is intentionally **not** wired here —
that's Phase 5; this layer is dry-run only.

Authorisation flows through `require_api_token`, the same
guard the rest of the v1 API uses. Tenant scoping comes from
`g.tenant_id`. State-changing operations (`policy_created`,
`policy_updated`, `policy_deleted`, `target_added`,
`target_removed`, `preview_generated`) record an audit row via
the existing `RadiusAuditService` so the audit timeline picks
up NPC activity automatically.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from flask import Blueprint, g, request

from ...radius.db.repos import (
    npc_common as nc,
    npc_deployments_repo as dep_repo,
    npc_remote_access_repo as ra_repo,
    npc_scripts_repo as scripts_repo,
    npc_walled_garden_repo as wg_repo,
    npc_web_block_repo as wb_repo,
)
from ...radius.services import (
    npc_audit_events as ev,
    npc_beginner_explainer as beginner_svc,
    npc_blast_radius as blast_svc,
    npc_canary_planner as canary_svc,
    npc_conflict_detector as conflict_svc,
    npc_dependency_detector as dependency_svc,
    npc_impact_analyzer as impact_svc,
    npc_policy_health as health_svc,
    npc_recommendations as rec_svc,
    npc_remote_access_planner as ra_planner,
    npc_script_renderer as renderer,
    npc_walled_garden_planner as wg_planner,
    npc_web_block_planner as wb_planner,
)
from ...radius.services.audit import get_audit_service
from ..auth import require_api_token
from ..responses import fail, ok


# ─── Shared helpers ──────────────────────────────────────────


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _audit():
    return get_audit_service()


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _bool(v: Any) -> bool:
    """Accept JSON true/false, string '1'/'0', or int."""
    if v is True or v == 1 or v == "1":
        return True
    if v is False or v == 0 or v == "0" or v is None:
        return False
    return bool(v)


def _emit_audit(
    *, service: str, action: str, policy_id: int,
    router_id: Optional[int] = None,
    script_hash: str = "",
    error: str = "",
    **extra: Any,
) -> None:
    """Common audit-row emission. Failures swallowed by the
    audit service so a logging mishap can't fail the request."""
    payload = ev.build_payload(
        service=service, policy_id=policy_id,
        router_id=router_id, script_hash=script_hash,
        error=error, **extra,
    )
    _audit().record(
        actor=_actor(),
        action=action,
        target_type=ev.target_type_for(service),
        target_id=str(policy_id),
        payload=payload.as_dict(),
        router_id=router_id,
    )


# ─── Serialisers ─────────────────────────────────────────────


def _serialize_remote(row: dict) -> dict:
    """Drop nothing — the remote_access table carries no
    secrets per the Phase-1 schema invariant."""
    return dict(row)


def _serialize_block(row: dict) -> dict:
    return dict(row)


def _serialize_garden(row: dict) -> dict:
    return dict(row)


def _serialize_target(row: dict) -> dict:
    return dict(row)


# ─── Service routing helper ──────────────────────────────────


# The URL-friendly form of each service discriminator. We
# accept the dashed form externally so URLs stay idiomatic and
# the planner/repo `service` literals stay snake_case.
_URL_TO_SERVICE = {
    "remote-access": nc.SERVICE_REMOTE_ACCESS,
    "web-block":     nc.SERVICE_WEB_BLOCK,
    "walled-garden": nc.SERVICE_WALLED_GARDEN,
}


def _service_or_404(url_part: str) -> Optional[str]:
    return _URL_TO_SERVICE.get(url_part)


# ─── Registration ────────────────────────────────────────────


def register(bp: Blueprint) -> None:
    # Remote Access
    bp.add_url_rule(
        "/network-policy/remote-access/policies",
        "npc_ra_list",
        require_api_token(ra_list), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies",
        "npc_ra_create",
        require_api_token(ra_create), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>",
        "npc_ra_get",
        require_api_token(ra_get), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>",
        "npc_ra_patch",
        require_api_token(ra_patch), methods=["PATCH"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>",
        "npc_ra_delete",
        require_api_token(ra_delete), methods=["DELETE"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>/preview",
        "npc_ra_preview",
        require_api_token(ra_preview), methods=["POST"])

    # Web Block
    bp.add_url_rule(
        "/network-policy/web-block/policies",
        "npc_wb_list",
        require_api_token(wb_list), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/web-block/policies",
        "npc_wb_create",
        require_api_token(wb_create), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>",
        "npc_wb_get",
        require_api_token(wb_get), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>",
        "npc_wb_patch",
        require_api_token(wb_patch), methods=["PATCH"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>",
        "npc_wb_delete",
        require_api_token(wb_delete), methods=["DELETE"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/targets",
        "npc_wb_target_list",
        require_api_token(wb_target_list), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/targets",
        "npc_wb_target_add",
        require_api_token(wb_target_add), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/targets/<int:target_id>",
        "npc_wb_target_delete",
        require_api_token(wb_target_delete), methods=["DELETE"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/preview",
        "npc_wb_preview",
        require_api_token(wb_preview), methods=["POST"])

    # Walled Garden
    bp.add_url_rule(
        "/network-policy/walled-garden/policies",
        "npc_wg_list",
        require_api_token(wg_list), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies",
        "npc_wg_create",
        require_api_token(wg_create), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>",
        "npc_wg_get",
        require_api_token(wg_get), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>",
        "npc_wg_patch",
        require_api_token(wg_patch), methods=["PATCH"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>",
        "npc_wg_delete",
        require_api_token(wg_delete), methods=["DELETE"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/entries",
        "npc_wg_entry_list",
        require_api_token(wg_entry_list), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/entries",
        "npc_wg_entry_add",
        require_api_token(wg_entry_add), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/entries/<int:entry_id>",
        "npc_wg_entry_delete",
        require_api_token(wg_entry_delete), methods=["DELETE"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/preview",
        "npc_wg_preview",
        require_api_token(wg_preview), methods=["POST"])


# ─── Remote Access views ─────────────────────────────────────


def ra_list():
    router_id = request.args.get("router_id")
    if router_id is not None:
        try:
            rid = int(router_id)
        except ValueError:
            return fail("validation_error",
                        "معرّف الراوتر يجب أن يكون رقمًا صحيحًا.", status=422)
        rows = ra_repo.list_for_router(_tid(), rid)
    else:
        rows = ra_repo.list_for_tenant(_tid())
    return ok({
        "items": [_serialize_remote(r) for r in rows],
        "count": len(rows),
    })


def ra_create():
    body = _body()
    name = (body.get("name") or "").strip()
    router_id = body.get("router_id")
    if not name:
        return fail("validation_error", "name مطلوب",
                    status=422)
    if router_id is None:
        return fail("validation_error", "router_id مطلوب",
                    status=422)
    try:
        pid = ra_repo.create(
            tenant_id=_tid(), router_id=int(router_id),
            name=name,
            slug=body.get("slug"),
            allow_winbox=_bool(body.get("allow_winbox", True)),
            allow_ssh=_bool(body.get("allow_ssh")),
            allow_api=_bool(body.get("allow_api")),
            allow_api_ssl=_bool(body.get("allow_api_ssl")),
            allow_webfig_http=_bool(body.get("allow_webfig_http")),
            allow_webfig_https=_bool(
                body.get("allow_webfig_https", True)),
            source_address_list=str(
                body.get("source_address_list") or ""),
            expires_at=str(body.get("expires_at") or ""),
            reason=str(body.get("reason") or ""),
            enabled=_bool(body.get("enabled", True)),
        )
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    row = ra_repo.get_by_id(_tid(), pid)
    _emit_audit(
        service=nc.SERVICE_REMOTE_ACCESS,
        action=ev.EVT_RA_POLICY_CREATED,
        policy_id=pid, router_id=int(router_id),
    )
    return ok(_serialize_remote(row), status=201)


def ra_get(policy_id: int):
    row = ra_repo.get_by_id(_tid(), policy_id)
    if not row:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    return ok(_serialize_remote(row))


def ra_patch(policy_id: int):
    existing = ra_repo.get_by_id(_tid(), policy_id)
    if not existing:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    body = _body()
    try:
        updated = ra_repo.update(_tid(), policy_id, **body)
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    _emit_audit(
        service=nc.SERVICE_REMOTE_ACCESS,
        action=ev.EVT_RA_POLICY_UPDATED,
        policy_id=policy_id,
        router_id=existing["router_id"],
    )
    return ok(_serialize_remote(updated))


def ra_delete(policy_id: int):
    existing = ra_repo.get_by_id(_tid(), policy_id)
    if not existing:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    ra_repo.delete(_tid(), policy_id)
    _emit_audit(
        service=nc.SERVICE_REMOTE_ACCESS,
        action=ev.EVT_RA_POLICY_DELETED,
        policy_id=policy_id,
        router_id=existing["router_id"],
    )
    return ok({"deleted": policy_id})


def ra_preview(policy_id: int):
    row = ra_repo.get_by_id(_tid(), policy_id)
    if not row:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    plan = ra_planner.plan(row)
    return _finalize_preview(
        service=nc.SERVICE_REMOTE_ACCESS,
        policy_id=policy_id,
        router_id=row["router_id"],
        plan=plan,
        policy=row,
        targets=(),
        audit_action=ev.EVT_RA_PREVIEW_GENERATED,
    )


# ─── Web Block views ─────────────────────────────────────────


def wb_list():
    router_id = request.args.get("router_id")
    if router_id is not None:
        try:
            rid = int(router_id)
        except ValueError:
            return fail("validation_error",
                        "معرّف الراوتر يجب أن يكون رقمًا صحيحًا.", status=422)
        rows = wb_repo.list_policies_for_router(_tid(), rid)
    else:
        rows = wb_repo.list_policies_for_tenant(_tid())
    return ok({
        "items": [_serialize_block(r) for r in rows],
        "count": len(rows),
    })


def wb_create():
    body = _body()
    name = (body.get("name") or "").strip()
    router_id = body.get("router_id")
    if not name:
        return fail("validation_error", "name مطلوب",
                    status=422)
    if router_id is None:
        return fail("validation_error", "router_id مطلوب",
                    status=422)
    try:
        pid = wb_repo.create_policy(
            tenant_id=_tid(), router_id=int(router_id),
            name=name,
            slug=body.get("slug"),
            scope=str(body.get("scope")
                      or wb_repo.SCOPE_ALL_USERS),
            schedule_id=str(body.get("schedule_id") or ""),
            fail_open=_bool(body.get("fail_open", True)),
            enabled=_bool(body.get("enabled", True)),
        )
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    row = wb_repo.get_policy(_tid(), pid)
    _emit_audit(
        service=nc.SERVICE_WEB_BLOCK,
        action=ev.EVT_WB_POLICY_CREATED,
        policy_id=pid, router_id=int(router_id),
    )
    return ok(_serialize_block(row), status=201)


def wb_get(policy_id: int):
    row = wb_repo.get_policy(_tid(), policy_id)
    if not row:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    return ok(_serialize_block(row))


def wb_patch(policy_id: int):
    existing = wb_repo.get_policy(_tid(), policy_id)
    if not existing:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    try:
        updated = wb_repo.update_policy(
            _tid(), policy_id, **_body())
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    _emit_audit(
        service=nc.SERVICE_WEB_BLOCK,
        action=ev.EVT_WB_POLICY_UPDATED,
        policy_id=policy_id,
        router_id=existing["router_id"],
    )
    return ok(_serialize_block(updated))


def wb_delete(policy_id: int):
    existing = wb_repo.get_policy(_tid(), policy_id)
    if not existing:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    wb_repo.delete_policy(_tid(), policy_id)
    _emit_audit(
        service=nc.SERVICE_WEB_BLOCK,
        action=ev.EVT_WB_POLICY_DELETED,
        policy_id=policy_id,
        router_id=existing["router_id"],
    )
    return ok({"deleted": policy_id})


def wb_target_list(policy_id: int):
    policy = wb_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    rows = wb_repo.list_targets(policy_id)
    return ok({
        "items": [_serialize_target(r) for r in rows],
        "count": len(rows),
        "counts": wb_repo.target_counts(policy_id),
    })


def wb_target_add(policy_id: int):
    policy = wb_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    body = _body()
    try:
        tid = wb_repo.add_target(
            policy_id=policy_id,
            value=str(body.get("value") or ""),
            target_type=str(body.get("target_type") or ""),
            normalized_value=body.get("normalized_value"),
            category=str(body.get("category") or "custom"),
            status=str(body.get("status")
                       or wb_repo.STATUS_ACTIVE),
            notes=str(body.get("notes") or ""),
        )
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    _emit_audit(
        service=nc.SERVICE_WEB_BLOCK,
        action=ev.EVT_WB_TARGET_ADDED,
        policy_id=policy_id,
        router_id=policy["router_id"],
        target_id=tid,
    )
    return ok(_serialize_target(wb_repo.get_target(tid)),
              status=201)


def wb_target_delete(policy_id: int, target_id: int):
    policy = wb_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    target = wb_repo.get_target(target_id)
    if not target or int(target["policy_id"]) != policy_id:
        return fail("not_found",
                    f"target {target_id} غير موجود", status=404)
    wb_repo.delete_target(target_id)
    _emit_audit(
        service=nc.SERVICE_WEB_BLOCK,
        action=ev.EVT_WB_TARGET_REMOVED,
        policy_id=policy_id,
        router_id=policy["router_id"],
        target_id=target_id,
    )
    return ok({"deleted": target_id})


def wb_preview(policy_id: int):
    policy = wb_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    targets = wb_repo.list_targets(policy_id)
    plan = wb_planner.plan(policy, targets)
    return _finalize_preview(
        service=nc.SERVICE_WEB_BLOCK,
        policy_id=policy_id,
        router_id=policy["router_id"],
        plan=plan,
        policy=policy,
        targets=targets,
        audit_action=ev.EVT_WB_PREVIEW_GENERATED,
    )


# ─── Walled Garden views ─────────────────────────────────────


def wg_list():
    router_id = request.args.get("router_id")
    if router_id is not None:
        try:
            rid = int(router_id)
        except ValueError:
            return fail("validation_error",
                        "معرّف الراوتر يجب أن يكون رقمًا صحيحًا.", status=422)
        rows = wg_repo.list_policies_for_router(_tid(), rid)
    else:
        rows = wg_repo.list_policies_for_tenant(_tid())
    return ok({
        "items": [_serialize_garden(r) for r in rows],
        "count": len(rows),
    })


def wg_create():
    body = _body()
    name = (body.get("name") or "").strip()
    router_id = body.get("router_id")
    if not name:
        return fail("validation_error", "name مطلوب",
                    status=422)
    if router_id is None:
        return fail("validation_error", "router_id مطلوب",
                    status=422)
    try:
        pid = wg_repo.create_policy(
            tenant_id=_tid(), router_id=int(router_id),
            name=name,
            slug=body.get("slug"),
            hotspot_profile=str(body.get("hotspot_profile") or ""),
            enabled=_bool(body.get("enabled", True)),
        )
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    row = wg_repo.get_policy(_tid(), pid)
    _emit_audit(
        service=nc.SERVICE_WALLED_GARDEN,
        action=ev.EVT_WG_POLICY_CREATED,
        policy_id=pid, router_id=int(router_id),
    )
    return ok(_serialize_garden(row), status=201)


def wg_get(policy_id: int):
    row = wg_repo.get_policy(_tid(), policy_id)
    if not row:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    return ok(_serialize_garden(row))


def wg_patch(policy_id: int):
    existing = wg_repo.get_policy(_tid(), policy_id)
    if not existing:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    try:
        updated = wg_repo.update_policy(
            _tid(), policy_id, **_body())
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    _emit_audit(
        service=nc.SERVICE_WALLED_GARDEN,
        action=ev.EVT_WG_POLICY_UPDATED,
        policy_id=policy_id,
        router_id=existing["router_id"],
    )
    return ok(_serialize_garden(updated))


def wg_delete(policy_id: int):
    existing = wg_repo.get_policy(_tid(), policy_id)
    if not existing:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    wg_repo.delete_policy(_tid(), policy_id)
    _emit_audit(
        service=nc.SERVICE_WALLED_GARDEN,
        action=ev.EVT_WG_POLICY_DELETED,
        policy_id=policy_id,
        router_id=existing["router_id"],
    )
    return ok({"deleted": policy_id})


def wg_entry_list(policy_id: int):
    policy = wg_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    rows = wg_repo.list_entries(policy_id)
    return ok({
        "items": [_serialize_target(r) for r in rows],
        "count": len(rows),
        "counts": wg_repo.entry_counts(policy_id),
    })


def wg_entry_add(policy_id: int):
    policy = wg_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    body = _body()
    try:
        eid = wg_repo.add_entry(
            policy_id=policy_id,
            value=str(body.get("value") or ""),
            entry_type=str(body.get("entry_type") or ""),
            normalized_value=body.get("normalized_value"),
            dst_port=str(body.get("dst_port") or ""),
            protocol=str(body.get("protocol") or ""),
            status=str(body.get("status")
                       or wg_repo.STATUS_ACTIVE),
            notes=str(body.get("notes") or ""),
        )
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    _emit_audit(
        service=nc.SERVICE_WALLED_GARDEN,
        action=ev.EVT_WG_TARGET_ADDED,
        policy_id=policy_id,
        router_id=policy["router_id"],
        entry_id=eid,
    )
    return ok(_serialize_target(wg_repo.get_entry(eid)),
              status=201)


def wg_entry_delete(policy_id: int, entry_id: int):
    policy = wg_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    entry = wg_repo.get_entry(entry_id)
    if not entry or int(entry["policy_id"]) != policy_id:
        return fail("not_found",
                    f"entry {entry_id} غير موجود", status=404)
    wg_repo.delete_entry(entry_id)
    _emit_audit(
        service=nc.SERVICE_WALLED_GARDEN,
        action=ev.EVT_WG_TARGET_REMOVED,
        policy_id=policy_id,
        router_id=policy["router_id"],
        entry_id=entry_id,
    )
    return ok({"deleted": entry_id})


def wg_preview(policy_id: int):
    policy = wg_repo.get_policy(_tid(), policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    entries = wg_repo.list_entries(policy_id)
    plan = wg_planner.plan(policy, entries)
    return _finalize_preview(
        service=nc.SERVICE_WALLED_GARDEN,
        policy_id=policy_id,
        router_id=policy["router_id"],
        plan=plan,
        policy=policy,
        targets=entries,
        audit_action=ev.EVT_WG_PREVIEW_GENERATED,
    )


# ─── Preview core ────────────────────────────────────────────


def _peer_policies_for(
    *, current_service: str, current_router_id: int,
) -> list[conflict_svc.PeerPolicy]:
    """Load every other tenant policy + its children once per
    preview, projected to the conflict-detector's PeerPolicy
    shape. Reads only — no MikroTik contact."""
    out: list[conflict_svc.PeerPolicy] = []

    # Remote access — no children.
    for row in ra_repo.list_for_tenant(_tid()):
        out.append(conflict_svc.PeerPolicy(
            service=nc.SERVICE_REMOTE_ACCESS,
            id=int(row["id"]), name=str(row["name"]),
            slug=str(row["slug"]),
            router_id=int(row["router_id"]),
            enabled=bool(row["enabled"]),
            children=(),
        ))

    # Web-block — pull targets.
    for row in wb_repo.list_policies_for_tenant(_tid()):
        targets = wb_repo.list_targets(int(row["id"]))
        out.append(conflict_svc.PeerPolicy(
            service=nc.SERVICE_WEB_BLOCK,
            id=int(row["id"]), name=str(row["name"]),
            slug=str(row["slug"]),
            router_id=int(row["router_id"]),
            enabled=bool(row["enabled"]),
            children=tuple(targets),
        ))

    # Walled-garden — pull entries + hotspot_profile.
    for row in wg_repo.list_policies_for_tenant(_tid()):
        entries = wg_repo.list_entries(int(row["id"]))
        out.append(conflict_svc.PeerPolicy(
            service=nc.SERVICE_WALLED_GARDEN,
            id=int(row["id"]), name=str(row["name"]),
            slug=str(row["slug"]),
            router_id=int(row["router_id"]),
            enabled=bool(row["enabled"]),
            hotspot_profile=str(row.get("hotspot_profile") or ""),
            children=tuple(entries),
        ))
    return out


def _finalize_preview(
    *, service: str, policy_id: int, router_id: int,
    plan: "renderer.ScriptPlan",
    policy: dict,
    targets,
    audit_action: str,
):
    """Common preview path: render forward + rollback, persist
    a script-version row, mark the deployment as `previewed`,
    audit, return the operator-facing payload.

    Phase A: the response now includes `impact_analysis`, a
    JSON projection of `ImpactAnalysis`. Even on render-unsafe
    paths we still build the analysis so the UI can show the
    operator *why* the plan was refused."""
    try:
        forward = renderer.render_forward_script(plan)
        rollback = renderer.render_rollback_script(plan)
        render_error = None
    except renderer.RenderSafetyError as e:
        render_error = str(e)
        forward = ""
        rollback = ""

    # Conflict detection runs against the current peer set
    # regardless of render outcome — operator should see
    # peer conflicts even when the render aborted.
    peers = [p for p in _peer_policies_for(
        current_service=service,
        current_router_id=router_id,
    ) if p.id != policy_id or p.service != service]
    conflict_analysis = conflict_svc.analyze(
        current_service=service,
        current_policy=policy,
        current_children=targets or (),
        peers=peers,
    )

    # Dependency hints — curated rule map, no DNS calls.
    dependency_analysis = dependency_svc.analyze(
        targets=targets or (), policy_type=service,
    )

    # Blast radius — NPC is one-policy-per-router today, so
    # affected_router_count defaults to 1. Future "policy
    # group" features will plug in here.
    blast_radius = blast_svc.analyze(
        policy_type=service, plan=plan,
        affected_router_count=1,
    )

    # Beginner explanation — pure glossary + plain-Arabic
    # prose. Surfaced as `beginner_explanation` so the UI can
    # render it next to the technical script viewer.
    beginner = beginner_svc.explain(
        policy_type=service, plan=plan, policy=policy,
    )

    # Canary planner — staged rollout recommendation.
    canary = canary_svc.plan(blast=blast_radius)

    def _build_recommendations(_impact):
        return rec_svc.build(
            impact=_impact,
            conflicts=conflict_analysis,
            dependencies=dependency_analysis,
            blast=blast_radius,
            canary=canary,
            policy_type=service,
            policy=policy,
        )

    # Health score helper — closes over all the analyses above.
    def _build_health(_impact):
        return health_svc.compute(
            impact=_impact,
            conflicts=conflict_analysis,
            dependencies=dependency_analysis,
            blast=blast_radius,
            rollback_available=_impact.rollback_available,
            canary_recommended=(
                canary.recommended_strategy
                in (canary_svc.STRATEGY_CANARY,
                    canary_svc.STRATEGY_STAGED)
            ),
        )

    if render_error is not None:
        impact = impact_svc.analyze(
            policy_type=service, policy=policy,
            plan=plan, targets=targets or (),
            rendered_forward="", rendered_rollback="",
            render_error=render_error,
        )
        _emit_audit(
            service=service, action=audit_action,
            policy_id=policy_id, router_id=router_id,
            error=render_error,
        )
        return fail(
            "render_unsafe", render_error,
            status=422,
            details={
                "impact_analysis":       impact.as_dict(),
                "conflict_analysis":     conflict_analysis.as_dict(),
                "dependency_analysis":   dependency_analysis.as_dict(),
                "blast_radius_analysis": blast_radius.as_dict(),
                "beginner_explanation":  beginner.as_dict(),
                "canary_plan":           canary.as_dict(),
                "health_score":          _build_health(impact).as_dict(),
                "smart_recommendations": _build_recommendations(impact).as_dict(),
            },
        )

    summary = renderer.script_summary(plan)
    impact = impact_svc.analyze(
        policy_type=service, policy=policy,
        plan=plan, targets=targets or (),
        rendered_forward=forward,
        rendered_rollback=rollback,
        render_error=None,
    )
    response_body: dict[str, Any] = {
        "service":   service,
        "policy_id": policy_id,
        "router_id": router_id,
        "can_apply": plan.can_apply,
        "summary":   summary,
        "forward_script":  forward,
        "rollback_script": rollback,
        "script_hash":     renderer.script_hash(forward),
        "impact_analysis":       impact.as_dict(),
        "conflict_analysis":     conflict_analysis.as_dict(),
        "dependency_analysis":   dependency_analysis.as_dict(),
        "blast_radius_analysis": blast_radius.as_dict(),
        "beginner_explanation":  beginner.as_dict(),
        "canary_plan":           canary.as_dict(),
        "health_score":          _build_health(impact).as_dict(),
        "smart_recommendations": _build_recommendations(impact).as_dict(),
    }

    if plan.can_apply and forward:
        # Persist the version + deployment state. We do this
        # only when the plan is applyable — preview-only failures
        # are still surfaced to the operator but don't pollute
        # the version history.
        try:
            sid = scripts_repo.record(
                service=service,
                policy_id=policy_id,
                script_body=forward,
                rollback_script_body=rollback,
                command_count=plan.total_commands,
            )
            response_body["script_version_id"] = sid
        except scripts_repo.SecretInScriptError as e:
            _emit_audit(
                service=service, action=audit_action,
                policy_id=policy_id, router_id=router_id,
                error=str(e),
            )
            return fail("render_unsafe", str(e), status=422)

        dep_repo.record_preview(
            tenant_id=_tid(),
            service=service,
            policy_id=policy_id,
            router_id=router_id,
            script_hash=response_body["script_hash"],
        )

    _emit_audit(
        service=service, action=audit_action,
        policy_id=policy_id, router_id=router_id,
        script_hash=response_body["script_hash"],
        command_count=plan.total_commands,
        can_apply=plan.can_apply,
    )
    return ok(response_body)


__all__ = ["register"]
