"""Sessions endpoints: online users list and live session controls."""
from __future__ import annotations
from app.i18n_text import N_, _tr

from dataclasses import asdict, replace
from datetime import datetime
from ipaddress import ip_address

from flask import Blueprint, g, request

from ..access_control import deny_out_of_scope, subscriber_in_scope
from ..auth import require_api_token
from ..responses import fail, ok
from ...radius.core.errors import RadiusConflict, RadiusError
from ...radius.core.strict_input import iso_utc_z


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


class _BadBody(RadiusError):
    """Request body / field of the wrong JSON type → 422 (was an HTML 500 or
    a raw «'int' object has no attribute 'strip'»)."""


def _body() -> dict:
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise _BadBody(_tr("جسم الطلب يجب أن يكون كائن JSON."))
    return body


def _text_field(body: dict, key: str, label: str, *, allow_int: bool = False) -> str:
    value = body.get(key)
    if value is None:
        return ""
    allowed = (str, int) if allow_int else (str,)
    if isinstance(value, bool) or not isinstance(value, allowed):
        raise _BadBody(_tr('«%(label)s» يجب أن يكون نصًّا.', label=label))
    value = str(value).strip()
    if len(value) > 256:
        raise _BadBody(_tr('«%(label)s» طويل جدًا.', label=label))
    return value


def _int_or_zero(raw) -> int:
    try:
        return int(float(str(raw or "").strip()))
    except (TypeError, ValueError):
        return 0


def _normalise_mac(raw: str) -> str:
    cleaned = (raw or "").strip().upper().replace("-", ":")
    hex_only = cleaned.replace(":", "")
    if len(hex_only) != 12 or any(c not in "0123456789ABCDEF" for c in hex_only):
        raise RadiusError(_tr("عنوان MAC في الجلسة غير صالح."))
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def _selected_online_row(body: dict):
    username = _text_field(body, "username", N_("اسم المستخدم"))
    session_id = _text_field(body, "session_id", N_("معرف الجلسة"), allow_int=True)
    if not username:
        raise RadiusError(_tr("اسم المستخدم مطلوب."))
    if not session_id:
        raise RadiusError(_tr("معرف الجلسة مطلوب."))
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
        username = _text_field(body, "username", N_("اسم المستخدم"))
        if username and not subscriber_in_scope(username=username):
            raise PermissionError
        raise RadiusError(_tr("الجلسة المحددة غير متصلة الآن أو انتهت."))
    return row


def _matches_query(item: dict, query: str, mobiles: dict | None = None) -> bool:
    if not query:
        return True
    q = query.lower()
    if mobiles and q in str(mobiles.get(item.get("username") or "", "")).lower():
        return True
    # f06-L4: a MAC in any notation (dashes/colons/dots) — same helper as the web.
    from ...radius.services.sessions import mac_query_matches
    if mac_query_matches(query, item.get("mac_address")):
        return True
    # Same fields as the web /online search (+ session id / type / state).
    return any(
        q in str(item.get(key) or "").lower()
        for key in (
            "username", "full_name", "mac_address", "framed_ip", "plan_name",
            "nas_address", "session_id", "user_type", "state",
        )
    )


_IN_CHUNK = 800
# Upper bound of open sessions scanned per /sessions/online request. The list
# is paged AFTER filtering, so search/filters see every open session (the old
# hard `limit=500` hid sessions 501+ from search and from the list).
_ONLINE_SCAN_CAP = 50_000


def _naive_utc(value):
    from datetime import timezone
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _lookup_accounts(usernames) -> tuple[dict, dict]:
    """Cards + subscribers for many usernames in a few IN queries (was two
    queries PER ROW — ~2.7 s for 500 sessions)."""
    from ...radius.db.connection import db
    from ...radius.db.helpers import parse_dt

    names = sorted({u for u in usernames if u})
    cards: dict = {}
    subs: dict = {}
    for i in range(0, len(names), _IN_CHUNK):
        chunk = names[i:i + _IN_CHUNK]
        ph = ",".join("?" for _ in chunk)
        for r in db().execute(
            f"SELECT c.id, c.username, c.batch_id, c.expire_at, c.revoked,"
            f"       COALESCE(NULLIF(TRIM(b.package_name), ''), b.batch_code, '')"
            f"         AS batch_name"
            f"  FROM cards c LEFT JOIN card_batches b"
            f"    ON b.tenant_id = c.tenant_id AND b.id = c.batch_id"
            f" WHERE c.tenant_id = ? AND c.username IN ({ph}) ORDER BY c.id",
            (_tid(), *chunk),
        ).fetchall():
            cards.setdefault(r["username"], {
                "id": r["id"], "batch_id": r["batch_id"],
                "batch_name": r["batch_name"] or "",
                "expire_at": _naive_utc(parse_dt(r["expire_at"])),
                "revoked": bool(r["revoked"]),
            })
        for r in db().execute(
            f"SELECT id, username, status, expire_at FROM subscribers "
            f" WHERE tenant_id = ? AND deleted_at IS NULL AND username IN ({ph})",
            (_tid(), *chunk),
        ).fetchall():
            subs[r["username"]] = {
                "id": r["id"], "status": r["status"],
                "expire_at": _naive_utc(parse_dt(r["expire_at"])),
            }
    return cards, subs


