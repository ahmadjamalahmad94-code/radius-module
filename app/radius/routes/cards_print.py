"""Print-Only Cards routes — a separate operator-facing service.

Backed by ``card_batches.print_only = 1``. Cards never reach
FreeRADIUS — they exist purely so the operator can render printable
labels from real-looking credentials supplied by the upstream
provider.

URL tree:
  GET  /cards/print                    → list batches (table + chips)
  GET  /cards/print/new                → import form
  POST /cards/print/new                → handle import
  POST /cards/print/new/preview        → smart-import preview JSON
  GET  /cards/print/<batch_id>         → cards inside a batch + print modal
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, g, jsonify, make_response, redirect,
    render_template, request, session as flask_session, url_for,
)

from ..core.errors import RadiusError, RadiusValidationError
from ..core.tenant import DEFAULT_TENANT_ID
from ..services import cards_import_engine
from ..services.cards import get_cards_service
from ..services.operations import get_operations_service


# ─── Registration ────────────────────────────────────────────────

def register_cards_print_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/cards/print",
        "cards_print_list",
        cards_print_list, methods=["GET"],
    )
    bp.add_url_rule(
        "/cards/print/new",
        "cards_print_new",
        cards_print_new, methods=["GET", "POST"],
    )
    bp.add_url_rule(
        "/cards/print/new/preview",
        "cards_print_new_preview",
        cards_print_new_preview, methods=["POST"],
    )
    bp.add_url_rule(
        "/cards/print/quick",
        "cards_print_quick",
        cards_print_quick, methods=["GET"],
    )
    bp.add_url_rule(
        "/cards/print/<int:batch_id>",
        "cards_print_batch",
        cards_print_batch, methods=["GET"],
    )
    bp.add_url_rule(
        "/cards/print/<int:batch_id>/delete",
        "cards_print_batch_delete",
        cards_print_batch_delete, methods=["POST"],
    )


# ─── «منشئ كروت PDF» السريع (شاشة واحدة) ────────────────────────

def cards_print_quick():
    """الوضع البسيط (طلب المالك): كل شيء بشاشة واحدة — صورة الكارت،
    إظهار اليوزر/الباس وحجومهما، عدد الكروت عرضًا/طولًا والفراغات،
    معاينة حية، وأزرار حفظ/تحميل — بدل معرض + غرفة تصميم + مركز تصدير.

    واجهة مدمجة فوق نفس المحرك: الحفظ عبر print_templates_create/update
    (return_to=quick)، المعاينة عبر designer-svg، والتحميل عبر مهام
    التصدير القائمة. آخر إعدادات التصدير تُعبّأ مسبقًا تلقائيًّا."""
    from .print_templates import get_last_print_settings

    ops = get_operations_service()
    templates = ops.list_print_templates(tenant_id=_tid(), limit=500)
    default_id = ops.get_default_print_template_id(tenant_id=_tid())
    try:
        selected_id = int(request.args.get("template_id") or 0)
    except (TypeError, ValueError):
        selected_id = 0
    tpl = None
    if selected_id:
        tpl = next((dict(t) for t in templates
                    if int(t.get("id") or 0) == selected_id), None)
    if tpl is None and selected_id != 0 and default_id:
        tpl = next((dict(t) for t in templates
                    if int(t.get("id") or 0) == int(default_id)), None)
    if tpl is None and request.args.get("template_id") is None:
        # بلا وسيط إطلاقًا: القالب الافتراضي إن وُجد، وإلا الأحدث.
        if default_id:
            tpl = next((dict(t) for t in templates
                        if int(t.get("id") or 0) == int(default_id)), None)
        if tpl is None and templates:
            tpl = dict(templates[0])
    fl = (tpl or {}).get("layout_json") or {}
    if not isinstance(fl, dict):
        fl = {}
    # الحزم: العادية + «طباعة فقط» معًا — المنشئ يخدم الاثنين.
    svc = get_cards_service()
    try:
        batches = list(svc.list_batch_operations(limit=200, offset=0))
    except Exception:  # noqa: BLE001
        batches = []
    try:
        print_only = list(svc.list_print_only_batches(limit=200))
    except Exception:  # noqa: BLE001
        print_only = []
    try:
        selected_batch = int(request.args.get("batch_id") or 0)
    except (TypeError, ValueError):
        selected_batch = 0
    return render_template(
        "radius/cards_print_quick.html",
        templates=templates,
        tpl=tpl, fl=fl,
        batches=batches,
        print_only_batches=print_only,
        selected_batch=selected_batch,
        lps=get_last_print_settings(),
        auto_export=(request.args.get("auto_export") == "1"),
        # وضع «النافذة العائمة»: iframe بلا شريط اللوحة (قاعدة embed).
        embed=(request.args.get("embed") == "1"),
    )


# ─── Helpers ─────────────────────────────────────────────────────

def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> str:
    return (flask_session.get("admin_name")
            or flask_session.get("admin_user")
            or "anonymous")


def _form_float(name: str, default: float = 0.0) -> float:
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# 12 MB matches the cap on the regular smart-import preview.
_IMPORT_MAX_BYTES = 12 * 1024 * 1024


# ─── List ────────────────────────────────────────────────────────

def cards_print_list():
    svc = get_cards_service()
    batches = svc.list_print_only_batches(limit=200)
    total = svc.count_print_only_batches()
    # Print modal needs the same template catalog the regular
    # batches page uses — we render the modal on every list view
    # so the per-chip print button can fire it without a round-trip.
    operations_service = get_operations_service()
    print_templates = operations_service.list_print_templates(
        tenant_id=_tid(), limit=500,
    )
    default_print_template_id = operations_service.get_default_print_template_id(
        tenant_id=_tid(),
    )
    return render_template(
        "radius/cards_print_list.html",
        batches=batches,
        total=total,
        print_templates=print_templates,
        default_print_template_id=default_print_template_id,
    )


# ─── Import form + handler ──────────────────────────────────────

def cards_print_new():
    if request.method == "GET":
        return _render_new_form()

    package_name = (request.form.get("package_name") or "").strip()
    price_per_card = _form_float("price_per_card")
    price_bulk = _form_float("price_bulk")
    csv_text = request.form.get("csv_text") or ""
    notes = (request.form.get("notes") or "").strip()

    if not package_name:
        flash("اسم الحزمة مطلوب.", "error")
        return _render_new_form(), 422

    # Re-use the engine's text parser — handles paste-direct flows
    # plus the smart-import preview that the JS pre-fills.
    parsed = cards_import_engine.parse_text(csv_text)
    rows = [
        {"username": c.username, "password": c.password}
        for c in parsed.cards
    ]
    if not rows:
        flash(
            "لم يُعثر على أي بطاقة قابلة للاستيراد — راجع النص أو ارفع الملف مرة أخرى.",
            "error",
        )
        return _render_new_form(), 422

    try:
        result = get_cards_service().import_print_only_batch(
            actor=_actor(),
            package_name=package_name,
            cards=rows,
            price_per_card=price_per_card,
            price_bulk=price_bulk,
            notes=notes,
        )
    except RadiusValidationError as exc:
        flash(exc.message, "error")
        return _render_new_form(), 422
    except RadiusError as exc:
        flash(exc.message, "error")
        return _render_new_form(), 500

    batch = result["batch"]
    skipped = result["skipped_count"]
    skipped_label = f" تم تخطي {skipped} مكرر/غير صالح." if skipped else ""
    flash(
        f"تم استيراد {result['inserted_count']} بطاقة طباعة "
        f"داخل الحزمة «{batch.package_name or batch.batch_code}».{skipped_label}",
        "success",
    )
    return redirect(url_for("radius.cards_print_batch", batch_id=batch.id))


def cards_print_new_preview():
    """JSON preview endpoint mirroring /cards/batches/import/preview
    — separate URL so it carries its own CSRF token + audit trail.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "اختر ملفًا قبل الرفع."}), 400

    raw = upload.read(_IMPORT_MAX_BYTES + 1)
    if not raw:
        return jsonify({"ok": False, "error": "الملف فارغ أو غير قابل للقراءة."}), 400
    if len(raw) > _IMPORT_MAX_BYTES:
        return jsonify({
            "ok": False,
            "error": "حجم الملف يتجاوز 12MB — قسّمه إلى دفعات أصغر.",
        }), 413

    result = cards_import_engine.parse(raw, upload.filename or "")
    payload = {
        "ok": bool(result.cards) or not result.warnings,
        "engine_version": cards_import_engine.ENGINE_VERSION,
        "engine_build_note": cards_import_engine.ENGINE_BUILD_NOTE,
        "fmt": result.fmt,
        "count": len(result.cards),
        "strategy": result.detected.strategy,
        "username_index": result.detected.username_index,
        "password_index": result.detected.password_index,
        "header_row_present": result.detected.header_row_present,
        "rows_seen": result.rows_seen,
        "rows_skipped": result.rows_skipped,
        "sheet_names": result.sheet_names,
        "warnings": result.warnings,
        "csv_text": cards_import_engine.cards_to_csv(result.cards),
        "preview": [
            {"username": c.username, "password": c.password}
            for c in result.cards[:5]
        ],
    }
    status = 200 if result.cards else 422
    return jsonify(payload), status


