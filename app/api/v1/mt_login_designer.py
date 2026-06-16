"""mikrotik login-designer — v1 JSON API (feat/api-first-parity, group 7c).

Mirrors the hotspot login-designer web page (`routes/mt_login_designer.py`,
`/admin/radius/mt/<nas_id>/login-designer`) — the **data + config** surface:
current design (template + variables), the template gallery, the variable
schema, saved presets, and last-deploy summary; plus save + preset
save/apply/delete. Reuses the route's own helpers + `hotspot_designs_repo` +
`hotspot_templates` (no duplicated logic).

Deferred (HTML / binary / live-wire — reuse existing web endpoints):
preview (renders HTML), deploy (+stream, FTP upload to router, gated by
PERM_DEPLOY_LOGIN), download.zip, custom-template upload, font serving.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.db.repos import hotspot_designs_repo
from ...radius.routes import mt_login_designer as web
from ...radius.services import hotspot_templates as ht
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    R = "/mikrotik/<int:nas_id>/login-designer"
    bp.add_url_rule(R, "mt_login_designer_state",
                    require_api_token(state), methods=["GET"])
    bp.add_url_rule(f"{R}/save", "mt_login_designer_save",
                    require_api_token(save), methods=["POST"])
    bp.add_url_rule(f"{R}/presets", "mt_login_designer_preset_save",
                    require_api_token(preset_save), methods=["POST"])
    bp.add_url_rule(f"{R}/presets/<int:preset_id>/apply",
                    "mt_login_designer_preset_apply",
                    require_api_token(preset_apply), methods=["POST"])
    bp.add_url_rule(f"{R}/presets/<int:preset_id>",
                    "mt_login_designer_preset_delete",
                    require_api_token(preset_delete), methods=["DELETE"])


def _variables_schema() -> list[dict]:
    return [{"slug": v.slug, "label_ar": v.label_ar, "default": v.default,
             "kind": v.kind} for v in ht.TEMPLATE_VARIABLES]


def _nas_or_404(nas_id: int):
    nas = web._load_nas(nas_id)
    return nas  # None → caller returns 404


def state(nas_id: int):
    """GET — حالة المصمّم (التصميم الحالي + المعرض + مخطّط المتغيّرات +
    القوالب المحفوظة + آخر نشر)."""
    nas = _nas_or_404(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    return ok({
        "nas": {"id": nas.get("id"), "name": nas.get("name"),
                "address": nas.get("address")},
        "design": web._current_design(nas_id),
        "gallery": web._gallery(nas_id),
        "variables": _variables_schema(),
        "presets": hotspot_designs_repo.list_presets(_tid(), nas_id),
        "last_deploy": web._last_deploy(nas_id),
    })


def _validate_and_collect(body: dict):
    """يبني قيم المتغيّرات من الجسم (variables dict) ويتحقّق منها — يعكس
    منطق صفحة الحفظ. يُعيد (slug, safe_values) أو يرفع ValueError."""
    slug = str(body.get("template_slug") or "").strip()
    incoming = body.get("variables") or {}
    values = {v.slug: str(incoming.get(v.slug, v.default) or "").strip()
              for v in ht.TEMPLATE_VARIABLES}
    if not values.get("STORE_URL"):
        values["STORE_URL"] = web._auto_store_url()
    if not web._known_slug(slug):
        raise ValueError("قالب غير معروف.")
    return slug, ht.validate_vars(values)


def save(nas_id: int):
    """POST /save — حفظ التصميم (template_slug + variables). لا يلمس الراوتر."""
    nas = _nas_or_404(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    try:
        slug, safe = _validate_and_collect(request.get_json(silent=True) or {})
    except ValueError as exc:
        return fail("validation_error", str(exc), status=422)
    hotspot_designs_repo.save_design(_tid(), nas_id, template_slug=slug, variables=safe)
    return ok({"design": {"template_slug": slug, "variables": safe}})


def preset_save(nas_id: int):
    """POST /presets — حفظ القالب الحالي باسم (UPSERT)."""
    nas = _nas_or_404(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:40]
    if not name:
        return fail("validation_error", "اكتب اسمًا للقالب المحفوظ.", status=422)
    try:
        slug, safe = _validate_and_collect(body)
    except ValueError as exc:
        return fail("validation_error", str(exc), status=422)
    hotspot_designs_repo.save_preset(_tid(), nas_id, name=name,
                                     template_slug=slug, variables=safe)
    return ok({"preset": {"name": name, "template_slug": slug},
               "presets": hotspot_designs_repo.list_presets(_tid(), nas_id)}, status=201)


def preset_apply(nas_id: int, preset_id: int):
    """POST /presets/<id>/apply — تطبيق قالب محفوظ (يصبح التصميم الحالي)."""
    nas = _nas_or_404(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    preset = hotspot_designs_repo.get_preset(_tid(), nas_id, int(preset_id))
    if not preset:
        return fail("not_found", "القالب المحفوظ غير موجود.", status=404)
    slug = preset.get("template_slug") or "classic"
    variables = preset.get("variables") or {}
    hotspot_designs_repo.save_design(_tid(), nas_id, template_slug=slug, variables=variables)
    return ok({"design": {"template_slug": slug, "variables": variables}})


def preset_delete(nas_id: int, preset_id: int):
    """DELETE /presets/<id> — حذف قالب محفوظ."""
    nas = _nas_or_404(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود.", status=404)
    if not hotspot_designs_repo.get_preset(_tid(), nas_id, int(preset_id)):
        return fail("not_found", "القالب المحفوظ غير موجود.", status=404)
    hotspot_designs_repo.delete_preset(_tid(), nas_id, int(preset_id))
    return ok({"id": preset_id, "deleted": True})