def _enrich_session(item: dict, accounts: tuple[dict, dict] | None = None) -> dict:
    from ...radius.services.operations import classify_online_state

    username = item.get("username") or ""
    cards, subs = accounts if accounts is not None else _lookup_accounts([username])
    card = cards.get(username)
    sub = subs.get(username)
    is_card = card is not None
    expire_at = card["expire_at"] if is_card else (sub["expire_at"] if sub else None)
    account_status = (
        "revoked" if is_card and card.get("revoked")
        else (sub["status"] if sub else "active")
    )
    item.update(classify_online_state(
        account_status=account_status,
        expire_at=expire_at,
        is_online=True,
    ))
    item["account_status"] = sub["status"] if sub else None
    item["subscriber_id"] = sub["id"] if sub else None
    item["card_id"] = card["id"] if card else None
    item["card_batch_id"] = card["batch_id"] if card else None
    # «كرت · هوت سبوت (حزمة علاء)» على بلاطة المتصلين (المالك 2026-10-05).
    item["card_batch_name"] = (card.get("batch_name") or "") if card else ""
    item["user_type"] = "card" if is_card else "subscriber"
    item["user_type_label"] = N_("بطاقة") if is_card else N_("مشترك")
    # «هوت سبوت / برود باند» — مصدرٌ واحد (services/access_type.py)
    from ...radius.services.access_type import classify_session
    item["access_type"] = classify_session(
        nas_port_type=item.get("nas_port_type"),
        framed_protocol=item.get("framed_protocol"),
        service_type=item.get("service_type"),
        user_type=item["user_type"],
    )
    item["expires_at"] = iso_utc_z(expire_at)

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
        return fail("validation_error", _tr("نوع الجلسات يجب أن يكون الكل أو مشترك أو كرت."), status=422)
    # فلتر نوع السرعة — يطابق صفحة الجلسات المتصلة (selected_speed):
    #   ""/all = الكل · special = سرعة خاصة أو مؤقتة فعّالة · temporary = مؤقتة
    #   فعّالة فقط · normal = بدون سرعة خاصة.
    speed = (request.args.get("speed") or "").strip().lower()
    speed = {"": "all", "all": "all"}.get(speed, speed)
    if speed not in {"all", "special", "temporary", "normal"}:
        return fail("validation_error", _tr("نوع السرعة يجب أن يكون الكل أو خاصة أو مؤقتة أو عادية."), status=422)
    if len(query) > 80:
        return fail("validation_error", _tr("عبارة البحث طويلة جدًا."), status=422)
    # «هوت سبوت / برود باند» (``access``): اختياريّ، والقيمة المجهولة = الكل.
    from ...radius.services.access_type import normalize_access
    access = normalize_access(request.args.get("access"))
    # Real paging (the list used to stop silently at 500). Default page size
    # stays 500 so older app builds that don't page keep their behaviour.
    try:
        limit = int(request.args.get("limit") or 500)
        offset = int(request.args.get("offset") or 0)
    except (TypeError, ValueError):
        return fail("validation_error", _tr("قيم limit و offset يجب أن تكون أرقامًا صحيحة."), status=422)
    if limit < 1 or limit > 1000 or offset < 0:
        return fail("validation_error", _tr("limit بين 1 و1000، و offset لا يكون سالبًا."), status=422)

    # موازاةً لصفحة الويب: نُنهي نوافذ السرعة المؤقتة المنتهية (revert CoA)
    # قبل القراءة كي لا تُعرض جلسة مخنوقة بعد انتهاء نافذتها. محصّن.
    try:
        from ...radius.services.temp_speed import expire_due_temp_speeds
        expire_due_temp_speeds(tenant_id=_tid())
    except Exception:  # noqa: BLE001
        pass

    # fix3 (F02 H2): every scoped manager (not only a distributor login) sees
    # only his own subscribers' live sessions — one set lookup, no per-row query.
    from ..access_control import subscriber_scope_admin_id
    from ...radius.services.subscriber_scope import filter_rows as _scope_rows
    scoped = False
    rows = _scope_rows([asdict(s) for s in _svc().list(limit=_ONLINE_SCAN_CAP)],
                       key="username", tenant_id=_tid(), scope=subscriber_scope_admin_id())
    accounts = _lookup_accounts(r.get("username") for r in rows)
    mobiles: dict = {}
    if query:
        from ...radius.services.sessions import mobiles_by_username
        mobiles = mobiles_by_username(_tid(), (r.get("username") for r in rows))
    items = []
    for data in rows:
        enriched = _enrich_session(data, accounts)
        if scoped and not subscriber_in_scope(username=enriched.get("username") or ""):
            continue
        if kind != "all" and enriched.get("user_type") != kind:
            continue
        if access and enriched.get("access_type") != access:
            continue
        if _matches_query(enriched, query, mobiles):
            items.append(enriched)

    # حالة السرعة لكل جلسة (يطابق منطق صفحة الويب: _has_active_temporary_speed
    # / _has_special_speed) عبر مصدر temp_speed المشترك.
    from ...radius.services.temp_speed import temp_speed_states
    temp_states: dict = {}
    _names = sorted({it.get("username") for it in items if it.get("username")})
    for i in range(0, len(_names), _IN_CHUNK):
        temp_states.update(temp_speed_states(_tid(), _names[i:i + _IN_CHUNK]))
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

    # Counters describe the WHOLE filtered result (not just this page).
    states: dict[str, int] = {}
    types: dict[str, int] = {"subscriber": 0, "card": 0}
    speeds: dict[str, int] = {"normal": 0, "custom": 0, "temporary": 0}
    accesses: dict[str, int] = {"hotspot": 0, "broadband": 0}
    for item in items:
        states[item["state"]] = states.get(item["state"], 0) + 1
        user_type = item.get("user_type") or "subscriber"
        types[user_type] = types.get(user_type, 0) + 1
        speeds[item["speed_state"]] = speeds.get(item["speed_state"], 0) + 1
        if item.get("access_type") in accesses:
            accesses[item["access_type"]] += 1

    total = len(items)
    page = items[offset:offset + limit]
    # «مُستخدَم / متبقّي» للكروت — للصفحة المعروضة وحدَها (حسبة الفاحص نفسها
    # بلا آثارٍ جانبيّة). خطأُ كرتٍ واحد لا يُسقط القائمة.
    from ...radius.services.card_checker import card_time_brief
    for item in page:
        if item.get("user_type") != "card":
            continue
        try:
            brief = card_time_brief(_tid(), item.get("username") or "")
        except Exception:  # noqa: BLE001
            brief = None
        if brief:
            item["card_used_seconds"] = brief["used_seconds"]
            item["card_remaining_seconds"] = brief["remaining_seconds"]
            item["card_budget_seconds"] = brief["budget_seconds"]
    for item in page:
        # One timestamp format on every session field: ISO-8601 UTC + «Z».
        for key in ("started_at", "last_update_at", "expire_at"):
            if key in item:
                item[key] = iso_utc_z(item.get(key))
        win = item.get("temporary_speed_window")
        if isinstance(win, dict) and win.get("ends_at"):
            item["temporary_speed_window"] = dict(win, ends_at=iso_utc_z(win["ends_at"]))
    return ok({
        "items": page,
        "count": len(page),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
        "states": states,
        "types": types,
        "speeds": speeds,
        "accesses": accesses,
        "query": query,
        "type": kind,
        "speed": speed,
        "access": access or "all",
    })


