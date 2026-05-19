"""Cards routes — batches + generate + list.

RM-H4: extended generate form with full AdvRadius batch options +
metadata JSON for future fields.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..services.cards import get_cards_service
from ..services.plans import get_plans_service


def register_cards_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/batches", "cards_batches", cards_batches, methods=["GET"])
    bp.add_url_rule("/cards/generate", "cards_generate", cards_generate, methods=["GET", "POST"])
    bp.add_url_rule("/cards", "cards_list", cards_list, methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>/revoke", "cards_revoke", cards_revoke, methods=["POST"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/cards", "cards_of_batch", cards_of_batch, methods=["GET"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


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


def cards_batches():
    svc = get_cards_service()
    batches = svc.list_batches(limit=500)
    plans = {p.id: p for p in get_plans_service().list(limit=500)}
    return render_template("radius/cards_batches.html", batches=batches, plans=plans)


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


def cards_list():
    used = request.args.get("used")
    used_b = True if used == "1" else (False if used == "0" else None)
    items = get_cards_service().list_cards(used=used_b, limit=1000)
    plans = {p.id: p for p in get_plans_service().list(limit=500)}
    batches = {b.id: b for b in get_cards_service().list_batches(limit=500)}
    return render_template("radius/cards_list.html", items=items, plans=plans, batches=batches, used=used)


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
