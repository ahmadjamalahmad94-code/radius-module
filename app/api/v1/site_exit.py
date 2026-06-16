"""site-exit — v1 JSON API (feat/api-first-parity, group 3).

Mirrors the site-exit web page (`routes/site_exit.py`,
`/admin/radius/site-exit/<nas_id>`) as JSON: the page **state** (policies,
selected policy, deployment, targets, group counts, VPS exit nodes, presets)
and the **plan** (forward + rollback RouterOS scripts + summary, read-only —
no wire). Plus policy create. Reuses the same repos + planner/renderer.

Scope note: the live **apply** (and targets-save / seed-import) cross the wire
with a 5-confirmation safety gate and need VPS/NAS acceptance — they are an
explicit follow-up (the web apply button is itself UI-gated today). The plan
endpoint already returns the rollback script so the app can display it.
"""
from __future__ import annotations

import dataclasses

from flask import Blueprint, g, request

from ...radius.db.connection import db
from ...radius.db.repos import (
    site_exit_deployments_repo as deployments_repo,
    site_exit_policies_repo as policies_repo,
    site_exit_targets_repo as targets_repo,
    vps_exit_nodes_repo as nodes_repo,
)
from ...radius.services import (
    site_exit_presets as presets_svc,
    site_exit_script_planner as planner,
    site_exit_script_renderer as renderer,
)
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/site-exit/routers/<int:nas_id>", "site_exit_state",
                    require_api_token(state), methods=["GET"])
    bp.add_url_rule("/site-exit/routers/<int:nas_id>/policies",
                    "site_exit_policy_create", require_api_token(create_policy),
                    methods=["POST"])
    bp.add_url_rule("/site-exit/routers/<int:nas_id>/policies/<int:policy_id>/plan",
                    "site_exit_plan", require_api_token(plan), methods=["GET"])


def _load_nas(nas_id: int):
    row = db().execute(
        "SELECT id, name, address, enabled, connection_mode, vpn_peer_address "
        "FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), _tid()),
    ).fetchone()
    return dict(row) if row else None


def _load_policy(nas_id: int, policy_id: int):
    row = policies_repo.get_by_id(_tid(), int(policy_id))
    if not row or int(row.get("router_id") or 0) != int(nas_id):
        return None
    return row


def _presets_json() -> list[dict]:
    # تُعرض البيانات الوصفية فقط — لا نُرسل body الخام الضخم.
    out = []
    for p in presets_svc.list_presets():
        out.append({
            "key": getattr(p, "key", ""),
            "label_ar": getattr(p, "label_ar", ""),
            "description_ar": getattr(p, "description_ar", ""),
            "target_count": getattr(p, "target_count", 0),
        })
    return out


def _group_meta() -> dict:
    try:  # ثابت تسمية واجهة فقط — استيراد كسول لتفادي دورة الاستيراد.
        from ...radius.routes.site_exit import GROUP_META
        return GROUP_META
    except Exception:  # noqa: BLE001
        return {}


def state(nas_id: int):
    """GET /site-exit/routers/<nas_id> — حالة الصفحة. ?policy_id لاختيار سياسة."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    policies = policies_repo.list_for_router(_tid(), int(nas["id"]))
    policy = policies[-1] if policies else None
    requested = request.args.get("policy_id")
    if requested and str(requested).isdigit():
        override = _load_policy(int(nas["id"]), int(requested))
        if override:
            policy = override
    pid = int(policy["id"]) if policy else 0
    return ok({
        "nas": nas,
        "policies": policies,
        "policy": policy,
        "deployment": deployments_repo.get_for_policy(_tid(), pid) if pid else None,
        "targets": targets_repo.list_for_policy(pid) if pid else [],
        "group_counts": targets_repo.group_counts(pid) if pid else None,
        "vps_nodes": nodes_repo.list_for_tenant(_tid()),
        "presets": _presets_json(),
        "group_meta": _group_meta(),
        "apply_disabled_reason": (
            "زر التطبيق سيُفعَّل بعد فحص الأمان والتأكيدات الصريحة (متابعة)."),
    })


def create_policy(nas_id: int):
    """POST /site-exit/routers/<nas_id>/policies — إنشاء سياسة (يطابق
    site_exit_policy_create)."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return fail("validation_error", "اسم السياسة مطلوب.", status=422)
    try:
        nid = int(body.get("exit_node_id"))
    except (TypeError, ValueError):
        return fail("validation_error", "اختر عقدة VPS أولاً.", status=422)
    if not nodes_repo.get_by_id(_tid(), nid):
        return fail("validation_error", "عقدة VPS غير معروفة.", status=422)
    fail_mode = (str(body.get("fail_mode") or "").strip()
                 or policies_repo.FAIL_MODE_BLOCK_WHEN_VPS_DOWN)
    try:
        pid = policies_repo.create(
            tenant_id=_tid(), router_id=int(nas["id"]), exit_node_id=nid,
            name=name, fail_mode=fail_mode,
            include_subdomains=bool(body.get("include_subdomains")),
            include_router_output=bool(body.get("include_router_output")),
        )
    except ValueError as exc:
        return fail("validation_error", f"تعذّر إنشاء السياسة: {exc}", status=422)
    except Exception:  # noqa: BLE001 — uniqueness collisions etc.
        return fail("conflict", "سياسة باسم/مُعرّف مكرَّر.", status=409)
    return ok({"policy": policies_repo.get_by_id(_tid(), pid)}, status=201)


def _skipped_json(plan) -> list:
    out = []
    for s in getattr(plan, "targets_skipped", ()) or ():
        try:
            out.append(dataclasses.asdict(s))
        except TypeError:
            out.append(str(s))
    return out


def plan(nas_id: int, policy_id: int):
    """GET /site-exit/routers/<nas_id>/policies/<policy_id>/plan — المعاينة:
    سكربتا forward/rollback + الملخّص (قراءة فقط، بلا اتصال بالراوتر).
    ?wan_interface_list اختياري (يطابق نموذج المعاينة)."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    policy = _load_policy(int(nas["id"]), policy_id)
    if not policy:
        return fail("not_found", "السياسة غير موجودة.", status=404)
    exit_node = nodes_repo.get_by_id(_tid(), int(policy["exit_node_id"]))
    targets = targets_repo.list_for_policy(int(policy_id))
    wan = (request.args.get("wan_interface_list") or "").strip() or None
    p = planner.build_plan(policy=policy, exit_node=exit_node or {"enabled": 0},
                           targets=targets, wan_interface_list=wan)
    return ok({
        "policy_id": int(policy_id),
        "can_apply": p.can_apply,
        "forward_script": renderer.render_forward_script(p) if p.can_apply else "",
        "rollback_script": renderer.render_rollback_script(p) if p.can_apply else "",
        "summary": renderer.script_summary(p),
        "total_commands": p.total_commands,
        "warnings": list(p.warnings),
        "blocking_errors": list(p.blocking_errors),
        "targets_skipped": _skipped_json(p),
    })
