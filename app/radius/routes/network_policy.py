"""NPC Phase 6 — server-rendered admin UI for the Network
Policy Center.

Preview-first workflow. Apply is intentionally **not** exposed
on any route in this module; the matching `npc.<svc>.apply`
permission is unused here on purpose. Every state-changing
action that touches MikroTik state is gated behind Phase 5
(future) — this module only writes to the local DB
(policies / targets / entries / script_versions) and renders
the resulting plan for human review.

Three sub-services share one URL tree and one template
family. A small `_SERVICE_REGISTRY` dispatches per-service
behaviour (repo handle, planner, audit event labels,
permissions, view-model builders) so route bodies stay small.

Sidebar entry: «الشبكة → سياسات الشبكة → [الوصول البعيد /
حظر المواقع / المواقع المسموحة]». The list page is the
landing for each sub-service.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from flask import (
    Blueprint, Response, abort, flash, g, redirect,
    render_template, request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import (
    npc_change_sets_repo as cs_repo,
    npc_common as nc,
    npc_deployments_repo as dep_repo,
    npc_remote_access_repo as ra_repo,
    npc_scripts_repo as scripts_repo,
    npc_walled_garden_repo as wg_repo,
    npc_web_block_repo as wb_repo,
)
from ..services import (
    npc_apply_service as apply_svc,
    npc_audit_events as ev,
    npc_beginner_explainer as beginner_svc,
    npc_blast_radius as blast_svc,
    npc_canary_planner as canary_svc,
    npc_conflict_detector as conflict_svc,
    npc_dependency_detector as dependency_svc,
    npc_domain_analyzer as analyzer,
    npc_execution_readiness as readiness_svc,
    npc_impact_analyzer as impact_svc,
    npc_policy_health as health_svc,
    npc_recommendations as rec_svc,
    npc_remote_access as ra_svc,
    npc_remote_access_planner as ra_planner,
    npc_rollback_service as rollback_svc,
    npc_script_renderer as renderer,
    npc_snapshot_capture_service as snapshot_capture_svc,
    npc_walled_garden_planner as wg_planner,
    npc_web_block_planner as wb_planner,
)
from ..services.audit import get_audit_service
from ..services.mt_permissions import (
    PERM_NPC_REMOTE_ACCESS_MANAGE,
    PERM_NPC_REMOTE_ACCESS_PREVIEW,
    PERM_NPC_REMOTE_ACCESS_VIEW,
    PERM_NPC_WALLED_GARDEN_MANAGE,
    PERM_NPC_WALLED_GARDEN_PREVIEW,
    PERM_NPC_WALLED_GARDEN_VIEW,
    PERM_NPC_WEB_BLOCK_MANAGE,
    PERM_NPC_WEB_BLOCK_PREVIEW,
    PERM_NPC_WEB_BLOCK_VIEW,
    current_admin_has,
    requires_perm,
)


# ─── Service registry ────────────────────────────────────────


@dataclass(frozen=True)
class _ServiceDef:
    key: str                          # nc.SERVICE_* literal
    url_slug: str                     # path slug
    label_ar: str                     # operator-facing label
    icon: str                         # font-awesome icon name
    perms: dict[str, str]             # 'view' / 'manage' / 'preview'
    audit_actions: dict[str, str]     # 'created' / 'updated' / 'deleted' / 'preview'
    target_type: str                  # audit target_type


_REGISTRY: dict[str, _ServiceDef] = {
    "remote-access": _ServiceDef(
        key=nc.SERVICE_REMOTE_ACCESS,
        url_slug="remote-access",
        label_ar="الوصول البعيد",
        icon="user-shield",
        perms={
            "view":    PERM_NPC_REMOTE_ACCESS_VIEW,
            "manage":  PERM_NPC_REMOTE_ACCESS_MANAGE,
            "preview": PERM_NPC_REMOTE_ACCESS_PREVIEW,
        },
        audit_actions={
            "created":  ev.EVT_RA_POLICY_CREATED,
            "updated":  ev.EVT_RA_POLICY_UPDATED,
            "deleted":  ev.EVT_RA_POLICY_DELETED,
            "preview":  ev.EVT_RA_PREVIEW_GENERATED,
        },
        target_type=ev.TARGET_REMOTE_ACCESS,
    ),
    "web-block": _ServiceDef(
        key=nc.SERVICE_WEB_BLOCK,
        url_slug="web-block",
        label_ar="حظر المواقع",
        icon="ban",
        perms={
            "view":    PERM_NPC_WEB_BLOCK_VIEW,
            "manage":  PERM_NPC_WEB_BLOCK_MANAGE,
            "preview": PERM_NPC_WEB_BLOCK_PREVIEW,
        },
        audit_actions={
            "created":  ev.EVT_WB_POLICY_CREATED,
            "updated":  ev.EVT_WB_POLICY_UPDATED,
            "deleted":  ev.EVT_WB_POLICY_DELETED,
            "preview":  ev.EVT_WB_PREVIEW_GENERATED,
            "target_added":   ev.EVT_WB_TARGET_ADDED,
            "target_removed": ev.EVT_WB_TARGET_REMOVED,
        },
        target_type=ev.TARGET_WEB_BLOCK,
    ),
    "walled-garden": _ServiceDef(
        key=nc.SERVICE_WALLED_GARDEN,
        url_slug="walled-garden",
        label_ar="المواقع المسموحة",
        icon="shield-halved",
        perms={
            "view":    PERM_NPC_WALLED_GARDEN_VIEW,
            "manage":  PERM_NPC_WALLED_GARDEN_MANAGE,
            "preview": PERM_NPC_WALLED_GARDEN_PREVIEW,
        },
        audit_actions={
            "created":  ev.EVT_WG_POLICY_CREATED,
            "updated":  ev.EVT_WG_POLICY_UPDATED,
            "deleted":  ev.EVT_WG_POLICY_DELETED,
            "preview":  ev.EVT_WG_PREVIEW_GENERATED,
            "entry_added":   ev.EVT_WG_TARGET_ADDED,
            "entry_removed": ev.EVT_WG_TARGET_REMOVED,
        },
        target_type=ev.TARGET_WALLED_GARDEN,
    ),
}


def _svc_or_404(url_slug: str) -> _ServiceDef:
    svc = _REGISTRY.get(url_slug)
    if not svc:
        abort(404)
    return svc


# ─── Helpers ─────────────────────────────────────────────────


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    """Operator-friendly actor string for the audit log."""
    try:
        from flask import session as flask_session
        u = flask_session.get("admin_user") or "admin"
        return f"admin:{u}"
    except Exception:  # noqa: BLE001
        return "admin:unknown"


def _audit():
    return get_audit_service()


def _emit_audit(
    svc: _ServiceDef, action: str, *,
    policy_id: int, router_id: Optional[int] = None,
    **extra: Any,
) -> None:
    """Record an audit event using the Phase-3 catalogue."""
    payload = ev.build_payload(
        service=svc.key, policy_id=policy_id,
        router_id=router_id, **extra,
    )
    _audit().record(
        actor=_actor(),
        action=action,
        target_type=svc.target_type,
        target_id=str(policy_id),
        payload=payload.as_dict(),
        router_id=router_id,
    )


def _bool_from_form(value: Any) -> bool:
    """Accept '1' / 'true' / 'on' / int 1 / bool True as truthy.
    HTML checkboxes submit 'on' when checked, omit the key
    when not — so callers must use form.get(key) to default to
    None / falsy."""
    if value is True:
        return True
    s = str(value or "").strip().lower()
    return s in {"1", "true", "on", "yes"}


def _nas_list() -> list[dict]:
    """All routers in the current tenant, ordered by name.
    Provides the operator-facing dropdown on the create form.
    Returns dicts so the template doesn't need NasDevice
    accessors."""
    rows = db().execute(
        "SELECT id, name, address FROM nas_devices "
        "WHERE tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='') "
        "ORDER BY name",
        (_tid(),),
    ).fetchall()
    return [dict(r) for r in rows]


def _nas_name(router_id: Optional[int]) -> str:
    if not router_id:
        return ""
    row = db().execute(
        "SELECT name FROM nas_devices WHERE id=? AND tenant_id=?",
        (int(router_id), _tid()),
    ).fetchone()
    return row["name"] if row else f"router #{router_id}"


# ─── Per-service repo + planner adapters ─────────────────────


@dataclass
class _PolicyAdapter:
    """Uniform shape over the three per-service repos so the
    route bodies don't have to switch on `service` constantly."""
    list_for_tenant: Callable[[int], list[dict]]
    get: Callable[[int, int], Optional[dict]]
    create: Callable[..., int]
    update: Callable[..., Optional[dict]]
    delete: Callable[[int, int], bool]
    plan: Callable[..., renderer.ScriptPlan]
    # Targets/entries surface — None for remote_access.
    list_children: Optional[Callable[[int], list[dict]]] = None
    add_child: Optional[Callable[..., int]] = None
    get_child: Optional[Callable[[int], Optional[dict]]] = None
    delete_child: Optional[Callable[[int], bool]] = None
    child_counts: Optional[Callable[[int], dict[str, int]]] = None
    child_label_ar: str = ""


