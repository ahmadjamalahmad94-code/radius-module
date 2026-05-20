"""Cards routes — batches + generate + list.

RM-H4: extended generate form with full AdvRadius batch options +
metadata JSON for future fields.
"""
from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..db.repos import operations_repo
from ..services.card_checker import check_card
from ..services.cards import get_cards_service
from ..services.plans import get_plans_service
from .speed_rules_ui import handle_embedded_speed_rule, speed_rules_panel


def register_cards_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/overview", "cards_overview", cards_overview, methods=["GET"])
    bp.add_url_rule("/cards/checker", "cards_checker", cards_checker, methods=["GET", "POST"])
    # ـ R13.A.1: JSON API لـ Card Checker AJAX (foundation للـ UI rebuild) ـ
    bp.add_url_rule("/cards/checker/api/lookup", "cards_checker_api_lookup",
                     cards_checker_api_lookup, methods=["GET"])
    # On-demand password reveal — separate endpoint so the password
    # never lives in the default Checker payload. Role-gated + audited.
    bp.add_url_rule("/cards/checker/api/reveal-password", "cards_checker_api_reveal_password",
                     cards_checker_api_reveal_password, methods=["POST"])
    # ـ R13.A.2: v2 template preview — side-by-side with v1 حتى A.4 ـ
    bp.add_url_rule("/cards/checker/v2", "cards_checker_v2",
                     cards_checker_v2, methods=["GET"])
    bp.add_url_rule("/cards/batches", "cards_batches", cards_batches, methods=["GET"])
    bp.add_url_rule("/cards/batches/bulk", "cards_batches_bulk", cards_batches_bulk, methods=["POST"])
    bp.add_url_rule("/cards/batches/export.csv", "cards_batches_export_csv", cards_batches_export_csv, methods=["GET"])
    bp.add_url_rule("/cards/generate", "cards_generate", cards_generate, methods=["GET", "POST"])
    bp.add_url_rule("/cards", "cards_list", cards_list, methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>/revoke", "cards_revoke", cards_revoke, methods=["POST"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/edit", "cards_batch_edit", cards_batch_edit, methods=["GET", "POST"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/cards", "cards_of_batch", cards_of_batch, methods=["GET"])


def cards_overview():
    """Stats overview للبطاقات — يستخدم helpers الموجودة من RM-H2."""
    from ..services.dashboard_metrics import (
        get_card_counts, get_recent_batches, get_plan_counts
    )
    cards = get_card_counts()
    recent = get_recent_batches(limit=10)
    plans = get_plan_counts()
    return render_template("radius/cards_overview.html",
                            cards=cards, recent=recent, plans=plans)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _form_int(name: str, d: int = 0) -> int:
    try: return int(request.form.get(name) or d)
    except (TypeError, ValueError): return d


def _form_float(name: str, d: float = 0.0) -> float:
    try: return float(request.form.get(name) or d)
    except (TypeError, ValueError): return d


def _form_bool(name: str) -> bool:
    return request.form.get(name, "") in ("1", "on", "true", "yes")


def _form_str(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _query_int(name: str) -> int | None:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _batch_filters_from_request() -> dict:
    return {
        "q": (request.args.get("q") or "").strip()[:120],
        "status": (request.args.get("status") or "").strip()[:40],
        "plan_id": _query_int("plan_id"),
        "manager": (request.args.get("manager") or "").strip()[:80],
        "distributor_id": _query_int("distributor_id"),
    }


def _page_args() -> tuple[int, int]:
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
    return page, per_page


def _collect_batch_options() -> dict:
    """جمع كل خيارات AdvRadius من POST. dict جاهز للتمرير لـ generate_batch."""
    return {
        # توليد
        "username_prefix":           _form_str("username_prefix"),
        "username_suffix":           _form_str("username_suffix"),
        "username_length":           _form_int("username_length", 8),
        "password_length":           _form_int("password_length", 6),
        "password_charset":          _form_str("password_charset") or "digits",
        "password_generation_type":  _form_str("password_generation_type") or "medium",
        "include_batch_number":      _form_bool("include_batch_number"),
        "starts_with_or_ends_with":  _form_str("starts_with_or_ends_with"),
        "prefix_or_suffix_value":    _form_str("prefix_or_suffix_value"),
        "random_generation_enabled": _form_bool("random_generation_enabled") or True,
        # وقت
        "time_value":                _form_int("time_value"),
        "time_unit":                 _form_str("time_unit") or "days",
        "device_count":              max(1, _form_int("device_count", 1)),
        "duration_mode":             _form_str("duration_mode") or "time_unit",
        "validity_after_first_login_days": _form_int("validity_after_first_login_days"),
        "count_by_seconds":          _form_bool("count_by_seconds"),
        "count_from_first_connect":  _form_bool("count_from_first_connect"),
        # السلوك عند انتهاء الكوتا + خيارات
        "on_quota_exhaust":          _form_str("on_quota_exhaust") or "stop",
        "auto_renew_after_first_use":            _form_bool("auto_renew_after_first_use"),
        "transfer_to_student_status_on_connect": _form_bool("transfer_to_student_status_on_connect"),
        "close_user_session_on_disconnect":      _form_bool("close_user_session_on_disconnect"),
        "allow_entry_by_previous_card_palestine":_form_bool("allow_entry_by_previous_card_palestine"),
        "switch_to_mac_on_connect":  _form_bool("switch_to_mac_on_connect"),
        "lock_to_mac_on_close":      _form_bool("lock_to_mac_on_close"),
        "phone_only_login":          _form_bool("phone_only_login"),
        # تجاري (مرجعي) + meta
        "price_per_card":            _form_float("price_per_card"),
        "price_bulk":                _form_float("price_bulk"),
        "total_price":               _form_float("total_price"),
        "total_quota_mb":            _form_int("total_quota_mb"),
        "package_name":              _form_str("package_name"),
        "service_name":              _form_str("service_name"),
        "manager_id":                _form_int("manager_id"),
        "notes":                     _form_str("notes"),
    }


def _batch_form_data(batch) -> dict:
    return {
        "package_name": batch.package_name,
        "plan_id": batch.plan_id,
        "count": batch.count,
        "manager_id": batch.manager_id,
        "price_per_card": batch.price_per_card,
        "price_bulk": batch.price_bulk,
        "total_price": batch.total_price,
        "total_quota_mb": batch.total_quota_mb,
        "service_name": batch.service_name,
        "username_prefix": batch.username_prefix,
        "username_suffix": batch.username_suffix,
        "username_length": batch.username_length,
        "password_length": batch.password_length,
        "password_charset": batch.password_charset,
        "password_generation_type": batch.password_generation_type,
        "include_batch_number": batch.include_batch_number,
        "random_generation_enabled": batch.random_generation_enabled,
        "starts_with_or_ends_with": batch.starts_with_or_ends_with,
        "prefix_or_suffix_value": batch.prefix_or_suffix_value,
        "time_value": batch.time_value,
        "time_unit": batch.time_unit,
        "device_count": batch.device_count,
        "duration_mode": batch.duration_mode,
        "validity_after_first_login_days": batch.validity_after_first_login_days,
        "count_by_seconds": batch.count_by_seconds,
        "count_from_first_connect": batch.count_from_first_connect,
        "on_quota_exhaust": batch.on_quota_exhaust,
        "auto_renew_after_first_use": batch.auto_renew_after_first_use,
        "transfer_to_student_status_on_connect": batch.transfer_to_student_status_on_connect,
        "close_user_session_on_disconnect": batch.close_user_session_on_disconnect,
        "allow_entry_by_previous_card_palestine": batch.allow_entry_by_previous_card_palestine,
        "switch_to_mac_on_connect": batch.switch_to_mac_on_connect,
        "lock_to_mac_on_close": batch.lock_to_mac_on_close,
        "phone_only_login": batch.phone_only_login,
        "status": batch.status,
        "notes": batch.notes,
    }


def cards_batches():
    svc = get_cards_service()
    filters = _batch_filters_from_request()
    page, per_page = _page_args()
    total = svc.count_batch_operations(**filters)
    pages_count = max(1, (total + per_page - 1) // per_page)
    if page > pages_count:
        page = pages_count
    batches = svc.list_batch_operations(
        **filters,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    totals = svc.batch_operations_totals(**filters)
    plans_list = list(get_plans_service().list(limit=500))
    distributors = operations_repo.list_distributors(_tid(), limit=500)
    return render_template(
        "radius/cards_batches.html",
        batches=batches,
        plans=plans_list,
        distributors=distributors,
        totals=totals,
        filters=filters,
        page=page,
        per_page=per_page,
        total=total,
        pages_count=pages_count,
        status_options=[
            ("", "النشطة فقط"),
            ("all", "كل الحزم"),
            ("available", "بها بطاقات جاهزة"),
            ("used", "بها استخدام"),
            ("expired", "بها بطاقات منتهية"),
            ("revoked", "بها بطاقات ملغاة"),
            ("exhausted", "مستهلكة"),
            ("deleted", "مؤرشفة"),
        ],
    )


def _selected_batch_ids() -> list[int]:
    ids: list[int] = []
    for raw in request.form.getlist("batch_ids"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    return sorted(set(ids))


def cards_batches_bulk():
    svc = get_cards_service()
    action = _form_str("bulk_action")
    batch_ids = _selected_batch_ids()
    return_to = request.form.get("return_to") or url_for("radius.cards_batches")
    if not batch_ids:
        flash("اختر حزمة واحدة على الأقل لتنفيذ الإجراء.", "error")
        return redirect(return_to)

    changed = 0
    try:
        if action == "archive":
            reason = _form_str("reason") or "أرشفة من مركز عمليات حزم البطاقات"
            for batch_id in batch_ids:
                if svc.archive_batch(actor=_actor(), batch_id=batch_id, reason=reason):
                    changed += 1
            flash(f"تمت أرشفة {changed} حزمة بدون حذف البطاقات.", "warning")
        elif action == "restore":
            for batch_id in batch_ids:
                if svc.restore_batch(actor=_actor(), batch_id=batch_id):
                    changed += 1
            flash(f"تمت استعادة {changed} حزمة مؤرشفة.", "success")
        elif action == "refresh":
            flash("تم تحديث إحصاءات الحزم من البيانات الحالية.", "success")
        else:
            flash("إجراء جماعي غير معروف.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(return_to)


def _csv_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def cards_batches_export_csv():
    svc = get_cards_service()
    filters = _batch_filters_from_request()
    rows = svc.list_batch_operations(**filters, limit=5000, offset=0)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "رقم الحزمة",
        "اسم الحزمة",
        "الباقة",
        "الحالة",
        "العدد",
        "المولد",
        "الجاهز",
        "النشط",
        "المنتهي",
        "الملغى",
        "المتبقي",
        "جلسات",
        "MAC مختلف",
        "قواعد سرعة",
        "سعر البطاقة",
        "قيمة الحزمة",
        "المدير",
        "الموزع",
        "تاريخ الإنشاء",
    ])
    for item in rows:
        unit_price = float(item.get("estimated_unit_price") or 0)
        configured_value = float(item.get("total_price") or 0)
        if configured_value <= 0:
            configured_value = unit_price * int(item.get("generated") or 0)
        writer.writerow([
            _csv_text(item.get("batch_code")),
            _csv_text(item.get("package_name")),
            _csv_text(item.get("plan_name")),
            _csv_text(item.get("operational_status")),
            _csv_text(item.get("count")),
            _csv_text(item.get("generated")),
            _csv_text(item.get("available_count")),
            _csv_text(item.get("active_count")),
            _csv_text(item.get("expired_count")),
            _csv_text(item.get("revoked_count")),
            _csv_text(item.get("remaining_count")),
            _csv_text(item.get("sessions_count")),
            _csv_text(item.get("unique_macs")),
            _csv_text(item.get("active_speed_rules")),
            f"{unit_price:.2f}",
            f"{configured_value:.2f}",
            _csv_text(item.get("created_by") or item.get("manager_id")),
            _csv_text(item.get("distributor_display_name") or item.get("distributor_name")),
            _csv_text(item.get("created_at")),
        ])
    payload = "\ufeff" + out.getvalue()
    return Response(
        payload,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=card-batches.csv"},
    )


def _checker_redirect(query: str):
    return redirect(url_for("radius.cards_checker", query=(query or "").strip()))


def _handle_card_operation():
    svc = get_cards_service()
    # Accept both 'op' (new template forms) and '_card_action' (older
    # forms / curl callers). 'op' wins when both are set.
    action = _form_str("op") or _form_str("_card_action")
    card_id = _form_int("card_id")
    username = _form_str("username")
    query = _form_str("query") or username
    try:
        if action == "lock_mac":
            svc.lock_card_mac(actor=_actor(), card_id=card_id, mac=_form_str("mac"))
            flash("تم تثبيت عنوان MAC على البطاقة.", "success")
        elif action == "unlock_mac":
            svc.unlock_card_mac(actor=_actor(), card_id=card_id)
            flash("تم إلغاء تثبيت MAC عن البطاقة.", "success")
        elif action == "disconnect":
            # session_ids = comma-separated acctsessionids (from the
            # per-device picker). Empty / missing → kick all.
            ids_raw = _form_str("session_ids")
            ids = [s.strip() for s in ids_raw.split(",") if s.strip()] if ids_raw else None
            svc.disconnect_card(
                actor=_actor(),
                username=username,
                session_id=_form_str("session_id"),
                session_ids=ids,
            )
            if ids:
                flash(
                    f"تم إرسال أمر قطع لـ {len(ids)} جلسة"
                    + (" مختارة." if len(ids) > 1 else "."),
                    "warning",
                )
            else:
                flash("تم إرسال أمر قطع لكل الجلسات النشطة.", "warning")
        elif action == "reset_usage":
            svc.reset_card_usage(actor=_actor(), card_id=card_id)
            flash("تم تصفير استخدام البطاقة ووقت بدايتها.", "success")
        elif action == "disable":
            res = svc.disable_card(
                actor=_actor(), card_id=card_id, reason=_form_str("reason"),
            )
            frozen = int((res or {}).get("frozen_remaining_seconds") or 0)
            # disable_card now also broadcasts CoA-Disconnect so devices
            # currently online can't keep using the network after the
            # admin froze the card. Reflect both actions in the flash.
            suffix = " وتم قطع كل الجلسات النشطة."
            if frozen > 0:
                h, m = divmod(frozen // 60, 60)
                flash(
                    f"تم تعطيل البطاقة وتجميد الوقت المتبقي ({h} ساعة و {m} دقيقة). "
                    "سيعود نفس الوقت عند إعادة التفعيل." + suffix,
                    "warning",
                )
            else:
                flash("تم تعطيل البطاقة." + suffix, "warning")
        elif action == "enable":
            res = svc.enable_card(actor=_actor(), card_id=card_id)
            restored = int((res or {}).get("restored_seconds") or 0)
            if restored > 0:
                h, m = divmod(restored // 60, 60)
                flash(
                    f"تم تفعيل البطاقة. تمت استعادة الوقت المجمَّد ({h} ساعة و {m} دقيقة).",
                    "success",
                )
            else:
                flash("تم تفعيل البطاقة.", "success")
        elif action == "soft_delete":
            # Default 'حذف' from the Card Checker — moves to recycle bin,
            # NOT permanent. The /admin/radius/recycle-bin screen can
            # restore or finally purge it.
            svc.soft_delete_card(
                actor=_actor(), card_id=card_id, reason=_form_str("reason"),
            )
            flash("تم نقل البطاقة إلى سلة المحذوفات. يمكنك استعادتها من سلة المحذوفات.", "success")
            query = ""
        elif action == "delete_permanent":
            # Hard-delete path retained for the recycle bin screen.
            if _form_str("confirm_delete") != "DELETE":
                flash("للحذف النهائي اكتب DELETE في خانة التأكيد.", "error")
            else:
                svc.delete_card_permanently(actor=_actor(), card_id=card_id)
                flash("تم حذف البطاقة نهائيًا. لا يظهر هذا الخيار في التشغيل اليومي إلا بحذر.", "warning")
                query = ""
        elif action == "set_time":
            # Per-card time adjustment (shift expire_at by ±N seconds).
            # Form fields:
            #   time_amount  → integer > 0
            #   time_unit    → "minutes" | "hours" | "days"
            #   time_op      → "add" | "subtract"
            unit_map = {"minutes": 60, "hours": 3600, "days": 86400}
            amount = _form_int("time_amount")
            unit   = (_form_str("time_unit") or "").strip().lower()
            op     = (_form_str("time_op")   or "").strip().lower()
            if amount <= 0 or unit not in unit_map or op not in ("add", "subtract"):
                flash("بيانات التعديل غير مكتملة. حدّد المدّة والوحدة والعملية.", "error")
            else:
                delta = amount * unit_map[unit] * (-1 if op == "subtract" else 1)
                try:
                    result = svc.adjust_card_time(
                        actor=_actor(), card_id=card_id,
                        delta_seconds=delta, username=username,
                    )
                except RadiusError as e:
                    flash(e.message, "error")
                else:
                    # Build a friendly Arabic summary
                    op_label   = "تمت إضافة" if op == "add" else "تم خصم"
                    unit_label = {"minutes": "دقيقة", "hours": "ساعة", "days": "يوم"}[unit]
                    rem_h, rem_m = divmod(int(result["remaining_seconds"]) // 60, 60)
                    coa = result.get("coa_result")
                    coa_note = ""
                    if coa is not None:
                        if getattr(coa, "ok", False):
                            coa_note = " — وصل التحديث للـ MikroTik (CoA-ACK)."
                        elif getattr(coa, "code_name", "") == "no_active_session":
                            coa_note = " — لا جلسة نشطة الآن، سيُطبَّق في الجلسة التالية."
                        else:
                            coa_note = f" — لم يصل التحديث الفوري للـ MikroTik ({getattr(coa,'code_name','?')})."
                    flash(
                        f"{op_label} {amount} {unit_label} من وقت البطاقة. "
                        f"المتبقي الآن: {rem_h} ساعة و {rem_m} دقيقة.{coa_note}",
                        "success",
                    )
        elif action == "set_speed":
            # Per-card speed override (migration 024). Persists to
            # cards.card_speed_*_kbps, re-syncs the FreeRADIUS radreply
            # row via freeradius_translator, and best-effort pushes a
            # CoA-Request with the new Mikrotik-Rate-Limit so any live
            # session picks the new rate without disconnect.
            #
            # Pass down=0 AND up=0 to CLEAR the override (revert to plan
            # default). The UI doesn't expose clearing yet but the
            # service supports it for API/CLI callers.
            down = _form_int("speed_down_kbps")
            up   = _form_int("speed_up_kbps")
            if down < 0 or up < 0:
                flash("قيم السرعة يجب ألا تكون سالبة.", "error")
            else:
                try:
                    result = svc.set_card_speed(
                        actor=_actor(), card_id=card_id,
                        down_kbps=down, up_kbps=up, username=username,
                    )
                except RadiusError as e:
                    flash(e.message, "error")
                else:
                    coa = result.get("coa_result")
                    coa_note = ""
                    if coa is not None:
                        if getattr(coa, "ok", False):
                            coa_note = " — وصل التحديث للـ MikroTik (CoA-ACK)."
                        elif getattr(coa, "code_name", "") == "no_active_session":
                            coa_note = " — لا جلسة نشطة، سيُطبَّق في الجلسة التالية."
                        else:
                            coa_note = f" — لم يصل التحديث الفوري للـ MikroTik ({getattr(coa,'code_name','?')})."
                    if down == 0 and up == 0:
                        flash(
                            f"تم إلغاء تخصيص السرعة على البطاقة — ترجع لسرعة الحزمة.{coa_note}",
                            "success",
                        )
                    else:
                        flash(
                            f"تم تعيين سرعة البطاقة: تنزيل {down} kbps / رفع {up} kbps.{coa_note}",
                            "success",
                        )
        else:
            flash("إجراء غير معروف.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return _checker_redirect(query)


def cards_checker():
    """R13.A.4: GET now renders the v2 operations-room template by default.

    The /v2 preview URL stays as an alias (same template), so any
    bookmarked /v2 link keeps working. POST still routes through the
    existing _handle_card_operation() handler — all card operations are
    unchanged.
    """
    if request.method == "POST":
        return _handle_card_operation()

    query = (request.args.get("query") or request.args.get("q") or "").strip()
    result = None
    error = ""
    if query:
        if len(query) > 128:
            error = "أدخل رقم بطاقة أو اسم دخول لا يتجاوز 128 حرفًا."
        else:
            result = check_card(_tid(), query)
    return render_template(
        "radius/cards_checker_v2.html",
        query=query,
        result=result,
        error=error,
    )


# ─────────────────────────────────────────────────────────────────────────────
# R13.A.1 — Card Checker JSON API
# ─────────────────────────────────────────────────────────────────────────────
#
# الـ AJAX foundation للـ UI rebuild (R13.A). الـ HTML page الحالي يَستخدم
# render_template مع full reload. الـ rebuild سيَستخدم هذا الـ endpoint
# للـ live lookup عبر fetch().
#
# لا يَكسر القديم — endpoint منفصل تمامًا. يُعيد نفس البيانات التي يَبنيها
# `check_card` كـ JSON. الـ schema:
#
#   200 OK   { "ok": true,  "query": "...", "result": { ... full payload ... } }
#   400 BAD  { "ok": false, "error": "human-readable error", "code": "..." }
#
# الـ codes الموحَّدة:
#   empty_query   — q فاضي
#   query_too_long — q > 128 حرف
#
# نُعيد دائمًا `ok` boolean و `query` echoes حتى الـ frontend يُسهّل الـ
# state matching. لا نَستخدم HTTP 404 لـ "card not found" — هذا حالة
# طبيعية (`result.exists = false`)، لا خطأ.
# ─────────────────────────────────────────────────────────────────────────────
def cards_checker_v2():
    """R13.A.2: GET /admin/radius/cards/checker/v2

    Preview of the new operations-room layout. Side-by-side with v1
    until A.4 swaps the default. Renders the same `result` payload as
    v1 — no POST handling here; the v2 template's operation forms
    submit back to the v1 route (proven path).
    """
    query = (request.args.get("query") or request.args.get("q") or "").strip()
    result = None
    error = ""
    if query:
        if len(query) > 128:
            error = "أدخل رقم بطاقة أو اسم دخول لا يتجاوز 128 حرفًا."
        else:
            result = check_card(_tid(), query)
    return render_template(
        "radius/cards_checker_v2.html",
        query=query,
        result=result,
        error=error,
    )


def cards_checker_api_lookup():
    """GET /admin/radius/cards/checker/api/lookup?q=<query>

    يُرجع JSON مكافئ لـ check_card() — جاهز للـ AJAX frontend.
    """
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    if not query:
        return jsonify({
            "ok": False,
            "error": "أدخل رقم بطاقة أو اسم دخول.",
            "code": "empty_query",
        }), 400
    if len(query) > 128:
        return jsonify({
            "ok": False,
            "error": "أدخل رقم بطاقة أو اسم دخول لا يتجاوز 128 حرفًا.",
            "code": "query_too_long",
        }), 400
    result = check_card(_tid(), query)
    return jsonify({
        "ok": True,
        "query": query,
        "result": result,
    })


def cards_checker_api_reveal_password():
    """POST /admin/radius/cards/checker/api/reveal-password

    On-demand password reveal for the Card Checker hero.

    Why a SEPARATE endpoint instead of including the password in the
    default Checker payload:
      • The default payload is rendered into the page HTML on every
        Checker request and may be cached, logged in browser dev tools,
        sniffed via the SSE / network tab, or copied accidentally.
      • By keeping the password OUT of the default response and
        requiring an explicit POST to retrieve it, we get:
          – Per-reveal audit row (action: 'card.password_reveal')
            with actor + card_id + tenant_id + timestamp.
          – Role gate: only admins reaching this route can call it
            (the blueprint already enforces login_required on the
            whole radius module).
          – Less leakage: a casual screenshot of the Checker won't
            include the value.

    Body: form field `card_id` (int)
    Returns: 200 {ok:true, password:'...'} or 4xx {ok:false, error:...}
    """
    from ..db.connection import db as _db_conn
    from ..services.audit import get_audit_service
    card_id = _form_int("card_id")
    if not card_id:
        return jsonify({"ok": False, "error": "card_id مطلوب"}), 400
    tenant_id = _tid()
    row = _db_conn().execute(
        "SELECT username, password FROM cards "
        "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (tenant_id, card_id),
    ).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "البطاقة غير موجودة"}), 404
    username = row["username"] if isinstance(row, dict) else row[0]
    password = row["password"]  if isinstance(row, dict) else row[1]
    if not password:
        return jsonify({"ok": False, "error": "هذه البطاقة بدون كلمة مرور"}), 404
    # Audit the reveal — operator + card + when.
    try:
        get_audit_service().record(
            actor=_actor(), action="card.password_reveal",
            target_type="card", target_id=str(card_id),
            payload={"username": username},
        )
    except Exception:  # noqa: BLE001 — never block the reveal on audit issues
        pass
    return jsonify({
        "ok": True,
        "card_id": card_id,
        "password": password,
    })


def cards_generate():
    if request.method == "POST":
        try:
            plan_id = _form_int("plan_id")
            count = _form_int("count")
            opts = _collect_batch_options()
            batch, cards = get_cards_service().generate_batch(
                actor=_actor(), plan_id=plan_id, count=count, **opts,
            )
            flash(f"تم إنشاء دفعة «{batch.batch_code}» — {len(cards)} بطاقة.", "success")
            return redirect(url_for("radius.cards_of_batch", batch_id=batch.id))
        except (TypeError, ValueError) as e:
            flash(f"قيم غير صحيحة: {e}", "error")
        except RadiusError as e:
            flash(e.message, "error")
    plans = list(get_plans_service().list(limit=500))
    return render_template("radius/cards_generate.html", plans=plans, form=request.form)


def cards_batch_edit(batch_id: int):
    svc = get_cards_service()
    batch = next((b for b in svc.list_batches(limit=1000) if b.id == batch_id), None)
    if not batch:
        flash("دفعة الكروت غير موجودة.", "error")
        return redirect(url_for("radius.cards_batches"))
    if request.method == "POST":
        if request.form.get("_speed_rule_action"):
            try:
                handle_embedded_speed_rule(
                    tenant_id=_tid(),
                    actor=_actor(),
                    form=request.form,
                    target_type="card_batch",
                    plan_id=batch.plan_id,
                    card_batch_id=batch_id,
                )
                flash("تم تنفيذ إجراء قواعد السرعة لهذه الحزمة.", "success")
            except RadiusError as e:
                flash(e.message, "error")
            return redirect(url_for("radius.cards_batch_edit", batch_id=batch_id))
        try:
            data = _collect_batch_options()
            data.update({
                "plan_id": _form_int("plan_id"),
                "count": _form_int("count", batch.count),
                "status": _form_str("status") or batch.status,
            })
            updated = svc.update_batch(actor=_actor(), batch_id=batch_id, data=data)
            flash("تم حفظ تعديلات دفعة الكروت.", "success")
            return redirect(url_for("radius.cards_of_batch", batch_id=updated.id))
        except (TypeError, ValueError) as e:
            flash(f"قيم غير صحيحة: {e}", "error")
        except RadiusError as e:
            flash(e.message, "error")
    plans = list(get_plans_service().list(limit=500))
    form = request.form if request.method == "POST" else _batch_form_data(batch)
    return render_template(
        "radius/cards_batch_edit.html",
        batch=batch,
        plans=plans,
        form=form,
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="card_batch",
            plan_id=batch.plan_id,
            card_batch_id=batch_id,
            return_to=request.path,
            title="قواعد سرعة هذه الحزمة",
            help_text="أضف قواعد سرعة لكل بطاقات هذه الحزمة. عند وجود سرعة للعرض وسرعة لهذه الحزمة، تُطبّق سرعة الحزمة أولًا.",
        ),
    )


def cards_list():
    """R10.4: pagination + search + batch + revoked filters.

    Pre-R10.4 رفعنا 1000 كرت دفعة واحدة. مع 2020+ كرت، الصفحة كانت
    بطيئة وصعبة التصفّح. الآن:
      - `?q=...`    LIKE على username (يدعم البحث الجزئي بالأرقام).
      - `?batch_id=X` فلترة على دفعة محدّدة.
      - `?used=0|1` ، `?revoked=0|1` — booleans منفصلة.
      - `?page=N`   صفحة (1-based). `?per_page` يقبل 25/50/100 (سقف 100).

    نُمرّر `total / page / per_page / pages_count / q / batch_id / revoked
    / used / preserve_params` للقالب حتى يبني روابط pagination بدون
    إعادة بناء query string.
    """
    used = request.args.get("used")
    used_b = True if used == "1" else (False if used == "0" else None)

    revoked = request.args.get("revoked")
    revoked_b = True if revoked == "1" else (False if revoked == "0" else None)

    raw_batch = (request.args.get("batch_id") or "").strip()
    try:
        batch_id = int(raw_batch) if raw_batch else None
    except ValueError:
        batch_id = None

    q = (request.args.get("q") or "").strip()

    # per_page: clamp إلى whitelist {25, 50, 100} لمنع abuse + استقرار CSS.
    try:
        per_page = int(request.args.get("per_page") or "50")
    except ValueError:
        per_page = 50
    if per_page not in (25, 50, 100):
        per_page = 50

    try:
        page = max(1, int(request.args.get("page") or "1"))
    except ValueError:
        page = 1

    svc = get_cards_service()
    total = svc.count_cards(used=used_b, revoked=revoked_b,
                             batch_id=batch_id, search=q or None)
    pages_count = max(1, (total + per_page - 1) // per_page)
    if page > pages_count:
        page = pages_count
    offset = (page - 1) * per_page

    items = svc.list_cards(used=used_b, revoked=revoked_b,
                           batch_id=batch_id, search=q or None,
                           limit=per_page, offset=offset)
    plans = {p.id: p for p in get_plans_service().list(limit=500)}
    batches = {b.id: b for b in svc.list_batches(limit=500)}

    # preserve_params: نمرّرها للقالب فيستخدمها في hidden inputs + روابط
    # pagination — يحافظ على البحث/الفلاتر عبر تغيير الصفحة.
    preserve = {}
    if used is not None: preserve["used"] = used
    if revoked is not None: preserve["revoked"] = revoked
    if batch_id is not None: preserve["batch_id"] = batch_id
    if q: preserve["q"] = q
    preserve["per_page"] = per_page

    return render_template(
        "radius/cards_list.html",
        items=items, plans=plans, batches=batches,
        used=used, revoked=revoked, batch_id=batch_id, q=q,
        page=page, per_page=per_page, total=total,
        pages_count=pages_count, preserve=preserve,
    )


def cards_of_batch(batch_id: int):
    svc = get_cards_service()
    items = svc.list_cards(batch_id=batch_id, limit=2000)
    batch = next((b for b in svc.list_batches(limit=500) if b.id == batch_id), None)
    plan = None
    if batch:
        plan = next((p for p in get_plans_service().list(limit=500) if p.id == batch.plan_id), None)
    return render_template("radius/cards_of_batch.html", items=items, batch=batch, plan=plan)


def cards_revoke(card_id: int):
    try:
        get_cards_service().revoke_card(actor=_actor(), card_id=card_id)
        flash("تم إلغاء البطاقة.", "warning")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(request.referrer or url_for("radius.cards_list"))