def _disconnect_error(e: RadiusError):
    """One mapping for every disconnect surface (also used by
    /accounts/<u>/disconnect): no live session → 409, router failure → 502."""
    if isinstance(e, RadiusConflict):
        code = (e.details or {}).get("code") or "no_active_session"
        return fail(code, e.message or _tr("لا توجد جلسة نشطة."), status=409)
    return fail("disconnect_failed", e.message or _tr("تعذّر قطع الجلسة."), status=502)


def sessions_disconnect():
    try:
        body = _body()
        username = _text_field(body, "username", N_("اسم المستخدم"))
        session_id = _text_field(body, "session_id", N_("معرف الجلسة"), allow_int=True) or None
    except _BadBody as e:
        return fail("validation_error", e.message, status=422)
    if not username:
        return fail("validation_error", _tr("اسم المستخدم مطلوب."), status=422)
    if not subscriber_in_scope(username=username):
        return deny_out_of_scope()
    try:
        _svc().disconnect(actor=_actor(), username=username, session_id=session_id)
    except RadiusError as e:
        return _disconnect_error(e)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("sessions/disconnect failed for %s", username)
        return fail("internal_error", _tr("حدث خطأ غير متوقع أثناء قطع الجلسة."), status=500)
    return ok({"username": username, "session_id": session_id, "disconnect_requested": True})