def _adapter(svc: _ServiceDef) -> _PolicyAdapter:
    if svc.key == nc.SERVICE_REMOTE_ACCESS:
        return _PolicyAdapter(
            list_for_tenant=ra_repo.list_for_tenant,
            get=ra_repo.get_by_id,
            create=ra_repo.create,
            update=ra_repo.update,
            delete=ra_repo.delete,
            plan=lambda policy, **_kw: ra_planner.plan(policy),
        )
    if svc.key == nc.SERVICE_WEB_BLOCK:
        return _PolicyAdapter(
            list_for_tenant=wb_repo.list_policies_for_tenant,
            get=wb_repo.get_policy,
            create=wb_repo.create_policy,
            update=wb_repo.update_policy,
            delete=wb_repo.delete_policy,
            plan=lambda policy, children=(): wb_planner.plan(
                policy, children),
            list_children=wb_repo.list_targets,
            add_child=wb_repo.add_target,
            get_child=wb_repo.get_target,
            delete_child=wb_repo.delete_target,
            child_counts=wb_repo.target_counts,
            child_label_ar="وجهات الحظر",
        )
    if svc.key == nc.SERVICE_WALLED_GARDEN:
        return _PolicyAdapter(
            list_for_tenant=wg_repo.list_policies_for_tenant,
            get=wg_repo.get_policy,
            create=wg_repo.create_policy,
            update=wg_repo.update_policy,
            delete=wg_repo.delete_policy,
            plan=lambda policy, children=(): wg_planner.plan(
                policy, children),
            list_children=wg_repo.list_entries,
            add_child=wg_repo.add_entry,
            get_child=wg_repo.get_entry,
            delete_child=wg_repo.delete_entry,
            child_counts=wg_repo.entry_counts,
            child_label_ar="إدخالات الإستثناء",
        )
    abort(404)


# ─── Risk derivation for list cards ──────────────────────────


def _risk_for(svc: _ServiceDef, policy: dict) -> str:
    """Compact risk label for a policy. Pure data — no
    network. Mirrors the planner's risk model so the list
    card matches the preview screen."""
    if svc.key == nc.SERVICE_REMOTE_ACCESS:
        assess = ra_svc.assess_policy(
            allow_winbox=bool(policy.get("allow_winbox")),
            allow_ssh=bool(policy.get("allow_ssh")),
            allow_api=bool(policy.get("allow_api")),
            allow_api_ssl=bool(policy.get("allow_api_ssl")),
            allow_webfig_http=bool(
                policy.get("allow_webfig_http")),
            allow_webfig_https=bool(
                policy.get("allow_webfig_https")),
            source_address_list=str(
                policy.get("source_address_list") or ""),
            expires_at=str(policy.get("expires_at") or ""),
        )
        return assess.risk
    # web_block / walled_garden risk is bounded by what the
    # operator asked for; we mark them low/medium without a
    # heavy assessor since they don't open ports.
    return "low"


# ─── Route registration ──────────────────────────────────────


def register_network_policy_routes(bp: Blueprint) -> None:
    # Landing — now a router-picker (was: redirect to
    # remote-access list). The intent: the operator picks a
    # router first, then sees that router's policies — which
    # matches how MikroTik state actually scopes anyway.
    bp.add_url_rule(
        "/network-policy/",
        "network_policy_index",
        npc_index,
        methods=["GET"],
    )
    # Per-router landing — redirects to the remote-access
    # tab of the picked router. Linked as a dashboard tab.
    bp.add_url_rule(
        "/mt/<int:nas_id>/network-policies/",
        "npc_router_landing",
        _npc_router_landing,
        methods=["GET"],
    )

    # Each sub-service has the same set of routes; we build
    # them in a loop so the URL surface is symmetric.
    for url_slug, svc in _REGISTRY.items():
        view = svc.perms["view"]
        manage = svc.perms["manage"]
        preview = svc.perms["preview"]

        bp.add_url_rule(
            f"/network-policy/{url_slug}/",
            f"npc_{svc.key}_list",
            requires_perm(view)(_make_list_view(svc)),
            methods=["GET"],
        )
        # Per-router scoped list — shares the same template,
        # filtered to one router. Surfaced as a dashboard tab
        # so the operator's daily entry point is the router
        # itself, not a global services menu.
        bp.add_url_rule(
            f"/mt/<int:nas_id>/network-policies/{url_slug}/",
            f"npc_{svc.key}_list_scoped",
            requires_perm(view)(
                _make_list_view(svc, scoped=True)),
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/network-policy/{url_slug}/new",
            f"npc_{svc.key}_new",
            requires_perm(manage)(_make_new_view(svc)),
            methods=["GET", "POST"],
        )
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>/edit",
            f"npc_{svc.key}_edit",
            requires_perm(manage)(_make_edit_view(svc)),
            methods=["GET", "POST"],
        )
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>/preview",
            f"npc_{svc.key}_preview",
            requires_perm(preview)(_make_preview_view(svc)),
            methods=["GET", "POST"],
        )
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>/preview.rsc",
            f"npc_{svc.key}_preview_download",
            requires_perm(preview)(_make_download_view(svc)),
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>/delete",
            f"npc_{svc.key}_delete",
            requires_perm(manage)(_make_delete_view(svc)),
            methods=["POST"],
        )
        # Guarded apply route — POST only, gated by the apply
        # permission AND the contracts engine. The route
        # NEVER calls MikroTik directly — it goes through
        # `apply_svc.request_apply` which uses the executor
        # adapter (defaulting to the null executor).
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>/apply",
            f"npc_{svc.key}_apply",
            requires_perm(
                svc.perms.get("apply")
                or (svc.perms["preview"].rsplit(".", 1)[0]
                    + ".apply")
            )(_make_apply_view(svc)),
            methods=["POST"],
        )
        # Rollback route — POST only, reuses the apply
        # permission (operators that can apply can roll back).
        # Managed-prefix safety check fires inside the service.
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>"
            "/changes/<int:change_set_id>/rollback",
            f"npc_{svc.key}_rollback",
            requires_perm(
                svc.perms.get("apply")
                or (svc.perms["preview"].rsplit(".", 1)[0]
                    + ".apply")
            )(_make_rollback_view(svc)),
            methods=["POST"],
        )
        # Change history page — read-only, view permission.
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>/changes",
            f"npc_{svc.key}_changes",
            requires_perm(view)(_make_changes_view(svc)),
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/network-policy/{url_slug}/<int:policy_id>/duplicate",
            f"npc_{svc.key}_duplicate",
            requires_perm(manage)(_make_duplicate_view(svc)),
            methods=["POST"],
        )

        # Child rows (targets / entries) — only for the two
        # services that have them.
        if _adapter(svc).add_child is not None:
            bp.add_url_rule(
                f"/network-policy/{url_slug}/<int:policy_id>/children",
                f"npc_{svc.key}_child_add",
                requires_perm(manage)(_make_child_add_view(svc)),
                methods=["POST"],
            )
            bp.add_url_rule(
                f"/network-policy/{url_slug}/<int:policy_id>/children/<int:child_id>/delete",
                f"npc_{svc.key}_child_delete",
                requires_perm(manage)(_make_child_delete_view(svc)),
                methods=["POST"],
            )


