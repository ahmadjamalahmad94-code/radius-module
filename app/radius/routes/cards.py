"""Cards routes — batches + generate + list.

RM-H4: extended generate form with full AdvRadius batch options +
metadata JSON for future fields.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.card_checker import check_card
from ..services.cards import get_cards_service
from ..services.plans import get_plans_service
from .speed_rules_ui import handle_embedded_speed_rule, speed_rules_panel


def register_cards_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/overview", "cards_overview", cards_overview, methods=["GET"])
    bp.add_url_rule("/cards/checker", "cards_checker", cards_checker, methods=["GET"])
    bp.add_url_rule("/cards/batches", "cards_batches", cards_batches, methods=["GET"])
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
    batches = svc.list_batches(limit=500)
    plans = {p.id: p for p in get_plans_service().list(limit=500)}
    summaries = {
        b.id: svc.batch_operational_summary(b.id)
        for b in batches
        if b.id is not None
    }
    return render_template(
        "radius/cards_batches.html",
        batches=batches,
        plans=plans,
        summaries=summaries,
    )


def cards_checker():
    query = (request.args.get("query") or "").strip()
    result = None
    error = ""
    if query:
        if len(query) > 128:
            error = "أدخل رقم بطاقة أو اسم دخول لا يتجاوز 128 حرفًا."
        else:
            result = check_card(_tid(), query)
    return render_template(
        "radius/cards_checker.html",
        query=query,
        result=result,
        error=error,
    )


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
