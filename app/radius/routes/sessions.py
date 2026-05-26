"""
routes للجلسات المباشرة (M3 — قراءة + disconnect واحد).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from ipaddress import ip_address

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..integration.factory import get_radius_adapter
from ..services.sessions import get_online_sessions_service


def register_sessions_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/online", "online_list", online_list, methods=["GET"])
    bp.add_url_rule("/online/disconnect", "online_disconnect", online_disconnect, methods=["POST"])
    bp.add_url_rule("/online/lock-mac", "online_lock_mac", online_lock_mac, methods=["POST"])
    bp.add_url_rule("/online/lock-ip", "online_lock_ip", online_lock_ip, methods=["POST"])


def _actor() -> str:
    return (
        session.get("admin_user")
        or session.get("username")
        or session.get("account_id")
        or "anonymous"
    )


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _return_to_online():
    next_url = (request.form.get("next") or "").strip()
    if next_url.startswith("/admin/radius/online"):
        return redirect(next_url)

    referrer = request.referrer or ""
    online_prefix = request.host_url.rstrip("/") + url_for("radius.online_list")
    if referrer.startswith(online_prefix):
        return redirect(referrer)

    return redirect(url_for("radius.online_list"))


def _selected_online_row():
    username = (request.form.get("username") or "").strip()
    session_id = (request.form.get("session_id") or "").strip()
    if not username or not session_id:
        raise RadiusError("حدد جلسة أولًا.")

    from ..db.connection import db

    row = db().execute(
        """
        SELECT r.username, r.acctsessionid, r.framedipaddress, r.callingstationid,
               CASE WHEN c.id IS NOT NULL THEN c.id ELSE NULL END AS card_id
        FROM radacct r
        LEFT JOIN cards c
          ON c.tenant_id = r.tenant_id AND c.username = r.username
        WHERE r.tenant_id = ?
          AND r.username = ?
          AND r.acctsessionid = ?
          AND r.acctstoptime IS NULL
        LIMIT 1
        """,
        (_tid(), username, session_id),
    ).fetchone()
    if not row:
        raise RadiusError("الجلسة المحددة غير متصلة الآن أو انتهت.")
    return row


def _normalise_mac(raw: str) -> str:
    cleaned = (raw or "").strip().upper().replace("-", ":")
    hex_only = cleaned.replace(":", "")
    if len(hex_only) != 12 or any(c not in "0123456789ABCDEF" for c in hex_only):
        raise RadiusError("عنوان MAC في الجلسة غير صالح.")
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def online_list():
    """R12.2: فصل صارم بين شاشتين:
      - افتراضي (`/online`)          → المشتركون فقط (يستثني كل usernames
                                       المسجّلة كـ user_type=card).
      - `/online?type=card`         → الكروت فقط.

    قبل R12.2 الافتراضي كان يعرض الاثنين مختلطين، فيظهر كرت (مثل 2044)
    في شاشة "المشتركين المتصلين" بشكل يربك الإدمن. الفصل يطابق صفحات
    /users vs /cards: كل شاشة لجمهورها فقط.
    """
    svc = get_online_sessions_service()
    settings = get_radius_adapter().settings()
    filter_type = (request.args.get("type") or "").strip().lower()
    selected_nas = (request.args.get("nas") or "").strip()
    selected_plan = (request.args.get("plan") or "").strip()
    selected_speed = (request.args.get("speed") or "").strip().lower()
    try:
        items = svc.list(limit=500)
        error = None
    except RadiusError as e:
        items = []
        error = e.message

    if items:
        try:
            from ..services.cards import get_cards_service
            card_usernames = {c.username for c in
                              get_cards_service().list_cards(limit=10000)}
        except Exception:
            card_usernames = None  # فشل lookup → fallback لعرض الكل
        if card_usernames is not None:
            if filter_type == "card":
                items = [it for it in items if it.username in card_usernames]
            else:
                items = [it for it in items if it.username not in card_usernames]

    nas_options = sorted({it.nas_address for it in items if it.nas_address})
    plan_options = sorted({it.plan_name for it in items if it.plan_name})

    if selected_nas:
        items = [it for it in items if it.nas_address == selected_nas]
    if selected_plan:
        items = [it for it in items if it.plan_name == selected_plan]
    if selected_speed == "special":
        items = [it for it in items if it.has_custom_speed or it.has_temporary_speed]
    elif selected_speed == "temporary":
        items = [it for it in items if it.has_temporary_speed]
    elif selected_speed == "normal":
        items = [it for it in items if not it.has_custom_speed and not it.has_temporary_speed]

    device_by_mac = {}
    try:
        from ..db.repos import device_fingerprints_repo
        from ..services.card_checker import _dhcp_device

        macs = [it.mac_address for it in items if it.mac_address]
        if macs:
            fp_by_mac = device_fingerprints_repo.get_many_by_macs(_tid(), macs)
            for mac, fp in fp_by_mac.items():
                device = _dhcp_device(fp)
                if device:
                    device_by_mac[mac] = device
    except Exception:
        device_by_mac = {}

    return render_template(
        "radius/sessions_list.html",
        items=items,
        settings=settings,
        error=error,
        filter_type=filter_type,
        nas_options=nas_options,
        plan_options=plan_options,
        selected_nas=selected_nas,
        selected_plan=selected_plan,
        selected_speed=selected_speed,
        device_by_mac=device_by_mac,
        now=datetime.utcnow(),
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
    return _return_to_online()


def online_lock_mac():
    try:
        row = _selected_online_row()
        mac = _normalise_mac(row["callingstationid"] or "")
        username = row["username"]
        if row["card_id"]:
            from ..db.repos import cards_repo

            if not cards_repo.set_card_locked_mac(
                _tid(), int(row["card_id"]), mac, actor=_actor()
            ):
                raise RadiusError("تعذّر تثبيت MAC للبطاقة.")
            flash(f"تم تثبيت MAC {mac} على البطاقة {username}.", "success")
        else:
            from ..services.users import get_users_service

            svc = get_users_service()
            sub = svc.get(username)
            svc.update(actor=_actor(), sub=replace(sub, mac_lock=mac, allowed_macs=mac))
            flash(f"تم تثبيت MAC {mac} على المشترك {username}.", "success")
    except RadiusError as e:
        flash(e.message or "تعذّر تثبيت MAC", "error")
    return _return_to_online()


def online_lock_ip():
    try:
        row = _selected_online_row()
        username = row["username"]
        if row["card_id"]:
            raise RadiusError("تثبيت IP متاح للمشتركين فقط.")
        ip = (row["framedipaddress"] or "").strip()
        if not ip:
            raise RadiusError("لا يوجد IP على الجلسة المحددة.")
        try:
            ip_address(ip)
        except ValueError as exc:
            raise RadiusError("عنوان IP في الجلسة غير صالح.") from exc

        from ..services.users import get_users_service

        svc = get_users_service()
        sub = svc.get(username)
        svc.update(actor=_actor(), sub=replace(sub, static_ip=ip))
        flash(f"تم تثبيت IP {ip} على المشترك {username}.", "success")
    except RadiusError as e:
        flash(e.message or "تعذّر تثبيت IP", "error")
    return _return_to_online()