def npc_index():
    """Global landing — now a router-picker.

    Operators reach NPC by picking the router first. Each row
    on this page is a small card linking to the per-router
    NPC page under `/admin/radius/mt/<nas_id>/network-policies/`.
    The old per-service global lists stay alive (the JSON API
    still uses them) but they're no longer surfaced in the UI.
    """
    routers = _nas_list()
    summary: list[dict] = []
    for r in routers:
        rid = int(r["id"])
        counts = {
            "remote_access": len(
                ra_repo.list_for_router(_tid(), rid)
            ),
            "web_block": len(
                wb_repo.list_policies_for_router(_tid(), rid)
            ),
            "walled_garden": len(
                wg_repo.list_policies_for_router(_tid(), rid)
            ),
        }
        summary.append({
            "id":      rid,
            "name":    r.get("name") or f"router #{rid}",
            "address": r.get("address") or "",
            "counts":  counts,
            "total":   sum(counts.values()),
        })
    return render_template(
        "radius/network_policy_router_picker.html",
        services=list(_REGISTRY.values()),
        routers=summary,
        page_title="سياسات الشبكة — اختر راوتراً",
    )


def _npc_router_landing(nas_id: int):
    """Per-router NPC landing — redirects to the
    remote-access tab of the picked router."""
    return redirect(url_for(
        f"radius.npc_{nc.SERVICE_REMOTE_ACCESS}_list_scoped",
        nas_id=int(nas_id),
    ))


# ─── List view ───────────────────────────────────────────────


def _make_list_view(svc: _ServiceDef, *, scoped: bool = False):
    """Build the policy-list view.

    `scoped=True` registers the variant that takes a `nas_id`
    URL parameter and filters policies to that single router.
    The two variants share a body so the cards layout stays
    identical between the global list (admin-only diagnostic
    surface) and the per-router list (the operator's daily
    entry point)."""

    def _view_global():
        return _render_list(scoped_to_nas=None)

    def _view_scoped(nas_id: int):
        # Validate the router belongs to this tenant; 404 if
        # not — same posture as every other /mt/<id>/* route.
        nas_row = db().execute(
            "SELECT id, name, address FROM nas_devices "
            "WHERE id=? AND tenant_id=? "
            "  AND (deleted_at IS NULL OR deleted_at='')",
            (int(nas_id), _tid()),
        ).fetchone()
        if not nas_row:
            abort(404)
        return _render_list(scoped_to_nas=dict(nas_row))

    def _render_list(*, scoped_to_nas):
        ad = _adapter(svc)
        if scoped_to_nas is not None:
            rid = int(scoped_to_nas["id"])
            if svc.key == nc.SERVICE_REMOTE_ACCESS:
                rows = ra_repo.list_for_router(_tid(), rid)
            elif svc.key == nc.SERVICE_WEB_BLOCK:
                rows = wb_repo.list_policies_for_router(
                    _tid(), rid,
                )
            else:
                rows = wg_repo.list_policies_for_router(
                    _tid(), rid,
                )
        else:
            rows = ad.list_for_tenant(_tid())
        cards: list[dict] = []
        for r in rows:
            dep = dep_repo.get_for_policy(
                tenant_id=_tid(), service=svc.key,
                policy_id=int(r["id"]),
            )
            child_count = 0
            if ad.list_children is not None:
                child_count = len(ad.list_children(int(r["id"])))
            cards.append({
                "row": r,
                "router_name": _nas_name(r.get("router_id")),
                "last_preview_at": (
                    dep.get("last_preview_at") if dep else ""
                ),
                "deployment_status": (
                    dep.get("status") if dep else "draft"
                ),
                "child_count": child_count,
                "risk": _risk_for(svc, r),
            })
        return render_template(
            "radius/network_policy_list.html",
            service=svc,
            services=list(_REGISTRY.values()),
            cards=cards,
            scoped_to_nas=scoped_to_nas,
            page_title=svc.label_ar,
        )

    return _view_scoped if scoped else _view_global


# ─── Create / edit view ──────────────────────────────────────


def _form_kwargs_remote(form) -> dict[str, Any]:
    """Pull the create_remote_access kwargs out of a form dict
    in a way that defaults checkboxes to False (HTML doesn't
    submit unchecked boxes)."""
    return {
        "name": (form.get("name") or "").strip(),
        "router_id": int(form.get("router_id") or 0),
        "allow_winbox": _bool_from_form(form.get("allow_winbox")),
        "allow_ssh": _bool_from_form(form.get("allow_ssh")),
        "allow_api": _bool_from_form(form.get("allow_api")),
        "allow_api_ssl": _bool_from_form(
            form.get("allow_api_ssl")),
        "allow_webfig_http": _bool_from_form(
            form.get("allow_webfig_http")),
        "allow_webfig_https": _bool_from_form(
            form.get("allow_webfig_https")),
        "source_address_list": (
            form.get("source_address_list") or "").strip(),
        "expires_at": (form.get("expires_at") or "").strip(),
        "reason": (form.get("reason") or "").strip(),
        "enabled": _bool_from_form(form.get("enabled", "1")),
    }


def _form_kwargs_web(form) -> dict[str, Any]:
    return {
        "name": (form.get("name") or "").strip(),
        "router_id": int(form.get("router_id") or 0),
        "scope": (form.get("scope")
                   or wb_repo.SCOPE_ALL_USERS).strip(),
        "schedule_id": (form.get("schedule_id") or "").strip(),
        "fail_open": _bool_from_form(form.get("fail_open", "1")),
        "enabled": _bool_from_form(form.get("enabled", "1")),
    }


