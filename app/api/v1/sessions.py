"""Sessions endpoints: online users list and live session controls."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from ipaddress import ip_address

from flask import Blueprint, g, request

from ..access_control import deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..responses import fail, ok
from ...radius.core.errors import RadiusError


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/sessions/online", "sessions_online",
                    require_api_token(sessions_online), methods=["GET"])
    bp.add_url_rule("/sessions/disconnect", "sessions_disconnect",
                    require_api_token(sessions_disconnect), methods=["POST"])
    bp.add_url_rule("/sessions/lock-mac", "sessions_lock_mac",
                    require_api_token(sessions_lock_mac), methods=["POST"])
    bp.add_url_rule("/sessions/lock-ip", "sessions_lock_ip",
                    require_api_token(sessions_lock_ip), methods=["POST"])
    bp.add_url_rule("/sessions/temp-speed", "sessions_temp_speed",
                    require_api_token(sessions_temp_speed), methods=["POST"])
    bp.add_url_rule("/sessions/temp-speed/cancel", "sessions_temp_speed_cancel",
                    require_api_token(sessions_temp_speed_cancel), methods=["POST"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return f"api-token:{getattr(g, 'api_token_id', 'env')}"


def _svc():
    from ...radius.services.sessions import get_online_sessions_service
    return get_online_sessions_service()


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _int_or_zero(raw) -> int:
    try:
        return int(float(str(raw or "").strip()))
    except (TypeError, ValueError):
        return 0


def _normalise_mac(raw: str) -> str:
    cleaned = (raw or "").strip().upper().replace("-", ":")
    hex_only = cleaned.replace(":", "")
    if len(hex_only) != 12 or any(c not in "0123456789ABCDEF" for c in hex_only):
        raise RadiusError("عنوان MAC في الجلسة غير صالح.")
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def _selected_online_row(body: dict):
    username = (body.get("username") or "").strip()
    session_id = (body.get("session_id") or "").strip()
    if not username:
        raise RadiusError("اسم المستخدم مطلوب.")
    if not session_id:
        raise RadiusError("معرف الجلسة مطلوب.")
    if not subscriber_in_scope(username=username):
        return None

    from ...radius.db.connection import db

    return db().execute(
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


def _require_online_row(body: dict):
    row = _selected_online_row(body)
    if row is None:
        username = (body.get("username") or "").strip()
        if username and not subscriber_in_scope(username=username):
            raise PermissionError
        raise RadiusError("الجلسة المحددة غير متصلة الآن أو انتهت.")
    return row


def _matches_query(item: dict, query: str) -> bool:
    if not query:
        return True
    q = query.lower()
    return any(
        q in str(item.get(key) or "").lower()
        for key in (
            "username", "mac_address", "framed_ip", "nas_address",
            "session_id", "user_type", "state",
        )
    )


def _enrich_session(item: dict) -> dict:
    from ...radius.db.repos import cards_repo, subscribers_repo
    from ...radius.services.operations import classify_online_state

    username = item.get("username") or ""
    card = cards_repo.get_card_by_username(_tid(), username)
    sub = subscribers_repo.get_subscriber(_tid(), username)
    is_card = card is not None
    expire_at = card.expire_at if is_card else (sub.expire_at if sub else None)
    account_status = (
        "revoked" if is_card and getattr(card, "revoked", False)
        else (sub.status if sub else "active")
    )
    item.update(classify_online_state(
        account_status=account_status,
        expire_at=expire_at,
        is_online=True,
    ))
    item["account_status"] = sub.status if sub else None
    item["subscriber_id"] = sub.id if sub else None
    item["card_id"] = card.id if card else None
    item["card_batch_id"] = card.batch_id if card else None
    item["user_type"] = "card" if is_card else "subscriber"
    item["user_type_label"] = "بطاقة" if is_card else "مشترك"
    item["expires_at"] = expire_at.isoformat() + "Z" if expire_at else None

    # Backward-compatible aliases for older mobile clients and clearer JSON.
    item["nas_ip_address"] = item.get("nas_address") or ""
    item["framed_ip_address"] = item.get("framed_ip") or ""
    item["calling_station_id"] = item.get("mac_address") or ""
    item["called_station_id"] = item.get("called_station_id") or ""
    item["nas_port_id"] = item.get("nas_port_id") or item.get("nas_id") or ""

    started_at = item.get("started_at")
    last_update_at = item.get("last_update_at") or datetime.utcnow()
    if hasattr(started_at, "replace") and hasattr(last_update_at, "replace"):
        try:
            item["session_time"] = max(0, int((last_update_at - started_at).total_seconds()))
        except TypeError:
            item["session_time"] = 0
    else:
        item["session_time"] = 0
    return item


def sessions_online():
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    kind = (request.args.get("type") or request.args.get("kind") or "all").strip().lower()
    aliases = {
        "": "all",
        "all": "all",
        "subscriber": "subscriber",
        "subscribers": "subscriber",
        "user": "subscriber",
        "users": "subscriber",
        "card": "card",
        "cards": "card",
    }
    kind = aliases.get(kind, kind)
    if kind not in {"all", "subscriber", "card"}:
        return fail("validation_error", "نوع الجلسات يجب أن يكون الكل أو مشترك أو كرت.", status=422)
    # فلتر نوع السرعة — يطابق صفحة الجلسات المتصلة (selected_speed):
    #   ""/all = الكل · special = سرعة خاصة أو مؤقتة فعّالة · temporary = مؤقتة
    #   فعّالة فقط · normal = بدون سرعة خاصة.
    speed = (request.args.get("speed") or "").strip().lower()
    speed = {"": "all", "all": "all"}.get(speed, speed)
    if speed not in {"all", "special", "temporary", "normal"}:
        return fail("validation_error", "نوع السرعة يجب أن يكون الكل أو خاصة أو مؤقتة أو عادية.", status=422)
    if len(query) > 80:
        return fail("validation_error", "عبارة البحث طويلة جدًا.", status=422)

    # موازاةً لصفحة الويب: نُنهي نوافذ السرعة المؤقتة المنتهية (revert CoA)
    # قبل القراءة كي لا تُعرض جلسة مخنوقة بعد انتهاء نافذتها. محصّن.
    try:
        from ...radius.services.temp_speed import expire_due_temp_speeds
        expire_due_temp_speeds(tenant_id=_tid())
    except Exception:  # noqa: BLE001
        pass

    items = []
    for session in _svc().list(limit=500):
        data = asdict(session)
        enriched = _enrich_session(data)
        for key in ("started_at", "last_update_at"):
            value = enriched.get(key)
            if hasattr(value, "isoformat"):
                enriched[key] = value.isoformat() + "Z"
        if not subscriber_in_scope(username=enriched.get("username") or ""):
            continue
        if kind != "all" and enriched.get("user_type") != kind:
            continue
        if _matches_query(enriched, query):
            items.append(enriched)

    # حالة السرعة لكل جلسة (يطابق منطق صفحة الويب: _has_active_temporary_speed
    # / _has_special_speed) عبر مصدر temp_speed المشترك.
    from ...radius.services.temp_speed import temp_speed_states
    temp_states = temp_speed_states(
        _tid(), {it.get("username") for it in items if it.get("username")})
    for item in items:
        st = temp_states.get(item.get("username"))
        has_active_temp = bool(st["active"]) if st is not None else bool(item.get("has_temporary_speed"))
        has_special = bool(item.get("has_custom_speed")) or has_active_temp
        item["has_active_temporary_speed"] = has_active_temp
        item["has_special_speed"] = has_special
        item["speed_state"] = (
            "temporary" if has_active_temp
            else ("custom" if item.get("has_custom_speed") else "normal")
        )
        item["temporary_speed_window"] = st  # None عند غياب أي نافذة

    if speed == "special":
        items = [it for it in items if it["has_special_speed"]]
    elif speed == "temporary":
        items = [it for it in items if it["has_active_temporary_speed"]]
    elif speed == "normal":
        items = [it for it in items if not it["has_special_speed"]]

    states: dict[str, int] = {}
    types: dict[str, int] = {"subscriber": 0, "card": 0}
    speeds: dict[str, int] = {"normal": 0, "custom": 0, "temporary": 0}
    for item in items:
        states[item["state"]] = states.get(item["state"], 0) + 1
        user_type = item.get("user_type") or "subscriber"
        types[user_type] = types.get(user_type, 0) + 1
        speeds[item["speed_state"]] = speeds.get(item["speed_state"], 0) + 1
    return ok({
        "items": items,
        "count": len(items),
        "states": states,
        "types": types,
        "speeds": speeds,
        "query": query,
        "type": kind,
        "speed": speed,
    })


def sessions_disconnect():
    body = _body()
    username = (body.get("username") or "").strip()
    if not username:
        return fail("validation_error", "اسم المستخدم مطلوب.", status=422)
    if not subscriber_in_scope(username=username):
        return deny_out_of_scope()
    session_id = body.get("session_id")
    try:
        _svc().disconnect(actor=_actor(), username=username, session_id=session_id)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({"username": username, "session_id": session_id, "disconnect_requested": True})


def sessions_lock_mac():
    body = _body()
    try:
        row = _require_online_row(body)
        mac = _normalise_mac(row["callingstationid"] or "")
        username = row["username"]
        if row["card_id"]:
            from ...radius.db.repos import cards_repo

            changed = cards_repo.set_card_locked_mac(
                _tid(), int(row["card_id"]), mac, actor=_actor()
            )
            if not changed:
                raise RadiusError("تعذر تثبيت MAC للبطاقة.")
            target_type = "card"
        else:
            from ...radius.services.users import get_users_service

            svc = get_users_service()
            sub = svc.get(username)
            svc.update(actor=_actor(), sub=replace(sub, mac_lock=mac, allowed_macs=mac))
            target_type = "subscriber"
    except PermissionError:
        return deny_out_of_scope()
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "mac_address": mac,
        "target_type": target_type,
        "locked": True,
    })


def sessions_lock_ip():
    body = _body()
    try:
        row = _require_online_row(body)
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

        from ...radius.services.users import get_users_service

        svc = get_users_service()
        sub = svc.get(username)
        svc.update(actor=_actor(), sub=replace(sub, static_ip=ip))
    except PermissionError:
        return deny_out_of_scope()
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "ip_address": ip,
        "locked": True,
    })


def sessions_temp_speed():
    body = _body()
    try:
        row = _require_online_row(body)
        username = row["username"]
        if row["card_id"]:
            raise RadiusError("السرعة المؤقتة متاحة للمشتركين فقط.")
        from ...radius.services.temp_speed import apply_temp_speed

        result = apply_temp_speed(
            tenant_id=_tid(),
            actor=_actor(),
            username=username,
            down_kbps=_int_or_zero(body.get("down_kbps")),
            up_kbps=_int_or_zero(body.get("up_kbps")),
            duration_minutes=_int_or_zero(body.get("duration_minutes")),
        )
    except PermissionError:
        return deny_out_of_scope()
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "temporary_speed": result,
    })


def sessions_temp_speed_cancel():
    body = _body()
    try:
        row = _require_online_row(body)
        username = row["username"]
        from ...radius.services.temp_speed import cancel_temp_speed

        result = cancel_temp_speed(tenant_id=_tid(), actor=_actor(), username=username)
    except PermissionError:
        return deny_out_of_scope()
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except Exception as e:  # noqa: BLE001
        return fail("internal_error", str(e), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "temporary_speed": result,
    })
