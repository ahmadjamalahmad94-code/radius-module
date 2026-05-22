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
from ..services import hotspot_templates as ht


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
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer",
        "mt_login_designer", mt_login_designer,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/save",
        "mt_login_designer_save", mt_login_designer_save,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/login-designer/preview",
        "mt_login_designer_preview", mt_login_designer_preview,
        methods=["GET"],
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
            hotspot_designs_repo.save_design(
                _tid(), nas_id,
                template_slug=slug, variables=safe,
            )
            saved = True
            values = safe
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