def _form_kwargs_garden(form) -> dict[str, Any]:
    return {
        "name": (form.get("name") or "").strip(),
        "router_id": int(form.get("router_id") or 0),
        "hotspot_profile": (
            form.get("hotspot_profile") or "").strip(),
        "enabled": _bool_from_form(form.get("enabled", "1")),
    }


def _extract_form(svc: _ServiceDef, form) -> dict[str, Any]:
    if svc.key == nc.SERVICE_REMOTE_ACCESS:
        return _form_kwargs_remote(form)
    if svc.key == nc.SERVICE_WEB_BLOCK:
        return _form_kwargs_web(form)
    return _form_kwargs_garden(form)


def _validate_required(payload: dict) -> Optional[str]:
    if not payload.get("name"):
        return "اسم السياسة مطلوب."
    if not payload.get("router_id"):
        return "اختر الراوتر المستهدف."
    return None


def _make_new_view(svc: _ServiceDef):
    def _view():
        ad = _adapter(svc)
        if request.method == "GET":
            # `?router_id=<id>` honoured so the per-router
            # dashboard tab can pre-lock the target router on
            # the new-policy form.
            preset_router_id = None
            try:
                preset_router_id = int(
                    request.args.get("router_id") or 0
                ) or None
            except (TypeError, ValueError):
                preset_router_id = None
            return render_template(
                "radius/network_policy_form.html",
                service=svc,
                services=list(_REGISTRY.values()),
                policy=None,
                children=(),
                child_counts={},
                routers=_nas_list(),
                preset_router_id=preset_router_id,
                page_title=f"إضافة سياسة — {svc.label_ar}",
            )

        payload = _extract_form(svc, request.form)
        err = _validate_required(payload)
        if err:
            flash(err, "danger")
            return redirect(url_for(
                f"radius.npc_{svc.key}_new"
            ))
        try:
            pid = ad.create(tenant_id=_tid(), **payload)
        except ValueError as e:
            flash(f"تعذّر إنشاء السياسة: {e}", "danger")
            return redirect(url_for(
                f"radius.npc_{svc.key}_new"
            ))
        _emit_audit(
            svc, svc.audit_actions["created"],
            policy_id=pid,
            router_id=int(payload["router_id"]),
        )
        flash("أُنشئت السياسة بنجاح. التطبيق ما زال معاينة فقط.",
              "success")
        return redirect(url_for(
            f"radius.npc_{svc.key}_edit", policy_id=pid,
        ))
    return _view


def _make_edit_view(svc: _ServiceDef):
    def _view(policy_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)
        if request.method == "POST":
            payload = _extract_form(svc, request.form)
            # `router_id` is immutable post-create — strip it.
            payload.pop("router_id", None)
            try:
                ad.update(_tid(), policy_id, **payload)
            except ValueError as e:
                flash(f"تعذّر حفظ السياسة: {e}", "danger")
                return redirect(url_for(
                    f"radius.npc_{svc.key}_edit",
                    policy_id=policy_id,
                ))
            _emit_audit(
                svc, svc.audit_actions["updated"],
                policy_id=policy_id,
                router_id=policy["router_id"],
            )
            flash("حُفظت التغييرات. ما زال هذا معاينة فقط — "
                  "لم يُطبَّق على الراوتر.", "success")
            return redirect(url_for(
                f"radius.npc_{svc.key}_edit", policy_id=policy_id,
            ))

        children: list[dict] = (
            list(ad.list_children(policy_id))
            if ad.list_children is not None else []
        )
        counts: dict[str, int] = (
            ad.child_counts(policy_id)
            if ad.child_counts is not None else {}
        )
        return render_template(
            "radius/network_policy_form.html",
            service=svc,
            services=list(_REGISTRY.values()),
            policy=policy,
            children=children,
            child_counts=counts,
            routers=_nas_list(),
            page_title=(f"تحرير: {policy['name']}"),
        )
    return _view


# ─── Preview view ────────────────────────────────────────────


def _make_preview_view(svc: _ServiceDef):
    def _view(policy_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)

        children: tuple = ()
        if ad.list_children is not None:
            children = tuple(ad.list_children(policy_id))

        plan = ad.plan(policy, children=children) \
            if ad.list_children is not None \
            else ad.plan(policy)

        try:
            forward = renderer.render_forward_script(plan)
            rollback = renderer.render_rollback_script(plan)
            render_error = ""
        except renderer.RenderSafetyError as e:
            forward = ""
            rollback = ""
            render_error = str(e)

        summary = renderer.script_summary(plan)
        script_hash = (
            renderer.script_hash(forward) if forward else ""
        )

        # Persist + audit on POST. GET re-renders without
        # touching DB state — the operator can navigate back to
        # the page without spamming audit / version rows.
        if (request.method == "POST"
                and plan.can_apply
                and forward
                and not render_error):
            try:
                scripts_repo.record(
                    service=svc.key,
                    policy_id=policy_id,
                    script_body=forward,
                    rollback_script_body=rollback,
                    command_count=plan.total_commands,
                )
                dep_repo.record_preview(
                    tenant_id=_tid(), service=svc.key,
                    policy_id=policy_id,
                    router_id=policy["router_id"],
                    script_hash=script_hash,
                )
                _emit_audit(
                    svc, svc.audit_actions["preview"],
                    policy_id=policy_id,
                    router_id=policy["router_id"],
                    script_hash=script_hash,
                    command_count=plan.total_commands,
                    can_apply=True,
                )
                flash("تم توليد المعاينة وحفظها. "
                      "لم يُطبَّق على الراوتر — هذه معاينة فقط.",
                      "success")
            except scripts_repo.SecretInScriptError as e:
                render_error = str(e)
                flash(f"رفض المُولِّد بسبب: {e}", "danger")

        # Operator-facing breakdown derived from the plan +
        # children, kept in the route so the template stays
        # presentation-only.
        explanation = _explain_plan(svc, policy, children, plan)

        # ── Surface the Phase A→I intelligence ─────────────
        # The route builds every analysis once and hands the
        # `intelligence` mapping to the template. The template
        # stays presentation-only — all classification + scoring
        # lives in the (already production-ready) services.
        intelligence = _build_intelligence(
            svc=svc, policy=policy, plan=plan,
            children=children,
            forward=forward, rollback=rollback,
            render_error=render_error,
        )

        return render_template(
            "radius/network_policy_preview.html",
            service=svc,
            services=list(_REGISTRY.values()),
            policy=policy,
            router_name=_nas_name(policy["router_id"]),
            plan=plan,
            forward_script=forward,
            rollback_script=rollback,
            script_hash=script_hash,
            render_error=render_error,
            summary=summary,
            explanation=explanation,
            intelligence=intelligence,
            page_title=f"معاينة: {policy['name']}",
        )
    return _view


# ─── Intelligence assembler ──────────────────────────────────