def sessions_lock_mac():
    try:
        body = _body()
        row = _require_online_row(body)
        mac = _normalise_mac(row["callingstationid"] or "")
        username = row["username"]
        if row["card_id"]:
            from ...radius.db.repos import cards_repo

            changed = cards_repo.set_card_locked_mac(
                _tid(), int(row["card_id"]), mac, actor=_actor()
            )
            if not changed:
                raise RadiusError(_tr("تعذر تثبيت MAC للبطاقة."))
            # zero-w1: same audit row as the subscriber path / cards checker.
            from ...radius.services.cards import get_cards_service
            get_cards_service().audit_card_mac_lock(
                actor=_actor(), card_id=int(row["card_id"]), mac=mac,
                source="online_api")
            target_type = "card"
        else:
            from ...radius.services.users import get_users_service

            svc = get_users_service()
            sub = svc.get(username)
            svc.update(actor=_actor(), sub=replace(sub, mac_lock=mac, allowed_macs=mac), base=sub)
            target_type = "subscriber"
    except PermissionError:
        return deny_out_of_scope()
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("session action failed")
        return fail("internal_error", _tr("حدث خطأ غير متوقع أثناء تنفيذ العملية على الجلسة."), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "mac_address": mac,
        "target_type": target_type,
        "locked": True,
    })


def sessions_lock_ip():
    try:
        body = _body()
        row = _require_online_row(body)
        username = row["username"]
        if row["card_id"]:
            raise RadiusError(_tr("تثبيت IP متاح للمشتركين فقط."))
        ip = (row["framedipaddress"] or "").strip()
        if not ip:
            raise RadiusError(_tr("لا يوجد IP على الجلسة المحددة."))
        try:
            ip_address(ip)
        except ValueError as exc:
            raise RadiusError(_tr("عنوان IP في الجلسة غير صالح.")) from exc

        from ...radius.services.users import get_users_service

        svc = get_users_service()
        sub = svc.get(username)
        svc.update(actor=_actor(), sub=replace(sub, static_ip=ip), base=sub)
    except PermissionError:
        return deny_out_of_scope()
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("session action failed")
        return fail("internal_error", _tr("حدث خطأ غير متوقع أثناء تنفيذ العملية على الجلسة."), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "ip_address": ip,
        "locked": True,
    })


def _effective_duration_minutes(body: dict) -> int:
    """مدّة التطبيق بالدقائق — تكافؤ مع نموذج الويب الذي يقبل
    duration + duration_unit (minutes|hours|days). `duration_minutes` (إن وُجد)
    له الأولوية للتوافق الخلفي. وحدة غير معروفة → 422 (كانت تُعامَل صامتةً
    كدقائق: «2 days» = دقيقتان). المنطق مشترك مع الويب (services.temp_speed)."""
    from ...radius.services.temp_speed import parse_duration_minutes
    return parse_duration_minutes(
        duration_minutes=body.get("duration_minutes"),
        duration=body.get("duration"),
        unit=body.get("duration_unit"),
    )


def _temp_speed_out(result: dict) -> dict:
    out = dict(result or {})
    if out.get("ends_at"):
        out["ends_at"] = iso_utc_z(out["ends_at"])
    return out


def sessions_temp_speed():
    try:
        body = _body()
        row = _require_online_row(body)
        username = row["username"]
        if row["card_id"]:
            # الكرت المولَّد له «مرآة» في subscribers تحمل سرعته ⇒ السرعة
            # المؤقتة تعمل عليه كالمشترك (قرار المالك 2026-10-01). كرتٌ بلا
            # مرآة (مستورد قديم/متجر فوريّ) يُرفض برسالةٍ واضحة.
            from ...radius.services.temp_speed import require_speed_account
            require_speed_account(_tid(), username)
        from ...radius.services.temp_speed import apply_temp_speed, parse_kbps

        down_kbps = parse_kbps(body.get("down_kbps"), N_("سرعة التنزيل"))
        up_kbps = parse_kbps(body.get("up_kbps"), N_("سرعة الرفع"))
        duration_minutes = _effective_duration_minutes(body)
        result = apply_temp_speed(
            tenant_id=_tid(),
            actor=_actor(),
            username=username,
            down_kbps=down_kbps,
            up_kbps=up_kbps,
            duration_minutes=duration_minutes,
        )
    except PermissionError:
        return deny_out_of_scope()
    except ValueError as e:
        return fail("validation_error", str(e), status=422)
    except RadiusError as e:
        return fail("validation_error", e.message, status=422)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("session action failed")
        return fail("internal_error", _tr("حدث خطأ غير متوقع أثناء تنفيذ العملية على الجلسة."), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "temporary_speed": _temp_speed_out(result),
    })


def sessions_temp_speed_cancel():
    try:
        body = _body()
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
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("session action failed")
        return fail("internal_error", _tr("حدث خطأ غير متوقع أثناء تنفيذ العملية على الجلسة."), status=500)
    return ok({
        "username": username,
        "session_id": row["acctsessionid"],
        "temporary_speed": result,
    })
