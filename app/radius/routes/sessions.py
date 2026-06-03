"""
routes للجلسات المباشرة (M3 — قراءة + disconnect واحد).
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
    bp.add_url_rule("/online/temp-speed", "online_temp_speed", online_temp_speed, methods=["POST"])
    bp.add_url_rule("/online/temp-speed/cancel", "online_temp_speed_cancel", online_temp_speed_cancel, methods=["POST"])


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


def _parse_datetime(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        value = str(raw).strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(value[:19], fmt)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_meta(raw) -> dict:
    try:
        data = json.loads(raw or "{}") or {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _meta_value(meta: dict, key: str) -> str:
    advanced = meta.get("advanced") if isinstance(meta.get("advanced"), dict) else {}
    return str(advanced.get(key) or meta.get(key) or "").strip()


def _int_or_zero(raw) -> int:
    try:
        return int(float(str(raw or "").strip()))
    except (TypeError, ValueError):
        return 0


def _temporary_speed_states(usernames: set[str], now: datetime) -> dict[str, dict]:
    if not usernames:
        return {}

    from ..db.connection import db

    placeholders = ",".join("?" for _ in usernames)
    rows = db().execute(
        f"""
        SELECT username, temporary_speed, custom_speed, metadata, updated_at
          FROM subscribers
         WHERE tenant_id = ?
           AND username IN ({placeholders})
        """,
        (_tid(), *sorted(usernames)),
    ).fetchall()

    states: dict[str, dict] = {}
    for row in rows:
        username = row["username"]
        has_flag = bool(row["temporary_speed"])
        meta = _parse_meta(row["metadata"])
        started_at = _parse_datetime(_meta_value(meta, "temporary_speed_from"))
        ends_at = _parse_datetime(_meta_value(meta, "temporary_speed_to"))
        duration_min = _int_or_zero(_meta_value(meta, "temporary_speed_duration_minutes"))
        if not started_at:
            started_at = _parse_datetime(row["updated_at"])
        if not ends_at and started_at and duration_min > 0:
            ends_at = started_at + timedelta(minutes=duration_min)

        unknown = bool(has_flag and not ends_at)
        remaining = int((ends_at - now).total_seconds()) if ends_at else None
        active = bool(has_flag and (unknown or (remaining is not None and remaining > 0)))
        states[username] = {
            "active": active,
            "unknown": unknown,
            "expired": bool(has_flag and ends_at and not active),
            "remaining_seconds": max(0, remaining) if remaining is not None else None,
            "ends_at": ends_at.isoformat(timespec="seconds") if ends_at else "",
            "custom_speed": bool(row["custom_speed"]),
        }
    return states


def _temporary_speed_end(row) -> datetime | None:
    meta = _parse_meta(row["metadata"])
    started_at = _parse_datetime(_meta_value(meta, "temporary_speed_from"))
    ends_at = _parse_datetime(_meta_value(meta, "temporary_speed_to"))
    duration_min = _int_or_zero(_meta_value(meta, "temporary_speed_duration_minutes"))
    if not started_at:
        started_at = _parse_datetime(row["updated_at"])
    if not ends_at and started_at and duration_min > 0:
        ends_at = started_at + timedelta(minutes=duration_min)
    return ends_at


def _expire_temporary_speeds(now: datetime) -> None:
    from ..db.connection import db

    rows = db().execute(
        """
        SELECT id, temporary_speed, custom_speed, metadata, updated_at
          FROM subscribers
         WHERE tenant_id = ?
           AND temporary_speed = 1
        """,
        (_tid(),),
    ).fetchall()
    expired_ids: list[int] = []
    expired_temp_only_ids: list[int] = []
    for row in rows:
        ends_at = _temporary_speed_end(row)
        if ends_at and ends_at <= now:
            subscriber_id = int(row["id"])
            expired_ids.append(subscriber_id)
            if not bool(row["custom_speed"]):
                expired_temp_only_ids.append(subscriber_id)
    if not expired_ids:
        return

    placeholders = ",".join("?" for _ in expired_ids)
    db().execute(
        f"""
        UPDATE subscribers
           SET temporary_speed = 0,
               updated_at = ?
         WHERE tenant_id = ?
           AND id IN ({placeholders})
        """,
        (now.isoformat(timespec="seconds"), _tid(), *expired_ids),
    )
    if expired_temp_only_ids:
        temp_only_placeholders = ",".join("?" for _ in expired_temp_only_ids)
        db().execute(
            f"""
            UPDATE subscribers
               SET bandwidth_control_enabled = 0,
                   download_speed_kbps = 0,
                   upload_speed_kbps = 0,
                   updated_at = ?
             WHERE tenant_id = ?
               AND id IN ({temp_only_placeholders})
            """,
            (now.isoformat(timespec="seconds"), _tid(), *expired_temp_only_ids),
        )
    db().commit()


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
    selected_group_raw = (request.args.get("group_id") or "").strip()
    selected_group_id = int(selected_group_raw) if selected_group_raw.isdigit() else None
    now = datetime.utcnow()
    try:
        # Authoritative expiry path: pushes a revert CoA to the live session
        # (not just a DB flag flip) so a page load can't silently leave a
        # session throttled. The background worker calls the same function.
        from ..services.temp_speed import expire_due_temp_speeds
        expire_due_temp_speeds(tenant_id=_tid(), now=now)
    except Exception:
        pass

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
    group_options = []
    if selected_group_id:
        try:
            from ..db.repos import subscriber_groups_repo
            member_names = set(
                subscriber_groups_repo.list_member_usernames(_tid(), selected_group_id)
            )
            items = [it for it in items if it.username in member_names]
            group_options = subscriber_groups_repo.list_groups(_tid())
        except Exception:
            selected_group_id = None
            group_options = []
    else:
        try:
            from ..db.repos import subscriber_groups_repo
            group_options = subscriber_groups_repo.list_groups(_tid())
        except Exception:
            group_options = []

    temp_speed_state_by_username = _temporary_speed_states(
        {it.username for it in items if it.username},
        now,
    )

    def _has_active_temporary_speed(item) -> bool:
        state = temp_speed_state_by_username.get(item.username)
        if state is None:
            return bool(item.has_temporary_speed)
        return bool(state.get("active"))

    def _has_special_speed(item) -> bool:
        return bool(item.has_custom_speed or _has_active_temporary_speed(item))

    if selected_nas:
        items = [it for it in items if it.nas_address == selected_nas]
    if selected_plan:
        items = [it for it in items if it.plan_name == selected_plan]
    if selected_speed == "special":
        items = [it for it in items if _has_special_speed(it)]
    elif selected_speed == "temporary":
        items = [it for it in items if _has_active_temporary_speed(it)]
    elif selected_speed == "normal":
        items = [it for it in items if not _has_special_speed(it)]

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
        selected_group_id=selected_group_id,
        group_options=group_options,
        device_by_mac=device_by_mac,
        temp_speed_state_by_username=temp_speed_state_by_username,
        now=now,
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


def online_temp_speed():
    """Apply a temporary speed to one active session — LIVE.

    Throttles the selected live session immediately via a rate-CoA and schedules
    an automatic revert at expiry (the temp_speed_expiry worker). Subscribers
    only (cards have no per-user override). Tenant-scoped + audited in the
    service; gated by ``users.edit`` in the blueprint permission guard.
    """
    try:
        row = _selected_online_row()
        username = row["username"]
        if row["card_id"]:
            raise RadiusError("السرعة المؤقتة متاحة للمشتركين فقط.")
        from ..services.temp_speed import apply_temp_speed
        try:
            result = apply_temp_speed(
                tenant_id=_tid(),
                actor=_actor(),
                username=username,
                down_kbps=_int_or_zero(request.form.get("down_kbps")),
                up_kbps=_int_or_zero(request.form.get("up_kbps")),
                duration_minutes=_int_or_zero(request.form.get("duration_minutes")),
            )
        except ValueError as exc:
            raise RadiusError(str(exc)) from exc
        if result["coa"].get("ok"):
            flash(
                f"تم تطبيق سرعة مؤقتة ({result['rate']}) على {username} "
                f"حتى {result['ends_at']} وأُرسل التغيير للجلسة مباشرةً.",
                "success",
            )
        else:
            flash(
                f"حُفظت السرعة المؤقتة ({result['rate']}) لـ {username} حتى "
                f"{result['ends_at']}، لكن لم تُؤكَّد على الجلسة الحيّة "
                f"({result['coa'].get('code') or 'no_coa'}) — ستُطبَّق عند إعادة الاتصال.",
                "warning",
            )
    except RadiusError as e:
        flash(e.message or "تعذّر تطبيق السرعة المؤقتة", "error")
    return _return_to_online()


def online_temp_speed_cancel():
    """Cancel an active temporary speed on a session — LIVE.

    Reverts via the SAME shared service used by the profile/edit cancel, so a
    window opened from either place is cancellable here (restore CoA now, no
    wait for expiry). Gated by ``users.edit``."""
    try:
        row = _selected_online_row()
        username = row["username"]
        from ..services.temp_speed import cancel_temp_speed
        res = cancel_temp_speed(tenant_id=_tid(), actor=_actor(), username=username)
        if res.get("reverted"):
            flash(f"تم إلغاء السرعة المؤقتة لـ {username} وإرجاعه لسرعته العادية فورًا.",
                  "success")
        else:
            flash(f"لا توجد سرعة مؤقتة فعّالة لـ {username}.", "info")
    except RadiusError as e:
        flash(e.message or "تعذّر إلغاء السرعة المؤقتة", "error")
    return _return_to_online()