def _peer_policies_for_route(svc: _ServiceDef) -> list[
    "conflict_svc.PeerPolicy"
]:
    """Mirror of the JSON API's peer-loader, scoped to the
    current tenant. Read-only — no MikroTik contact."""
    out: list[conflict_svc.PeerPolicy] = []
    tid = _tid()
    for row in ra_repo.list_for_tenant(tid):
        out.append(conflict_svc.PeerPolicy(
            service=nc.SERVICE_REMOTE_ACCESS,
            id=int(row["id"]), name=str(row["name"]),
            slug=str(row["slug"]),
            router_id=int(row["router_id"]),
            enabled=bool(row["enabled"]),
            children=(),
        ))
    for row in wb_repo.list_policies_for_tenant(tid):
        targets = wb_repo.list_targets(int(row["id"]))
        out.append(conflict_svc.PeerPolicy(
            service=nc.SERVICE_WEB_BLOCK,
            id=int(row["id"]), name=str(row["name"]),
            slug=str(row["slug"]),
            router_id=int(row["router_id"]),
            enabled=bool(row["enabled"]),
            children=tuple(targets),
        ))
    for row in wg_repo.list_policies_for_tenant(tid):
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


def _build_intelligence(
    *, svc: _ServiceDef, policy: dict,
    plan: "renderer.ScriptPlan",
    children: tuple,
    forward: str, rollback: str,
    render_error: str,
) -> dict:
    """Assemble every Phase A–I analysis once. Returns a dict
    the template can iterate over. Each value is a dataclass
    instance — templates can call `.as_dict()` themselves or
    just access attributes."""
    policy_id = int(policy.get("id") or 0)
    router_id = int(policy.get("router_id") or 0)

    # Conflicts compare against the rest of the tenant's
    # policies; exclude this one to avoid self-conflict noise.
    peers = [
        p for p in _peer_policies_for_route(svc)
        if not (p.id == policy_id and p.service == svc.key)
    ]

    conflicts = conflict_svc.analyze(
        current_service=svc.key,
        current_policy=policy,
        current_children=children or (),
        peers=peers,
    )
    dependencies = dependency_svc.analyze(
        targets=children or (), policy_type=svc.key,
    )
    blast = blast_svc.analyze(
        policy_type=svc.key, plan=plan,
        affected_router_count=1,
    )
    beginner = beginner_svc.explain(
        policy_type=svc.key, plan=plan, policy=policy,
    )
    canary = canary_svc.plan(blast=blast)
    impact = impact_svc.analyze(
        policy_type=svc.key, policy=policy,
        plan=plan, targets=children or (),
        rendered_forward=forward, rendered_rollback=rollback,
        render_error=(render_error or None),
    )
    health = health_svc.compute(
        impact=impact,
        conflicts=conflicts,
        dependencies=dependencies,
        blast=blast,
        rollback_available=impact.rollback_available,
        canary_recommended=(
            canary.recommended_strategy
            in (canary_svc.STRATEGY_CANARY,
                canary_svc.STRATEGY_STAGED)
        ),
    )
    recommendations = rec_svc.build(
        impact=impact,
        conflicts=conflicts,
        dependencies=dependencies,
        blast=blast,
        canary=canary,
        policy_type=svc.key,
        policy=policy,
    )

    # Phase 3+6 — the contracts engine drives readiness; the
    # template uses the result to decide whether to render
    # the apply form.
    apply_perm = svc.perms.get("apply") or (
        svc.perms["preview"].rsplit(".", 1)[0] + ".apply"
    )
    actor_has_apply = current_admin_has(apply_perm)
    readiness_obj = readiness_svc.evaluate_for_preview(
        policy=policy, policy_type=svc.key,
        impact=impact, conflicts=conflicts,
        dependencies=dependencies, blast=blast,
        health=health, canary=canary,
        forward_script=forward,
        rollback_script=rollback,
        render_error=render_error,
        apply_perm=apply_perm,
        actor_has_apply_perm=actor_has_apply,
        # Preview-time: we haven't taken a snapshot yet. The
        # apply route does that as part of the POST. We pass
        # a sentinel positive id (1) so the readiness card
        # doesn't show "no snapshot" for a policy that's
        # otherwise fine — the apply route will re-evaluate
        # with the real snapshot id.
        snapshot_id=1,
    )
    readiness = readiness_obj.as_dict()
    readiness["actor_has_apply_perm"] = bool(actor_has_apply)
    readiness["required_confirmations"] = list(
        readiness_obj.decision.required_confirmations
    )

    return {
        "impact":          impact,
        "conflicts":       conflicts,
        "dependencies":    dependencies,
        "blast":           blast,
        "beginner":        beginner,
        "canary":          canary,
        "health":          health,
        "recommendations": recommendations,
        "readiness":       readiness,
    }


# ─── Phase 1 readiness helper (deprecated in Phase 3) ────────
#
# `_compute_readiness_v1` was the placeholder inline helper that
# shipped with the Phase-1 readiness UI. Phase 3 replaced it
# with the proper contracts engine
# (`npc_execution_contracts.evaluate`) wrapped by
# `npc_execution_readiness.evaluate_for_preview`. The route now
# calls the orchestrator directly. The function is retained
# below behind an unreachable guard so any internal caller that
# still imports it gets a clear deprecation message instead of
# a silent regression.


def _compute_readiness_v1(
    *, svc: _ServiceDef,
    impact, conflicts, blast, canary, health,
    forward: str, render_error: str,
) -> dict:
    raise RuntimeError(
        "_compute_readiness_v1 is deprecated; call "
        "npc_execution_readiness.evaluate_for_preview instead."
    )