def _render_new_form():
    """Render the import form. Uses no-store so the rendered CSRF
    token stays in lock-step with the session, the same way the
    regular import page does."""
    html = render_template(
        "radius/cards_print_new.html",
        engine_version=cards_import_engine.ENGINE_VERSION,
        engine_build_note=cards_import_engine.ENGINE_BUILD_NOTE,
        form=request.form,
    )
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ─── Batch detail + print modal ────────────────────────────────

def cards_print_batch_delete(batch_id: int):
    """Soft-delete a print-only batch. Confirmed on the client; the
    server also checks that the batch is actually print_only=1 to
    refuse cross-section misuse.
    """
    ok = get_cards_service().delete_print_only_batch(
        actor=_actor(), batch_id=batch_id,
    )
    if ok:
        flash("تم حذف حزمة الطباعة.", "success")
    else:
        flash("الحزمة غير موجودة أو غير قابلة للحذف.", "error")
    return redirect(url_for("radius.cards_print_list"))


def cards_print_batch(batch_id: int):
    svc = get_cards_service()
    batch = svc.get_print_only_batch(batch_id)
    if not batch:
        abort(404)

    # Pagination — same option ladder the rest of the admin uses
    # (10/20/50/100). The page query param caps to the actual
    # page count so a bookmark like ?page=99 doesn't 404.
    try:
        per_page = int(request.args.get("per_page") or "20")
    except ValueError:
        per_page = 20
    if per_page not in (10, 20, 50, 100):
        per_page = 20
    try:
        page = max(1, int(request.args.get("page") or "1"))
    except ValueError:
        page = 1

    total = svc.count_print_only_cards(batch_id)
    pages_count = max(1, (total + per_page - 1) // per_page)
    if page > pages_count:
        page = pages_count
    offset = (page - 1) * per_page
    cards = svc.list_print_only_cards(
        batch_id, limit=per_page, offset=offset,
    )

    # Reuse the same print-template catalog the regular cards-of-batch
    # screen uses, so an operator can pick a familiar layout.
    operations_service = get_operations_service()
    print_templates = operations_service.list_print_templates(
        tenant_id=_tid(), limit=500,
    )
    default_print_template_id = operations_service.get_default_print_template_id(
        tenant_id=_tid(),
    )

    return render_template(
        "radius/cards_print_batch.html",
        batch=batch,
        cards=cards,
        total_cards=total,
        page=page,
        per_page=per_page,
        pages_count=pages_count,
        print_templates=print_templates,
        default_print_template_id=default_print_template_id,
    )
