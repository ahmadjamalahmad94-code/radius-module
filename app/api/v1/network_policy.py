"""
Network Policy Center — JSON API.

Three sub-services share one URL tree:

  /v1/network-policy/remote-access/policies            …
  /v1/network-policy/web-block/policies                …
  /v1/network-policy/walled-garden/policies            …

Each supports CRUD + preview, and mirrors the operational web
surface with guarded apply, rollback, change history, script
download, and duplicate actions.

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
    npc_change_sets_repo as cs_repo,
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
    npc_apply_service as apply_svc,
    npc_remote_access_planner as ra_planner,
    npc_rollback_service as rollback_svc,
    npc_script_renderer as renderer,
    npc_snapshot_capture_service as snapshot_capture_svc,
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
    return _with_deployment(nc.SERVICE_REMOTE_ACCESS, row)


def _serialize_block(row: dict) -> dict:
    return _with_deployment(nc.SERVICE_WEB_BLOCK, row)


def _serialize_garden(row: dict) -> dict:
    return _with_deployment(nc.SERVICE_WALLED_GARDEN, row)


def _serialize_target(row: dict) -> dict:
    return dict(row)


def _with_deployment(service: str, row: dict) -> dict:
    out = dict(row)
    dep = dep_repo.get_for_policy(
        tenant_id=_tid(), service=service, policy_id=int(out["id"]),
    )
    out["deployment"] = dep or {}
    out["deployment_status"] = (dep or {}).get("status") or "draft"
    return out


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


def _slug_for_service(service: str) -> str:
    for slug, key in _URL_TO_SERVICE.items():
        if key == service:
            return slug
    return service.replace("_", "-")


def _policy_row(service: str, policy_id: int) -> Optional[dict]:
    if service == nc.SERVICE_REMOTE_ACCESS:
        return ra_repo.get_by_id(_tid(), policy_id)
    if service == nc.SERVICE_WEB_BLOCK:
        return wb_repo.get_policy(_tid(), policy_id)
    if service == nc.SERVICE_WALLED_GARDEN:
        return wg_repo.get_policy(_tid(), policy_id)
    return None


def _policy_children(service: str, policy_id: int) -> tuple[dict, ...]:
    if service == nc.SERVICE_WEB_BLOCK:
        return tuple(wb_repo.list_targets(policy_id))
    if service == nc.SERVICE_WALLED_GARDEN:
        return tuple(wg_repo.list_entries(policy_id))
    return ()


def _policy_plan(service: str, policy: dict, children: tuple[dict, ...]):
    if service == nc.SERVICE_REMOTE_ACCESS:
        return ra_planner.plan(policy)
    if service == nc.SERVICE_WEB_BLOCK:
        return wb_planner.plan(policy, children)
    if service == nc.SERVICE_WALLED_GARDEN:
        return wg_planner.plan(policy, children)
    raise ValueError(f"unknown network policy service: {service}")


def _serialize_policy(service: str, row: dict) -> dict:
    if service == nc.SERVICE_REMOTE_ACCESS:
        return _serialize_remote(row)
    if service == nc.SERVICE_WEB_BLOCK:
        return _serialize_block(row)
    return _serialize_garden(row)


def _create_audit_action(service: str) -> str:
    if service == nc.SERVICE_REMOTE_ACCESS:
        return ev.EVT_RA_POLICY_CREATED
    if service == nc.SERVICE_WEB_BLOCK:
        return ev.EVT_WB_POLICY_CREATED
    return ev.EVT_WG_POLICY_CREATED


def _render_policy(service: str, policy_id: int):
    policy = _policy_row(service, policy_id)
    if not policy:
        return None
    children = _policy_children(service, policy_id)
    plan = _policy_plan(service, policy, children)
    try:
        forward = renderer.render_forward_script(plan)
        rollback = renderer.render_rollback_script(plan)
        render_error = ""
    except renderer.RenderSafetyError as e:
        forward = ""
        rollback = ""
        render_error = str(e)
    return policy, children, plan, forward, rollback, render_error


def _policy_intelligence(
    *, service: str, policy: dict, children, plan,
    forward: str, rollback: str, render_error: str,
) -> dict[str, Any]:
    policy_id = int(policy["id"])
    peers = [
        p for p in _peer_policies_for(
            current_service=service,
            current_router_id=int(policy["router_id"]),
        )
        if p.id != policy_id or p.service != service
    ]
    conflicts = conflict_svc.analyze(
        current_service=service,
        current_policy=policy,
        current_children=children or (),
        peers=peers,
    )
    dependencies = dependency_svc.analyze(
        targets=children or (), policy_type=service,
    )
    blast = blast_svc.analyze(
        policy_type=service, plan=plan, affected_router_count=1,
    )
    beginner = beginner_svc.explain(
        policy_type=service, plan=plan, policy=policy,
    )
    canary = canary_svc.plan(blast=blast)
    impact = impact_svc.analyze(
        policy_type=service,
        policy=policy,
        plan=plan,
        targets=children or (),
        rendered_forward=forward,
        rendered_rollback=rollback,
        render_error=render_error or None,
    )
    health = health_svc.compute(
        impact=impact,
        conflicts=conflicts,
        dependencies=dependencies,
        blast=blast,
        rollback_available=impact.rollback_available,
        canary_recommended=(
            canary.recommended_strategy
            in (canary_svc.STRATEGY_CANARY, canary_svc.STRATEGY_STAGED)
        ),
    )
    return {
        "impact": impact,
        "conflicts": conflicts,
        "dependencies": dependencies,
        "blast": blast,
        "beginner": beginner,
        "canary": canary,
        "health": health,
        "recommendations": rec_svc.build(
            impact=impact,
            conflicts=conflicts,
            dependencies=dependencies,
            blast=blast,
            canary=canary,
            policy_type=service,
            policy=policy,
        ),
    }


def _change_set_payload(row: dict) -> dict:
    out = dict(row)
    out["targets"] = [dict(t) for t in cs_repo.list_targets(int(row["id"]))]
    out["rollback_eligible"] = (
        row.get("action_type") == cs_repo.ACTION_APPLY
        and row.get("status") in (
            cs_repo.STATUS_SUCCEEDED,
            cs_repo.STATUS_PARTIALLY_SUCCEEDED,
        )
    )
    return out


def _slug_exists(service: str, slug: str) -> bool:
    if service == nc.SERVICE_REMOTE_ACCESS:
        return ra_repo.get_by_slug(_tid(), slug) is not None
    if service == nc.SERVICE_WEB_BLOCK:
        return wb_repo.get_policy_by_slug(_tid(), slug) is not None
    return wg_repo.get_policy_by_slug(_tid(), slug) is not None


def _unique_copy_slug(service: str, original_slug: str) -> str:
    base = f"{original_slug}-copy"
    candidate = base
    index = 2
    while _slug_exists(service, candidate):
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _duplicate_policy(service: str, policy: dict) -> dict:
    copy_slug = _unique_copy_slug(service, str(policy["slug"]))
    if service == nc.SERVICE_REMOTE_ACCESS:
        new_id = ra_repo.create(
            tenant_id=_tid(),
            router_id=int(policy["router_id"]),
            name=f"{policy['name']} (نسخة)",
            slug=copy_slug,
            allow_winbox=bool(policy.get("allow_winbox")),
            allow_ssh=bool(policy.get("allow_ssh")),
            allow_api=bool(policy.get("allow_api")),
            allow_api_ssl=bool(policy.get("allow_api_ssl")),
            allow_webfig_http=bool(policy.get("allow_webfig_http")),
            allow_webfig_https=bool(policy.get("allow_webfig_https")),
            source_address_list=str(policy.get("source_address_list") or ""),
            expires_at=str(policy.get("expires_at") or ""),
            reason=str(policy.get("reason") or ""),
            enabled=bool(policy.get("enabled")),
        )
    elif service == nc.SERVICE_WEB_BLOCK:
        new_id = wb_repo.create_policy(
            tenant_id=_tid(),
            router_id=int(policy["router_id"]),
            name=f"{policy['name']} (نسخة)",
            slug=copy_slug,
            scope=str(policy.get("scope") or wb_repo.SCOPE_ALL_USERS),
            schedule_id=str(policy.get("schedule_id") or ""),
            fail_open=bool(policy.get("fail_open")),
            enabled=bool(policy.get("enabled")),
        )
    else:
        new_id = wg_repo.create_policy(
            tenant_id=_tid(),
            router_id=int(policy["router_id"]),
            name=f"{policy['name']} (نسخة)",
            slug=copy_slug,
            hotspot_profile=str(policy.get("hotspot_profile") or ""),
            enabled=bool(policy.get("enabled")),
        )
    created = _policy_row(service, new_id) or {}
    _emit_audit(
        service=service,
        action=_create_audit_action(service),
        policy_id=int(new_id),
        router_id=int(policy["router_id"]),
        duplicated_from=int(policy["id"]),
    )
    return created


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
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>/preview.rsc",
        "npc_ra_script",
        require_api_token(ra_script), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>/apply",
        "npc_ra_apply",
        require_api_token(ra_apply), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>/changes",
        "npc_ra_changes",
        require_api_token(ra_changes), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>/changes/<int:change_set_id>/rollback",
        "npc_ra_rollback",
        require_api_token(ra_rollback), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/remote-access/policies/<int:policy_id>/duplicate",
        "npc_ra_duplicate",
        require_api_token(ra_duplicate), methods=["POST"])

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
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/preview.rsc",
        "npc_wb_script",
        require_api_token(wb_script), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/apply",
        "npc_wb_apply",
        require_api_token(wb_apply), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/changes",
        "npc_wb_changes",
        require_api_token(wb_changes), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/changes/<int:change_set_id>/rollback",
        "npc_wb_rollback",
        require_api_token(wb_rollback), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/web-block/policies/<int:policy_id>/duplicate",
        "npc_wb_duplicate",
        require_api_token(wb_duplicate), methods=["POST"])

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
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/preview.rsc",
        "npc_wg_script",
        require_api_token(wg_script), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/apply",
        "npc_wg_apply",
        require_api_token(wg_apply), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/changes",
        "npc_wg_changes",
        require_api_token(wg_changes), methods=["GET"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/changes/<int:change_set_id>/rollback",
        "npc_wg_rollback",
        require_api_token(wg_rollback), methods=["POST"])
    bp.add_url_rule(
        "/network-policy/walled-garden/policies/<int:policy_id>/duplicate",
        "npc_wg_duplicate",
        require_api_token(wg_duplicate), methods=["POST"])


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


def ra_script(policy_id: int):
    return _api_script(nc.SERVICE_REMOTE_ACCESS, policy_id)


def wb_script(policy_id: int):
    return _api_script(nc.SERVICE_WEB_BLOCK, policy_id)


def wg_script(policy_id: int):
    return _api_script(nc.SERVICE_WALLED_GARDEN, policy_id)


def ra_apply(policy_id: int):
    return _api_apply(nc.SERVICE_REMOTE_ACCESS, policy_id)


def wb_apply(policy_id: int):
    return _api_apply(nc.SERVICE_WEB_BLOCK, policy_id)


def wg_apply(policy_id: int):
    return _api_apply(nc.SERVICE_WALLED_GARDEN, policy_id)


def ra_changes(policy_id: int):
    return _api_changes(nc.SERVICE_REMOTE_ACCESS, policy_id)


def wb_changes(policy_id: int):
    return _api_changes(nc.SERVICE_WEB_BLOCK, policy_id)


def wg_changes(policy_id: int):
    return _api_changes(nc.SERVICE_WALLED_GARDEN, policy_id)


def ra_rollback(policy_id: int, change_set_id: int):
    return _api_rollback(nc.SERVICE_REMOTE_ACCESS, policy_id, change_set_id)


def wb_rollback(policy_id: int, change_set_id: int):
    return _api_rollback(nc.SERVICE_WEB_BLOCK, policy_id, change_set_id)


def wg_rollback(policy_id: int, change_set_id: int):
    return _api_rollback(nc.SERVICE_WALLED_GARDEN, policy_id, change_set_id)


def ra_duplicate(policy_id: int):
    return _api_duplicate(nc.SERVICE_REMOTE_ACCESS, policy_id)


def wb_duplicate(policy_id: int):
    return _api_duplicate(nc.SERVICE_WEB_BLOCK, policy_id)


def wg_duplicate(policy_id: int):
    return _api_duplicate(nc.SERVICE_WALLED_GARDEN, policy_id)


def _api_script(service: str, policy_id: int):
    rendered = _render_policy(service, policy_id)
    if not rendered:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    policy, _children, _plan, forward, _rollback, render_error = rendered
    if render_error:
        return fail("render_unsafe", render_error, status=422)
    if not forward:
        return fail(
            "empty_script",
            "لا يوجد سكربت قابل للتنزيل. راجع المعاينة أولًا.",
            status=422,
        )
    slug = _slug_for_service(service)
    filename = f"npc-{slug}-{policy['slug']}-preview.rsc"
    return ok({
        "filename": filename,
        "script": forward,
        "script_hash": renderer.script_hash(forward),
        "service": service,
        "policy_id": int(policy_id),
        "router_id": int(policy["router_id"]),
    })


def _api_changes(service: str, policy_id: int):
    policy = _policy_row(service, policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    rows = cs_repo.list_for_policy(
        _tid(), service=service, policy_id=int(policy_id),
    )
    return ok({
        "items": [_change_set_payload(r) for r in rows],
        "count": len(rows),
        "service": service,
        "policy_id": int(policy_id),
    })


def _api_apply(service: str, policy_id: int):
    rendered = _render_policy(service, policy_id)
    if not rendered:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    policy, children, plan, forward, rollback, render_error = rendered
    intelligence = _policy_intelligence(
        service=service,
        policy=policy,
        children=children,
        plan=plan,
        forward=forward,
        rollback=rollback,
        render_error=render_error,
    )
    script_hash = renderer.script_hash(forward) if forward else ""
    snapshot_id: Optional[int] = None
    try:
        cap = snapshot_capture_svc.capture_pre_apply_snapshot(
            tenant_id=_tid(),
            router_id=int(policy["router_id"]),
            policy_id=int(policy_id),
            policy_type=service,
            created_by=_actor(),
        )
        snapshot_id = int(cap.snapshot_id)
    except Exception:  # noqa: BLE001
        snapshot_id = None

    body = _body()
    confirmations_raw = body.get("confirmations") or ()
    if isinstance(confirmations_raw, str):
        confirmations = (confirmations_raw,)
    else:
        confirmations = tuple(str(x) for x in confirmations_raw)
    execution_mode = str(body.get("execution_mode") or cs_repo.MODE_FULL)
    if execution_mode not in cs_repo.ALLOWED_MODES:
        return fail(
            "validation_error",
            "وضع التنفيذ غير صالح.",
            status=422,
        )
    result = apply_svc.request_apply(
        tenant_id=_tid(),
        service=service,
        policy=policy,
        policy_children=children,
        forward_script=forward,
        rollback_script=rollback,
        render_error=render_error,
        preview_hash=script_hash,
        snapshot_id=snapshot_id,
        target_router_ids=(int(policy["router_id"]),),
        actor=_actor(),
        actor_has_apply_perm=True,
        confirmations=confirmations,
        execution_mode=execution_mode,
        canary_opt_in=_bool(body.get("canary_opt_in")),
        all_routers_targeted=False,
        offline_router_ids=(),
        impact=intelligence["impact"],
        conflicts=intelligence["conflicts"],
        dependencies=intelligence["dependencies"],
        blast=intelligence["blast"],
        health=intelligence["health"],
        canary=intelligence["canary"],
    )
    if result.ok and service == nc.SERVICE_REMOTE_ACCESS:
        try:
            from ...radius.services import npc_remote_tunnel as tunnel_svc
            tunnel_svc.ensure_tunnels_for_policy(
                tenant_id=_tid(), policy=policy,
            )
            tunnel_svc.regenerate_and_reload()
        except Exception:  # noqa: BLE001
            pass
    return ok({
        **result.as_dict(),
        "service": service,
        "policy_id": int(policy_id),
        "router_id": int(policy["router_id"]),
        "snapshot_id": snapshot_id,
        "script_hash": script_hash,
    })


def _api_rollback(service: str, policy_id: int, change_set_id: int):
    policy = _policy_row(service, policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    result = rollback_svc.request_rollback(
        tenant_id=_tid(),
        service=service,
        policy_id=int(policy_id),
        change_set_id=int(change_set_id),
        actor=_actor(),
        actor_has_apply_perm=True,
    )
    return ok({
        **result.as_dict(),
        "service": service,
        "policy_id": int(policy_id),
        "original_change_set_id": int(change_set_id),
    })


def _api_duplicate(service: str, policy_id: int):
    policy = _policy_row(service, policy_id)
    if not policy:
        return fail("not_found",
                    f"policy {policy_id} غير موجود", status=404)
    try:
        created = _duplicate_policy(service, policy)
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    return ok(_serialize_policy(service, created), status=201)


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
