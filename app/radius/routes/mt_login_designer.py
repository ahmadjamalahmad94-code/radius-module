"""R2 — Hotspot login-page designer.

Routes:
  GET  /admin/radius/mt/<id>/login-designer        — picker + form
  POST /admin/radius/mt/<id>/login-designer/save   — persist choice
  GET  /admin/radius/mt/<id>/login-designer/preview — iframe target

The preview endpoint returns rendered HTML (with $(...) stripped
via `hotspot_templates.preview`) so a designer iframe can show a
WYSIWYG view without ever calling the router.
"""
from __future__ import annotations

from flask import (
    Blueprint, Response, abort, g, render_template, request,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import hotspot_designs_repo
from ..integration.mikrotik.client import MikrotikClient
from ..services import hotspot_templates as ht
from ..services.audit import get_audit_service
from ..services.nas_connection import resolve_connection_address
from ..services.mt_permissions import (
    PERM_DEPLOY_LOGIN, PERM_MANAGE, PERM_VIEW, requires_perm,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _load_nas(nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, enabled FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    return dict(row) if row else None


def register_mt_login_designer_routes(bp: Blueprint) -> None:
    # S3.2 — VIEW for the designer (read-only preview), MANAGE
    # for save (changes persisted state but doesn't touch the
    # router), DEPLOY_LOGIN for the actual upload.
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer",
        "mt_login_designer",
        requires_perm(PERM_VIEW)(mt_login_designer),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/save",
        "mt_login_designer_save",
        requires_perm(PERM_MANAGE)(mt_login_designer_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/preview",
        "mt_login_designer_preview",
        requires_perm(PERM_VIEW)(mt_login_designer_preview),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/deploy",
        "mt_login_designer_deploy",
        requires_perm(PERM_DEPLOY_LOGIN)(mt_login_designer_deploy),
        methods=["POST"],
    )


def _connect_client(nas_id: int):
    row = db().execute(
        "SELECT address, api_port, api_user, api_password, "
        "       api_use_tls, connection_mode, vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    if not row:
        return None
    return MikrotikClient(
        host=resolve_connection_address(row), port=int(row["api_port"] or 8728),
        username=row["api_user"] or "admin",
        password=row["api_password"] or "",
        use_tls=bool(row["api_use_tls"]),
        verify_tls=True, timeout=15.0,
    )


def _current_design(nas_id: int) -> dict:
    """Either the row from the DB or sensible defaults so the
    GET form has something to render even on first visit."""
    row = hotspot_designs_repo.get_design(_tid(), nas_id)
    if not row:
        return {
            "template_slug": "classic",
            "variables": {v.slug: v.default
                          for v in ht.TEMPLATE_VARIABLES},
        }
    return {
        "template_slug": row.get("template_slug") or "classic",
        "variables": {**{v.slug: v.default
                         for v in ht.TEMPLATE_VARIABLES},
                      **(row.get("variables") or {})},
    }


def mt_login_designer(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    design = _current_design(nas_id)
    return render_template(
        "radius/mt_login_designer.html",
        nas=nas,
        library=ht.LIBRARY,
        variables=ht.TEMPLATE_VARIABLES,
        design=design,
        saved=False,
        error="",
    )


def mt_login_designer_save(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    slug = (request.form.get("template_slug") or "").strip()
    values = {v.slug: (request.form.get(v.slug) or "").strip()
              for v in ht.TEMPLATE_VARIABLES}
    error = ""
    saved = False
    if slug not in ht.TEMPLATES_BY_SLUG:
        error = "قالب غير معروف."
    else:
        try:
            safe = ht.validate_vars(values)
        except ValueError as e:
            error = str(e)
        else:
            # S2.3 — capture pre-save state for the audit row's
            # `before` field, then save, then audit. A failed
            # save still writes an audit entry with result=failed.
            prev = hotspot_designs_repo.get_design(_tid(), nas_id) or {}
            hotspot_designs_repo.save_design(
                _tid(), nas_id,
                template_slug=slug, variables=safe,
            )
            saved = True
            values = safe
            actor = str(getattr(g, "admin_id", None) or "ui")
            get_audit_service().record(
                actor=actor,
                action="mt.login_designer.save",
                target_type="mikrotik_nas",
                target_id=str(nas_id),
                severity="info",
                result_status="success",
                router_id=int(nas_id),
                payload={"template_slug": slug,
                         "variables": safe},
                before={"template_slug":
                        prev.get("template_slug", ""),
                        "variables": prev.get("variables") or {}},
                after={"template_slug": slug,
                       "variables": safe},
            )
    design = {"template_slug": slug if slug else "classic",
              "variables": values}
    return render_template(
        "radius/mt_login_designer.html",
        nas=nas,
        library=ht.LIBRARY,
        variables=ht.TEMPLATE_VARIABLES,
        design=design,
        saved=saved,
        error=error,
    )


def mt_login_designer_deploy(nas_id: int):
    """R3 — Render the saved design + upload login.html to the
    router. Requires the confirm checkbox; refuses on validation
    failure; writes one audit-log entry per attempt."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    confirmed = request.form.get("confirm") == "1"
    error = ""
    deploy_result = None
    design = _current_design(nas_id)

    if not confirmed:
        error = "يجب تأكيد عملية النشر قبل تنفيذها."
    else:
        try:
            safe = ht.validate_vars(design["variables"])
        except ValueError as e:
            error = str(e)
            safe = None
        if safe is not None:
            client = _connect_client(nas_id)
            if client is None:
                error = "الراوتر غير موجود."
            else:
                try:
                    client.connect()
                    deploy_result = ht.deploy_login(
                        client, design["template_slug"], safe,
                    )
                except Exception as e:  # noqa: BLE001
                    error = "تعذّر الاتصال بالراوتر: " + str(e)
                finally:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass

                actor = str(getattr(g, "admin_id", None) or "ui")
                # S2.3 — enrich audit; deploy writes a file on
                # the router so success is `warning`, failure is
                # `critical`.
                if not deploy_result:
                    _result = "failed"
                    _sev = "critical"
                elif deploy_result.ok:
                    _result = "success"
                    _sev = "warning"
                else:
                    _result = "failed"
                    _sev = "critical"
                get_audit_service().record(
                    actor=actor,
                    action="mt.login_designer.deploy",
                    target_type="mikrotik_nas",
                    target_id=str(nas_id),
                    severity=_sev,
                    result_status=_result,
                    router_id=int(nas_id),
                    error_message=(deploy_result.error
                                   if deploy_result else error),
                    payload={
                        "template_slug": design["template_slug"],
                        "path": (deploy_result.path
                                 if deploy_result else ""),
                        "bytes": (deploy_result.bytes
                                  if deploy_result else 0),
                        "ok": bool(deploy_result and deploy_result.ok),
                        "error": (deploy_result.error
                                  if deploy_result else error),
                    },
                )

    return render_template(
        "radius/mt_login_designer.html",
        nas=nas,
        library=ht.LIBRARY,
        variables=ht.TEMPLATE_VARIABLES,
        design=design,
        saved=False,
        error=error,
        deploy_result=deploy_result,
    )


def mt_login_designer_preview(nas_id: int):
    """Return the rendered HTML for the iframe. Reads slug +
    values from query string so the designer JS can re-call this
    with whatever the operator is typing without first hitting the
    DB. If the values fail validation we still return *something*
    — the saved design or, failing that, the classic template
    with defaults — so the iframe never blanks out."""
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    slug = (request.args.get("template_slug") or "").strip()
    if slug not in ht.TEMPLATES_BY_SLUG:
        design = _current_design(nas_id)
        slug = design["template_slug"]
        values = design["variables"]
    else:
        values = {v.slug: (request.args.get(v.slug) or "").strip()
                  for v in ht.TEMPLATE_VARIABLES}
    try:
        html = ht.preview(slug, values)
    except ValueError:
        # Operator typed something invalid in real time — fall
        # back to defaults so the iframe doesn't go blank.
        html = ht.preview(slug, {})
    return Response(html, mimetype="text/html")