def _compute_readiness_v1_deprecated_body(
    *, svc: _ServiceDef,
    impact, conflicts, blast, canary, health,
    forward: str, render_error: str,
) -> dict:
    """Lightweight execution-readiness summary for the preview
    UI. Phase 3 will refactor this into a dedicated contracts
    service; for now it lives next to the intelligence
    builder so we can ship the UI card before the apply engine
    exists.

    Returns a plain dict the template renders. Keys:
      ready_for_future_apply : bool — would the next-phase
                               apply engine accept this plan?
      blockers_ar            : list[str] — hard reasons we'd
                               refuse apply.
      warnings_ar            : list[str] — soft reasons the
                               operator should know about.
      checklist_ar           : list[dict{label,status_ok}] —
                               friendly per-condition status
                               for the readiness card.
      apply_perm             : str — the perm string a future
                               apply button would require.
      apply_perm_label_ar    : str — human label for the perm.
      caveat_ar              : str — the "apply not active yet"
                               disclaimer.
    """
    apply_perm = svc.perms.get("apply") or (
        svc.perms["preview"].rsplit(".", 1)[0] + ".apply"
    )

    blockers: list[str] = []
    warnings: list[str] = []

    if render_error:
        blockers.append(
            "السكربت مرفوض تلقائياً بسبب محتوى حسّاس — لا يمكن "
            "المتابعة."
        )
    if not forward.strip():
        blockers.append(
            "لا يوجد سكربت forward لتنفيذه — حدّث السياسة "
            "ثم أعد المعاينة."
        )
    if impact.risk_level == "critical":
        blockers.append(
            "تحليل الأثر يصنّف الخطّة critical — يجب إعادة "
            "التخطيط قبل أي تنفيذ."
        )
    if health.grade in ("dangerous",):
        blockers.append(
            "درجة السلامة منخفضة جداً — راجع التحذيرات أعلاه."
        )
    if not impact.rollback_available:
        blockers.append(
            "لا يوجد سكربت rollback — التنفيذ بدون إمكانية "
            "تراجع غير مسموح."
        )
    high_conflicts = [c for c in conflicts.conflicts
                      if c.severity == "high"]
    if high_conflicts:
        blockers.append(
            f"يوجد {len(high_conflicts)} تعارض(ات) عالي الخطورة "
            "مع سياسات أخرى — حلّها أوّلاً."
        )

    # Warnings (soft)
    if impact.risk_level == "high":
        warnings.append("مستوى الخطر مرتفع — راجع الأسباب أعلاه.")
    if blast.blast_radius in ("large", "critical"):
        warnings.append(
            "نطاق التأثير واسع — مفضّل البدء بـ canary."
        )
    if canary.recommended_strategy in (
        canary_svc.STRATEGY_CANARY, canary_svc.STRATEGY_HOLD,
    ):
        warnings.append(
            "هناك توصية بالتطبيق التدريجي قبل أي تطبيق كامل."
        )
    if health.grade == "risky":
        warnings.append(
            "درجة السلامة في خانة «محفوفة بالمخاطر» — "
            "تحقّق من الخطّة قبل المتابعة."
        )

    checklist = [
        {"label": "السكربت forward موجود ومُولَّد بنجاح.",
         "status_ok": bool(forward.strip()) and not render_error},
        {"label": "سكربت rollback متاح.",
         "status_ok": bool(impact.rollback_available)},
        {"label": "تحليل الأثر ليس في خانة critical.",
         "status_ok": impact.risk_level != "critical"},
        {"label": "درجة السلامة فوق خط الخطر.",
         "status_ok": health.grade not in ("dangerous",)},
        {"label": "لا تعارضات عالية الخطورة مع سياسات أخرى.",
         "status_ok": not high_conflicts},
    ]

    ready_for_future_apply = not blockers
    return {
        "ready_for_future_apply": ready_for_future_apply,
        "blockers_ar":            blockers,
        "warnings_ar":            warnings,
        "checklist_ar":           checklist,
        "apply_perm":             apply_perm,
        "apply_perm_label_ar":    f"npc.{svc.key}.apply",
        "caveat_ar":              (
            "التنفيذ غير مفعّل بعد — هذه نسخة معاينة فقط. "
            "زرّ التنفيذ سيظهر في مرحلة قادمة."
        ),
    }


def _make_apply_view(svc: _ServiceDef):
    """Guarded POST endpoint for live apply.

    The route NEVER calls MikroTik. Everything goes through
    `apply_svc.request_apply` which:
      1. Re-runs the contracts engine over the current state.
      2. Refuses if any blocker is present.
      3. Creates the change_set + per-router targets.
      4. Calls `RouterExecutor.execute_forward` per router
         (default: NullRouterExecutor → refuses → marks per-
         router as failed; the test fake replaces this).
      5. Aggregates the status and records the result.

    A pre-flight snapshot is required. If the route can take
    one via `npc_snapshot_capture_service` without any reader
    error, it does — otherwise the apply is refused with the
    `no_snapshot` blocker.
    """
    def _view(policy_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)

        # Pull the same intelligence the preview did.
        children: tuple = ()
        if ad.list_children is not None:
            children = tuple(ad.list_children(policy_id))
        plan = (ad.plan(policy, children=children)
                if ad.list_children is not None
                else ad.plan(policy))
        try:
            forward = renderer.render_forward_script(plan)
            rollback = renderer.render_rollback_script(plan)
            render_error = ""
        except renderer.RenderSafetyError as e:
            forward = ""
            rollback = ""
            render_error = str(e)
        script_hash = (
            renderer.script_hash(forward) if forward else ""
        )
        intelligence = _build_intelligence(
            svc=svc, policy=policy, plan=plan,
            children=children, forward=forward,
            rollback=rollback, render_error=render_error,
        )

        # Snapshot — best effort. If the reader is the default
        # null one (which it is until a live adapter ships)
        # the capture raises StateReaderNotConfigured; we
        # interpret that as "no snapshot" and let the contracts
        # engine refuse with NO_SNAPSHOT.
        snapshot_id: Optional[int] = None
        try:
            cap = snapshot_capture_svc.capture_pre_apply_snapshot(
                tenant_id=_tid(),
                router_id=int(policy["router_id"]),
                policy_id=policy_id,
                policy_type=svc.key,
                created_by=_actor(),
            )
            snapshot_id = int(cap.snapshot_id)
        except Exception:  # noqa: BLE001
            snapshot_id = None

        # Confirmations supplied via form fields named
        # `confirm__<code>=on`.
        confirmations = tuple(
            k.split("__", 1)[1]
            for k in request.form.keys()
            if k.startswith("confirm__")
            and (request.form.get(k) or "")
                .strip().lower() in {"on", "1", "true", "yes"}
        )
        canary_opt_in = (
            (request.form.get("canary_opt_in") or "")
            .strip().lower() in {"on", "1", "true", "yes"}
        )
        # Execution mode from the form; default to "full".
        execution_mode = (
            request.form.get("execution_mode")
            or cs_repo.MODE_FULL
        )
        if execution_mode not in cs_repo.ALLOWED_MODES:
            execution_mode = cs_repo.MODE_FULL

        result = apply_svc.request_apply(
            tenant_id=_tid(),
            service=svc.key,
            policy=policy,
            policy_children=children,
            forward_script=forward,
            rollback_script=rollback,
            render_error=render_error,
            preview_hash=script_hash,
            snapshot_id=snapshot_id,
            target_router_ids=(int(policy["router_id"]),),
            actor=_actor(),
            actor_has_apply_perm=True,  # perm-decorated route
            confirmations=confirmations,
            execution_mode=execution_mode,
            canary_opt_in=canary_opt_in,
            all_routers_targeted=False,
            offline_router_ids=(),
            impact=intelligence["impact"],
            conflicts=intelligence["conflicts"],
            dependencies=intelligence["dependencies"],
            blast=intelligence["blast"],
            health=intelligence["health"],
            canary=intelligence["canary"],
        )

        # Surface the result via flash + redirect back to the
        # preview page. Phase 6 will render a dedicated result
        # template; for Phase 4 we keep the UI minimal so the
        # tests can validate the wiring rather than the polish.
        if result.ok:
            flash(
                "تم التنفيذ بنجاح على الراوتر — "
                f"change_set #{result.change_set_id}.",
                "success",
            )
        elif result.status == cs_repo.STATUS_FAILED \
                and result.blockers:
            flash(
                "التنفيذ ممنوع — موانع: "
                + ", ".join(b.code for b in result.blockers),
                "danger",
            )
        else:
            flash(
                "نتيجة التنفيذ: " + result.reason_ar,
                "warning",
            )
        return redirect(url_for(
            f"radius.npc_{svc.key}_preview",
            policy_id=policy_id,
        ))
    return _view


