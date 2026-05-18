"""Cards routes — batches + generate + list."""
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


def cards_batches():
    svc = get_cards_service()
    batches = svc.list_batches(limit=500)
    plans = {p.id: p for p in get_plans_service().list(limit=500)}
    return render_template("radius/cards_batches.html", batches=batches, plans=plans)


def cards_generate():
    if request.method == "POST":
        try:
            plan_id = int(request.form.get("plan_id") or 0)
            count = int(request.form.get("count") or 0)
            batch, cards = get_cards_service().generate_batch(
                actor=_actor(),
                plan_id=plan_id,
                count=count,
                username_prefix=(request.form.get("username_prefix") or "").strip(),
                username_length=int(request.form.get("username_length") or 8),
                password_length=int(request.form.get("password_length") or 6),
                notes=(request.form.get("notes") or "").strip(),
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
