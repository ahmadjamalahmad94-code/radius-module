"""VX2.4 — Site-exit routing UI + preview flow.

Single per-router page that lets an operator:

  - see / create / edit a `site_exit_policies` row
  - paste a MikroTik address-list seed file and review the
    classified import buckets
  - pick which classifier groups to include
  - generate a preview (forward + rollback scripts) — *but
    NOT apply*; apply lands in VX2.6 behind safety checks +
    explicit confirmations.

The route is intentionally narrow in VX2.4: no audit events,
no apply, no permission additions beyond PERM_VIEW. VX2.5 adds
permissions + audit + safety; VX2.6 wires the real apply.
Splitting the work this way keeps each commit small and the
review surface honest.

Every mutating endpoint accepts a tenant-scoped (router_id,
policy_id) pair and refuses to operate outside that scope.
404 for unknown router. 403 via the existing PERM_VIEW guard.
"""
from __future__ import annotations

from typing import Optional

from flask import (
    Blueprint, abort, flash, g, redirect, render_template,
    request, url_for,
)

from ..auth.session_helpers import current_admin_id
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import admins_repo
from ..db.repos import (
    site_exit_deployments_repo as deployments_repo,
    site_exit_policies_repo    as policies_repo,
    site_exit_scripts_repo     as scripts_repo,
    site_exit_targets_repo     as targets_repo,
    vps_exit_nodes_repo        as nodes_repo,
)
from ..integration.mikrotik.client import MikrotikClient
from ..services import (
    mt_programming,
    site_exit_classifier      as classifier,
    site_exit_importer        as importer,
    site_exit_safety          as safety_svc,
    site_exit_script_planner  as planner,
    site_exit_script_renderer as renderer,
    site_exit_validator       as validator,
)
from ..services.audit import get_audit_service
from ..services.mt_permissions import (
    PERM_SITE_EXIT_APPLY,
    PERM_SITE_EXIT_ENABLE_RISKY_GROUPS,
    PERM_SITE_EXIT_MANAGE,
    PERM_SITE_EXIT_OVERRIDE_BACKUP_WARNING,
    PERM_SITE_EXIT_PREVIEW,
    PERM_SITE_EXIT_VIEW,
    admin_permissions,
    requires_perm,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _load_nas(nas_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT id, name, address, enabled, connection_mode "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), _tid()),
    ).fetchone()
    return dict(row) if row else None


# ─── Group metadata for the UI ───────────────────────────────


GROUP_META: dict[str, dict] = {
    classifier.GROUP_SPEEDTEST_MEASUREMENT: {
        "label_ar": "اختبارات السرعة",
        "risk_ar":  "منخفض",
        "default_enabled": True,
        "risky":   False,
    },
    classifier.GROUP_PUBLIC_IP_CHECKERS: {
        "label_ar": "كاشفات الـ IP العام",
        "risk_ar":  "منخفض",
        "default_enabled": True,
        "risky":   False,
    },
    classifier.GROUP_RAW_IP_TARGETS: {
        "label_ar": "عناوين IP / CIDR مباشرة",
        "risk_ar":  "متوسط",
        "default_enabled": True,
        "risky":   False,
    },
    classifier.GROUP_VPN_PROVIDER_PAGES: {
        "label_ar": "صفحات مزوّدي VPN",
        "risk_ar":  "مرتفع",
        "default_enabled": False,
        "risky":   True,
    },
    classifier.GROUP_NETWORK_DIAGNOSTICS: {
        "label_ar": "أدوات التشخيص الشبكي",
        "risk_ar":  "متوسط",
        "default_enabled": False,
        "risky":   True,
    },
    classifier.GROUP_GENERAL_PROBE_SITES: {
        "label_ar": "مواقع فحص عامة (Google …)",
        "risk_ar":  "مرتفع — قد يؤثر على CDNs",
        "default_enabled": False,
        "risky":   True,
    },
    classifier.GROUP_MANUAL_REVIEW: {
        "label_ar": "مراجعة يدوية",
        "risk_ar":  "غير مصنّف — اقرأ أولاً",
        "default_enabled": False,
        "risky":   True,
    },
}


def register_site_exit_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/site-exit",
        "site_exit_page",
        requires_perm(PERM_SITE_EXIT_VIEW)(site_exit_page),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/site-exit/policies",
        "site_exit_policy_create",
        requires_perm(PERM_SITE_EXIT_MANAGE)(site_exit_policy_create),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/site-exit/policies/<int:policy_id>/import",
        "site_exit_import",
        requires_perm(PERM_SITE_EXIT_PREVIEW)(site_exit_import),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/site-exit/policies/<int:policy_id>/targets",
        "site_exit_targets_save",
        requires_perm(PERM_SITE_EXIT_MANAGE)(site_exit_targets_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/site-exit/policies/<int:policy_id>/preview",
        "site_exit_preview",
        requires_perm(PERM_SITE_EXIT_PREVIEW)(site_exit_preview),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/site-exit/policies/<int:policy_id>/apply",
        "site_exit_apply",
        requires_perm(PERM_SITE_EXIT_APPLY)(site_exit_apply),
        methods=["POST"],
    )


# ─── Page render ─────────────────────────────────────────────


def _load_policy(
    nas_id: int, policy_id: int,
) -> Optional[dict]:
    """Lookup a policy AND verify it belongs to this router.
    Returns None on either miss — caller decides 404 vs flash."""
    row = policies_repo.get_by_id(_tid(), int(policy_id))
    if not row:
        return None
    if int(row.get("router_id") or 0) != int(nas_id):
        return None
    return row


def _vps_nodes() -> list[dict]:
    return nodes_repo.list_for_tenant(_tid())


def _render_page(
    *, nas: dict,
    policy: Optional[dict] = None,
    import_result: Optional[importer.ImportResult] = None,
    preview_plan: Optional[planner.ScriptPlan] = None,
    forward_script: str = "",
    rollback_script: str = "",
    error: str = "",
    notice: str = "",
):
    pid = int(policy["id"]) if policy else 0
    policies = policies_repo.list_for_router(_tid(), int(nas["id"]))
    deployment = (
        deployments_repo.get_for_policy(_tid(), pid)
        if pid else None
    )
    targets = (
        targets_repo.list_for_policy(pid) if pid else []
    )
    group_counts = (
        targets_repo.group_counts(pid) if pid else None
    )
    return render_template(
        "radius/site_exit.html",
        nas=nas,
        policies=policies,
        policy=policy,
        deployment=deployment,
        targets=targets,
        group_counts=group_counts,
        vps_nodes=_vps_nodes(),
        group_meta=GROUP_META,
        import_result=import_result,
        preview_plan=preview_plan,
        forward_script=forward_script,
        rollback_script=rollback_script,
        # script_summary lets the template show a compact
        # counts/header card without re-walking the plan.
        preview_summary=(
            renderer.script_summary(preview_plan)
            if preview_plan else None
        ),
        error=error, notice=notice,
        # Apply is intentionally disabled in VX2.4 — surface the
        # reason so the operator isn't confused by a dead button.
        apply_disabled_reason=(
            "زر التطبيق سيُفعَّل بعد VX2.5/VX2.6 (فحص الأمان"
            " والتأكيدات الصريحة)."
        ),
    )


def site_exit_page(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    # Show the most-recent policy by default (or none).
    policies = policies_repo.list_for_router(_tid(), nas["id"])
    policy = policies[-1] if policies else None
    requested = request.args.get("policy_id")
    if requested:
        try:
            override = _load_policy(nas["id"], int(requested))
            if override:
                policy = override
        except (TypeError, ValueError):
            pass
    return _render_page(nas=nas, policy=policy)


# ─── Policy create ───────────────────────────────────────────


def site_exit_policy_create(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    name = (request.form.get("name") or "").strip()
    fail_mode = (request.form.get("fail_mode")
                  or policies_repo.FAIL_MODE_BLOCK_WHEN_VPS_DOWN
                  ).strip()
    include_subs = bool(request.form.get("include_subdomains"))
    include_outp = bool(request.form.get("include_router_output"))
    exit_node_id = (request.form.get("exit_node_id") or "").strip()

    if not name:
        flash("اسم السياسة مطلوب.", "danger")
        return redirect(url_for(
            "radius.site_exit_page", nas_id=nas_id))
    try:
        nid = int(exit_node_id)
    except (TypeError, ValueError):
        flash("اختر عقدة VPS أولاً.", "danger")
        return redirect(url_for(
            "radius.site_exit_page", nas_id=nas_id))
    if not nodes_repo.get_by_id(_tid(), nid):
        flash("عقدة VPS غير معروفة.", "danger")
        return redirect(url_for(
            "radius.site_exit_page", nas_id=nas_id))

    try:
        pid = policies_repo.create(
            tenant_id=_tid(),
            router_id=int(nas["id"]),
            exit_node_id=nid,
            name=name,
            fail_mode=fail_mode,
            include_subdomains=include_subs,
            include_router_output=include_outp,
        )
    except ValueError as e:
        flash(f"تعذّر إنشاء السياسة: {e}", "danger")
        return redirect(url_for(
            "radius.site_exit_page", nas_id=nas_id))
    except Exception:  # noqa: BLE001 — uniqueness collisions etc.
        flash("سياسة باسم/مُعرّف مكرَّر.", "danger")
        return redirect(url_for(
            "radius.site_exit_page", nas_id=nas_id))

    flash("أُنشئت السياسة بنجاح.", "success")
    return redirect(url_for(
        "radius.site_exit_page", nas_id=nas_id, policy_id=pid))


# ─── Seed import (preview only — no DB writes) ───────────────


def site_exit_import(nas_id: int, policy_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    policy = _load_policy(nas["id"], policy_id)
    if not policy:
        abort(404)

    raw = request.form.get("seed_text") or ""
    advanced = bool(request.form.get("advanced_mode"))
    result = importer.parse_address_list(
        raw, advanced_mode=advanced)
    return _render_page(
        nas=nas, policy=policy, import_result=result,
        notice=(
            f"تم تحليل {result.total_parsed} سطرًا — "
            f"مقبولة: {len(result.accepted)}، "
            f"مكرّرة: {len(result.duplicates)}، "
            f"غير صالحة: {len(result.invalid)}."
        ),
    )


# ─── Targets persist (selected groups + optional manual list) ─


def site_exit_targets_save(nas_id: int, policy_id: int):
    """Persist the operator-selected targets to the policy.

    Accepts two input shapes (one or the other; both is fine):
      - `paste_text`: line-separated manual targets (no group
        chooser — each line is validated, classified, then
        inserted).
      - `seed_text` + `enabled_groups[]`: the same seed-file
        body the import previewed plus a list of group names
        the operator checked to keep. Only those groups land in
        site_exit_targets.

    The implementation is conservative: every accepted line
    goes through the validator + classifier so the persisted
    rows match what the importer preview showed.
    """
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    policy = _load_policy(nas["id"], policy_id)
    if not policy:
        abort(404)

    advanced = bool(request.form.get("advanced_mode"))
    seed_text = request.form.get("seed_text") or ""
    paste_text = request.form.get("paste_text") or ""
    enabled_groups = set(request.form.getlist("enabled_groups"))

    # Build candidates from both inputs, dedup by normalized.
    candidates: list[dict] = []
    if seed_text:
        parsed = importer.parse_address_list(
            seed_text, advanced_mode=advanced)
        # `manual_review` items aren't auto-included unless the
        # operator explicitly ticked that group.
        for t in (*parsed.accepted, *parsed.manual_review):
            if t.group_name not in enabled_groups:
                continue
            candidates.append({
                "value":            t.value,
                "normalized_value": t.normalized_value,
                "target_type":      t.target_type,
                "group_name":       t.group_name,
                "include_www":      t.include_www,
                "include_subdomains": t.include_subdomains,
            })
    if paste_text:
        for raw_line in paste_text.splitlines():
            v = raw_line.strip()
            if not v:
                continue
            res = validator.validate(v, advanced_mode=advanced)
            if not res.valid:
                continue
            grp = classifier.classify(res.normalized,
                                       res.target_type)
            candidates.append({
                "value":            v,
                "normalized_value": res.normalized,
                "target_type":      res.target_type,
                "group_name":       grp,
                "include_www": res.target_type == "domain",
                "include_subdomains":
                    res.target_type == "domain",
            })

    if not candidates:
        flash("لم تُحدَّد أهداف لحفظها.", "warning")
        return redirect(url_for(
            "radius.site_exit_page", nas_id=nas_id,
            policy_id=policy_id))

    out = targets_repo.add_many(int(policy_id), candidates)
    flash(
        f"حُفظت الأهداف — مُدخلة: {out['inserted']}، "
        f"محدَّثة: {out['updated']}، "
        f"متجاهَلة: {out['skipped']}.",
        "success",
    )
    return redirect(url_for(
        "radius.site_exit_page", nas_id=nas_id,
        policy_id=policy_id))


# ─── Preview (NO apply, NO mutation to deployment row) ───────


def _current_admin():
    aid = current_admin_id()
    if not aid:
        return None
    try:
        return admins_repo.get_admin(int(aid))
    except Exception:  # noqa: BLE001
        return None


def _connect_client(nas: dict):
    """Open a live MikrotikClient using the same shape as
    mt_programming. Caller is responsible for `.close()`."""
    return MikrotikClient(
        host=nas["address"],
        port=int(nas.get("api_port") or 8728),
        username=nas.get("api_user") or "admin",
        password=nas.get("api_password") or "",
        use_tls=bool(nas.get("api_use_tls")),
        timeout=10,
    )


def _plan_to_commands(
    plan: planner.ScriptPlan,
) -> list[mt_programming.Command]:
    """Convert PlanCommand `add` ops to mt_programming.Command
    objects that the existing apply_commands executor accepts.

    REMOVE ops are NOT converted — they use the RouterOS script
    `[find comment~"..."]` syntax which is not addressable via
    the RouterOS API. The operator runs the rollback script
    manually if needed. This is the conservative, audit-safe
    choice for VX2.6: we never call /remove without an
    explicit ID, so we cannot accidentally delete unmanaged
    rules through an over-broad pattern."""
    out: list[mt_programming.Command] = []
    sections: list[tuple] = [
        plan.routing_table_ops,
        plan.route_ops,
        plan.address_list_ops,
        plan.dns_ops,
        plan.mangle_ops,
        plan.firewall_filter_ops,
    ]
    for cmd_tuple in sections:
        for cmd in cmd_tuple:
            if cmd.kind != "add":
                continue
            # Strip empty-string-valued flags into bare paths
            # — mt_programming.apply_commands hands a dict to
            # the client.run(); the client accepts only k=v.
            # `/routing table add ... fib` has fib as a flag,
            # encode it as fib="" → consumer will read True.
            api_path = cmd.path + "/" + cmd.kind
            out.append(mt_programming.Command(
                path=api_path,
                attrs=dict(cmd.attrs),
            ))
    return out


def _audit() -> Any:
    return get_audit_service()


def _confirmations_present(form) -> tuple[bool, list[str]]:
    missing = [
        name for name in safety_svc.REQUIRED_CONFIRMATIONS
        if not form.get(name)
    ]
    return (not missing), missing


def site_exit_apply(nas_id: int, policy_id: int):
    """VX2.6 — Guarded apply.

    Flow:
      1. Load NAS / policy / exit_node / targets.
      2. Verify the 5 explicit confirmations are present.
      3. Run safety.evaluate.
      4. If blocked → audit `apply_attempted` with result=blocked
         and return without touching the wire.
      5. If allowed → build plan, render forward + rollback,
         persist script version + advance deployment row, then
         hand the structured commands to the EXISTING
         mt_programming.apply_commands path.
      6. Audit succeeded/failed/partial based on the executor's
         ApplyResult.

    The route NEVER calls anything destructive through a
    `[find comment~"..."]` pattern via the live API — only
    structured `add` operations cross the wire. Cleanup is
    operator-driven via the rollback script displayed in the
    preview pane.
    """
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    policy = _load_policy(nas["id"], policy_id)
    if not policy:
        abort(404)

    admin = _current_admin()
    exit_node = nodes_repo.get_by_id(
        _tid(), int(policy["exit_node_id"]))
    targets = targets_repo.list_for_policy(int(policy_id))
    wan_interface_list = (
        request.form.get("wan_interface_list") or "").strip() or None
    backup_ack = bool(request.form.get(
        "override_backup_warning"))
    confs_ok, missing_confs = _confirmations_present(request.form)
    actor = str(getattr(g, "admin_id", None)
                  or (admin.username if admin else "ui"))

    # ── 1. Safety check ────────────────────────────────────
    safety = safety_svc.evaluate(
        tenant_id=_tid(), nas_id=int(nas_id),
        admin=admin, policy=policy, exit_node=exit_node,
        targets=targets,
        wan_interface_list=wan_interface_list,
        backup_override_acknowledged=backup_ack,
    )

    deployments_repo.ensure_for_policy(
        tenant_id=_tid(), policy_id=int(policy_id),
        router_id=int(nas_id))

    if not safety.allowed or not confs_ok:
        # ── 2. Blocked path — audit + render without touching
        # the wire.
        reasons = list(safety.blocking_reasons)
        if not confs_ok:
            reasons.append(
                "missing required confirmations: "
                + ", ".join(missing_confs))
        _audit().record(
            actor=actor,
            action="site_exit.apply_attempted",
            target_type="site_exit_policy",
            target_id=str(policy_id),
            severity="warning",
            result_status="blocked",
            router_id=int(nas_id),
            payload={
                "policy_id":   int(policy_id),
                "target_count": len(targets),
                "fail_mode":   policy.get("fail_mode"),
                "safety":      safety.to_dict(),
                "blocked_reasons": reasons,
                "missing_confirmations": missing_confs,
            },
        )
        deployments_repo.set_status(
            tenant_id=_tid(), policy_id=int(policy_id),
            status=deployments_repo.STATUS_FAILED
            if safety.blocking_reasons
            else deployments_repo.STATUS_PREVIEWED,
        )
        plan = planner.build_plan(
            policy=policy,
            exit_node=exit_node or {"enabled": 0},
            targets=targets,
            wan_interface_list=wan_interface_list,
        )
        return _render_page(
            nas=nas, policy=policy,
            preview_plan=plan,
            forward_script=(renderer.render_forward_script(plan)
                              if plan.can_apply else ""),
            rollback_script=(renderer.render_rollback_script(plan)
                              if plan.can_apply else ""),
            error="تعذّر التطبيق — راجع الأسباب أعلاه.",
            notice=" / ".join(reasons),
        )

    # ── 3. Build + render + persist ───────────────────────
    plan = planner.build_plan(
        policy=policy, exit_node=exit_node,
        targets=targets,
        wan_interface_list=wan_interface_list,
    )
    forward = renderer.render_forward_script(plan)
    rollback = renderer.render_rollback_script(plan)
    script_id = scripts_repo.record(
        policy_id=int(policy_id),
        script_body=forward,
        rollback_script_body=rollback,
        deployment_id=None,
        generated_by_admin_id=(admin.id if admin else None),
        command_count=plan.total_commands,
    )

    # ── 4. Apply through the existing safe executor ───────
    _audit().record(
        actor=actor,
        action="site_exit.apply_attempted",
        target_type="site_exit_policy",
        target_id=str(policy_id),
        severity="info",
        result_status="started",
        router_id=int(nas_id),
        payload={
            "policy_id":   int(policy_id),
            "target_count": len(targets),
            "fail_mode":   policy.get("fail_mode"),
            "script_hash": renderer.script_hash(forward),
            "script_version_id": script_id,
            "safety":      safety.to_dict(),
        },
    )

    commands = _plan_to_commands(plan)
    apply_result = None
    error = ""
    client = _connect_client(nas)
    try:
        client.connect()
        apply_result = mt_programming.apply_commands(
            client, commands)
    except Exception as e:  # noqa: BLE001
        error = f"تعذّر الاتصال بالراوتر: {e}"
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    if apply_result and apply_result.ok:
        deployments_repo.record_apply_success(
            tenant_id=_tid(), policy_id=int(policy_id),
            router_id=int(nas_id),
            script_hash=renderer.script_hash(forward),
        )
        _audit().record(
            actor=actor,
            action="site_exit.apply_succeeded",
            target_type="site_exit_policy",
            target_id=str(policy_id),
            severity="info",
            result_status="success",
            router_id=int(nas_id),
            payload={
                "policy_id":     int(policy_id),
                "summary":       apply_result.summary(),
                "script_hash":   renderer.script_hash(forward),
                "script_version_id": script_id,
            },
        )
        notice = "تم التطبيق بنجاح."
    else:
        deployments_repo.record_apply_failure(
            tenant_id=_tid(), policy_id=int(policy_id),
            router_id=int(nas_id),
            error=(apply_result.error if apply_result else error),
        )
        _audit().record(
            actor=actor,
            action="site_exit.apply_failed",
            target_type="site_exit_policy",
            target_id=str(policy_id),
            severity="critical",
            result_status=(
                apply_result.result_status()
                if apply_result else "failed"
            ),
            router_id=int(nas_id),
            error_message=(apply_result.error
                            if apply_result else error),
            payload={
                "policy_id":     int(policy_id),
                "summary":       (apply_result.summary()
                                  if apply_result else None),
                "script_hash":   renderer.script_hash(forward),
                "script_version_id": script_id,
                "error":         (apply_result.error
                                  if apply_result else error),
            },
        )
        notice = (
            "فشل التطبيق — راجع سجل العمليات والـ recovery plan."
            if apply_result else error
        )

    return _render_page(
        nas=nas, policy=policy,
        preview_plan=plan,
        forward_script=forward,
        rollback_script=rollback,
        notice=notice,
        error=("" if (apply_result and apply_result.ok)
                else (notice or "")),
    )


def site_exit_preview(nas_id: int, policy_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    policy = _load_policy(nas["id"], policy_id)
    if not policy:
        abort(404)

    exit_node = nodes_repo.get_by_id(
        _tid(), int(policy["exit_node_id"]))
    targets = targets_repo.list_for_policy(int(policy_id))
    wan_interface_list = (
        request.form.get("wan_interface_list") or ""
    ).strip() or None

    plan = planner.build_plan(
        policy=policy,
        exit_node=exit_node or {"enabled": 0},
        targets=targets,
        wan_interface_list=wan_interface_list,
    )
    forward = (
        renderer.render_forward_script(plan)
        if plan.can_apply else ""
    )
    rollback = (
        renderer.render_rollback_script(plan)
        if plan.can_apply else ""
    )
    return _render_page(
        nas=nas, policy=policy,
        preview_plan=plan,
        forward_script=forward,
        rollback_script=rollback,
        notice=(
            "أُنشئت معاينة جديدة."
            if plan.can_apply else
            "تعذّر إنشاء معاينة — راجع رسائل المنع."
        ),
    )