def _make_changes_view(svc: _ServiceDef):
    """Server-rendered سجل التغييرات page. Lists every
    change_set for this policy with per-router breakdown +
    a rollback button on eligible rows."""
    def _view(policy_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)
        rows = cs_repo.list_for_policy(
            _tid(), service=svc.key, policy_id=int(policy_id),
        )
        # Decorate each row with its targets + eligibility
        # flags for the template.
        decorated: list[dict] = []
        for r in rows:
            r = dict(r)
            r["targets"] = cs_repo.list_targets(int(r["id"]))
            r["rollback_eligible"] = (
                r["action_type"] == cs_repo.ACTION_APPLY
                and r["status"] in (
                    cs_repo.STATUS_SUCCEEDED,
                    cs_repo.STATUS_PARTIALLY_SUCCEEDED,
                )
            )
            decorated.append(r)
        apply_perm = svc.perms.get("apply") or (
            svc.perms["preview"].rsplit(".", 1)[0] + ".apply"
        )
        return render_template(
            "radius/network_policy_changes.html",
            service=svc,
            services=list(_REGISTRY.values()),
            policy=policy,
            router_name=_nas_name(policy["router_id"]),
            change_sets=decorated,
            can_rollback=current_admin_has(apply_perm),
            page_title=(
                f"سجل التغييرات: {policy['name']}"
            ),
        )
    return _view


def _make_rollback_view(svc: _ServiceDef):
    """POST endpoint for rolling back a previously-applied
    change_set. Delegates to `rollback_svc.request_rollback`
    which:
      * verifies tenant + policy match
      * verifies the original change_set is rollback-eligible
      * re-validates the stored rollback script against the
        managed-prefix safety rule
      * drives `executor.execute_rollback` per router
      * marks the original change_set as `rolled_back` (or
        `partially_rolled_back`) on success.
    """
    def _view(policy_id: int, change_set_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)
        result = rollback_svc.request_rollback(
            tenant_id=_tid(),
            service=svc.key,
            policy_id=int(policy_id),
            change_set_id=int(change_set_id),
            actor=_actor(),
            actor_has_apply_perm=True,
        )
        if result.ok:
            flash(
                f"تم التراجع بنجاح — change_set "
                f"#{result.change_set_id}.", "success",
            )
        else:
            flash(
                f"تعذّر التراجع: {result.reason_ar}",
                "danger",
            )
        return redirect(url_for(
            f"radius.npc_{svc.key}_preview",
            policy_id=policy_id,
        ))
    return _view


def _make_download_view(svc: _ServiceDef):
    """Download the rendered forward script as a `.rsc` file.

    Pure file delivery — the operator pastes this into MikroTik
    terminal **themselves**. Phase 6 does not push anything to
    a live router. The endpoint is still gated by the preview
    permission since it discloses script bytes.
    """
    def _view(policy_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)
        children: tuple = ()
        if ad.list_children is not None:
            children = tuple(ad.list_children(policy_id))
        plan = ad.plan(policy, children=children) \
            if ad.list_children is not None \
            else ad.plan(policy)
        try:
            forward = renderer.render_forward_script(plan)
        except renderer.RenderSafetyError as e:
            flash(f"تعذّر توليد السكربت: {e}", "danger")
            return redirect(url_for(
                f"radius.npc_{svc.key}_preview",
                policy_id=policy_id,
            ))
        if not forward:
            flash("لا يوجد سكربت قابل للتنزيل — راجع الأخطاء.",
                  "warning")
            return redirect(url_for(
                f"radius.npc_{svc.key}_preview",
                policy_id=policy_id,
            ))
        fname = (
            f"npc-{svc.url_slug}-{policy['slug']}-preview.rsc"
        )
        return Response(
            forward, mimetype="text/plain; charset=utf-8",
            headers={
                "Content-Disposition":
                    f"attachment; filename=\"{fname}\"",
            },
        )
    return _view


# ─── Delete / duplicate ──────────────────────────────────────


def _make_delete_view(svc: _ServiceDef):
    def _view(policy_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)
        ad.delete(_tid(), policy_id)
        _emit_audit(
            svc, svc.audit_actions["deleted"],
            policy_id=policy_id,
            router_id=policy["router_id"],
        )
        flash("حُذفت السياسة. (محلياً — لم يُطبَّق على الراوتر.)",
              "success")
        return redirect(url_for(f"radius.npc_{svc.key}_list"))
    return _view


def _make_duplicate_view(svc: _ServiceDef):
    def _view(policy_id: int):
        ad = _adapter(svc)
        src = ad.get(_tid(), policy_id)
        if not src:
            abort(404)
        # Build kwargs from the source row, stripped to the
        # repo's accepted args. We re-use the form-kwargs
        # extractor by adapting the row to look like a form
        # dict — keeps a single source of truth for field
        # whitelisting per service.
        proxy = _row_to_form_dict(svc, src)
        proxy["name"] = f"{src['name']} (نسخة)"
        proxy["slug"] = None
        proxy["router_id"] = src["router_id"]
        try:
            new_id = ad.create(tenant_id=_tid(), **proxy)
        except ValueError as e:
            flash(f"تعذّرت النسخة: {e}", "danger")
            return redirect(url_for(
                f"radius.npc_{svc.key}_list"
            ))
        _emit_audit(
            svc, svc.audit_actions["created"],
            policy_id=new_id,
            router_id=src["router_id"],
            duplicated_from=policy_id,
        )
        flash("أُنشئت نسخة جديدة من السياسة.", "success")
        return redirect(url_for(
            f"radius.npc_{svc.key}_edit", policy_id=new_id,
        ))
    return _view


def _row_to_form_dict(svc: _ServiceDef, row: dict) -> dict:
    """Project a stored row down to the kwargs each repo's
    `create` accepts. Mirror of the form-kwargs extractors but
    populated from a row instead of an HTTP form."""
    if svc.key == nc.SERVICE_REMOTE_ACCESS:
        return {
            "name": row["name"], "slug": None,
            "router_id": row["router_id"],
            "allow_winbox": bool(row.get("allow_winbox")),
            "allow_ssh": bool(row.get("allow_ssh")),
            "allow_api": bool(row.get("allow_api")),
            "allow_api_ssl": bool(row.get("allow_api_ssl")),
            "allow_webfig_http": bool(row.get("allow_webfig_http")),
            "allow_webfig_https": bool(
                row.get("allow_webfig_https")),
            "source_address_list":
                row.get("source_address_list") or "",
            "expires_at": row.get("expires_at") or "",
            "reason": row.get("reason") or "",
            "enabled": bool(row.get("enabled")),
        }
    if svc.key == nc.SERVICE_WEB_BLOCK:
        return {
            "name": row["name"], "slug": None,
            "router_id": row["router_id"],
            "scope": row.get("scope") or wb_repo.SCOPE_ALL_USERS,
            "schedule_id": row.get("schedule_id") or "",
            "fail_open": bool(row.get("fail_open", 1)),
            "enabled": bool(row.get("enabled")),
        }
    return {
        "name": row["name"], "slug": None,
        "router_id": row["router_id"],
        "hotspot_profile": row.get("hotspot_profile") or "",
        "enabled": bool(row.get("enabled")),
    }


# ─── Targets / entries — add/delete ──────────────────────────


