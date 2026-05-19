"""
routes للجلسات المباشرة (M3 — قراءة + disconnect واحد).
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..integration.factory import get_radius_adapter
from ..services.sessions import get_online_sessions_service


def register_sessions_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/online", "online_list", online_list, methods=["GET"])
    bp.add_url_rule("/online/disconnect", "online_disconnect", online_disconnect, methods=["POST"])


def _actor() -> str:
    return (
        session.get("admin_user")
        or session.get("username")
        or session.get("account_id")
        or "anonymous"
    )


def online_list():
    """?type=card → يفلتر إلى usernames التي صُدِرت من حزم البطاقات."""
    svc = get_online_sessions_service()
    settings = get_radius_adapter().settings()
    filter_type = (request.args.get("type") or "").strip().lower()
    try:
        items = svc.list(limit=500)
        error = None
    except RadiusError as e:
        items = []
        error = e.message

    if filter_type == "card" and items:
        try:
            from ..services.cards import get_cards_service
            card_usernames = {c.username for c in
                              get_cards_service().list_cards(limit=10000)}
            items = [it for it in items if it.username in card_usernames]
        except Exception:
            pass  # لو فشل الـ lookup، نُظهر الكل بدلًا من قائمة فارغة كاذبة

    return render_template(
        "radius/sessions_list.html",
        items=items,
        settings=settings,
        error=error,
        filter_type=filter_type,
    )


def online_disconnect():
    username = (request.form.get("username") or "").strip()
    session_id = (request.form.get("session_id") or "").strip() or None
    if not username:
        flash("اسم المستخدم مطلوب", "error")
        return redirect(url_for("radius.online_list"))
    try:
        get_online_sessions_service().disconnect(
            actor=_actor(), username=username, session_id=session_id
        )
        flash(f"تم إرسال أمر قطع الجلسة لـ {username}.", "success")
    except RadiusError as e:
        flash(e.message or "تعذّر قطع الجلسة", "error")
    return redirect(url_for("radius.online_list"))