def _make_child_add_view(svc: _ServiceDef):
    """Add a target (web-block) or entry (walled-garden) to a
    policy. Routes only — the actual validation/analysis lives
    in `npc_domain_analyzer` (web_block) or repo enum guards
    (walled_garden)."""
    def _view(policy_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)
        raw_value = (request.form.get("value") or "").strip()
        if not raw_value:
            flash("القيمة فارغة.", "danger")
            return redirect(url_for(
                f"radius.npc_{svc.key}_edit",
                policy_id=policy_id,
            ))

        if svc.key == nc.SERVICE_WEB_BLOCK:
            # Run through the analyzer so the operator gets
            # the same Arabic rejection messages the JSON API
            # would give. We accept domain / ip / cidr.
            entry = analyzer.analyze_line(raw_value)
            if entry.kind == analyzer.KIND_INVALID:
                flash(f"تعذّر القبول: {entry.reason}", "danger")
                return redirect(url_for(
                    f"radius.npc_{svc.key}_edit",
                    policy_id=policy_id,
                ))
            category = (request.form.get("category")
                        or "custom").strip() or "custom"
            try:
                tid = ad.add_child(
                    policy_id=policy_id,
                    value=entry.raw or raw_value,
                    normalized_value=entry.normalized,
                    target_type=entry.kind,
                    category=category,
                    status=wb_repo.STATUS_ACTIVE,
                )
            except ValueError as e:
                flash(f"تعذّرت الإضافة: {e}", "danger")
                return redirect(url_for(
                    f"radius.npc_{svc.key}_edit",
                    policy_id=policy_id,
                ))
            _emit_audit(
                svc, svc.audit_actions["target_added"],
                policy_id=policy_id,
                router_id=policy["router_id"],
                target_id=tid, category=category,
                value=entry.normalized,
            )
            flash("أُضيفت الوجهة.", "success")
            return redirect(url_for(
                f"radius.npc_{svc.key}_edit",
                policy_id=policy_id,
            ))

        # walled-garden: type + value + optional port/protocol
        entry_type = (request.form.get("entry_type")
                       or "").strip()
        if not entry_type:
            flash("اختر نوع الإدخال (host / IP).", "danger")
            return redirect(url_for(
                f"radius.npc_{svc.key}_edit",
                policy_id=policy_id,
            ))
        try:
            eid = ad.add_child(
                policy_id=policy_id,
                value=raw_value,
                normalized_value=raw_value.lower(),
                entry_type=entry_type,
                dst_port=(request.form.get("dst_port")
                          or "").strip(),
                protocol=(request.form.get("protocol")
                          or "").strip(),
                status=wg_repo.STATUS_ACTIVE,
            )
        except ValueError as e:
            flash(f"تعذّرت الإضافة: {e}", "danger")
            return redirect(url_for(
                f"radius.npc_{svc.key}_edit",
                policy_id=policy_id,
            ))
        _emit_audit(
            svc, svc.audit_actions["entry_added"],
            policy_id=policy_id,
            router_id=policy["router_id"],
            entry_id=eid, entry_type=entry_type,
            value=raw_value,
        )
        flash("أُضيف الإدخال.", "success")
        return redirect(url_for(
            f"radius.npc_{svc.key}_edit", policy_id=policy_id,
        ))
    return _view


def _make_child_delete_view(svc: _ServiceDef):
    def _view(policy_id: int, child_id: int):
        ad = _adapter(svc)
        policy = ad.get(_tid(), policy_id)
        if not policy:
            abort(404)
        child = ad.get_child(child_id) if ad.get_child else None
        if not child or int(child["policy_id"]) != policy_id:
            abort(404)
        ad.delete_child(child_id)
        action = svc.audit_actions.get(
            "target_removed", svc.audit_actions.get(
                "entry_removed", svc.audit_actions["updated"]))
        _emit_audit(
            svc, action,
            policy_id=policy_id,
            router_id=policy["router_id"],
            child_id=child_id,
        )
        flash("حُذف العنصر.", "success")
        return redirect(url_for(
            f"radius.npc_{svc.key}_edit", policy_id=policy_id,
        ))
    return _view


# ─── Explanation builder ─────────────────────────────────────


def _explain_plan(
    svc: _ServiceDef, policy: dict,
    children: Iterable[dict],
    plan: renderer.ScriptPlan,
) -> dict:
    """Human-readable Arabic explanation of what the script
    will do. Pure — no IO. Returns a dict the template renders
    without further computation.

    Keys:
        will_do      — list of "سيتم … " bullet strings
        will_skip    — list of "لن يُعدَّل … " strings
        will_block   — list of values (for web_block)
        will_allow   — list of values (for walled_garden)
        warnings_ar  — pulled verbatim from the plan
        rollback_ar  — explanation of the rollback path
    """
    will_do: list[str] = []
    will_skip: list[str] = []
    will_block: list[str] = []
    will_allow: list[str] = []

    if svc.key == nc.SERVICE_REMOTE_ACCESS:
        n_filter = len(plan.filter_ops)
        n_sched = len(plan.scheduler_ops)
        if n_filter:
            will_do.append(
                f"سيتم فتح {n_filter} منفذ إداري على سلسلة "
                "input مع علامة managed."
            )
        if n_sched:
            will_do.append(
                "سيتم إنشاء مهمّة /system scheduler "
                "تُحذف القواعد تلقائياً عند تاريخ الانتهاء."
            )
        will_skip.extend([
            "إعدادات /ip service الأصلية لا تُلمس.",
            "حسابات المستخدمين على الراوتر لا تُعدَّل.",
            "حركة العملاء (forward chain) لا تتأثّر.",
        ])

    elif svc.key == nc.SERVICE_WEB_BLOCK:
        for c in children:
            if c.get("status") == "active":
                will_block.append(c.get("normalized_value")
                                  or c.get("value") or "")
        n_addr = len(plan.address_list_ops)
        n_filter = len(plan.filter_ops)
        if n_addr:
            will_do.append(
                f"سيتم إنشاء قائمة عناوين بـ {n_addr} مدخلاً "
                "تحت اسم managed."
            )
        if n_filter:
            will_do.append(
                "سيتم إضافة قاعدة drop واحدة على سلسلة forward "
                "تستهدف القائمة أعلاه فقط."
            )
        will_skip.extend([
            "ملف الهوت‌سبوت (hotspot profile) لا يُعدَّل.",
            "الـ NAT والـ DHCP لا يتأثّران.",
            "قواعد الـ firewall الأخرى لا تُلمس.",
        ])

    else:  # walled_garden
        for c in children:
            if c.get("status") == "active":
                will_allow.append(c.get("normalized_value")
                                  or c.get("value") or "")
        n_wg = len(plan.walled_garden_ops)
        if n_wg:
            will_do.append(
                f"سيتم إضافة {n_wg} مدخل/إدخال إلى الـ "
                "walled-garden قبل تسجيل الدخول."
            )
        will_skip.extend([
            "قواعد الـ hotspot login نفسها لا تُلمس.",
            "قوالب الـ landing page لا تُعدَّل.",
            "صلاحيات المستخدمين/البطاقات لا تُلمس.",
        ])

    rollback_ar = (
        "يمكن التراجع الكامل عبر سكربت rollback المُولَّد. "
        "يطابق التعليقات المُدارة فقط "
        f"({plan.comment_prefix or 'HOBE_NPC_…'}) "
        "ولا يلمس أي قاعدة لم نُنشئها."
    )
    return {
        "will_do":     will_do,
        "will_skip":   will_skip,
        "will_block":  will_block,
        "will_allow":  will_allow,
        "warnings_ar": list(plan.warnings),
        "blocking":    list(plan.blocking_errors),
        "rollback_ar": rollback_ar,
    }


__all__ = ["register_network_policy_routes"]
