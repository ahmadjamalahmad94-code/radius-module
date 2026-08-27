"""
routes للجلسات المباشرة (M3 — قراءة + disconnect واحد).
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..integration.factory import get_radius_adapter
from ..services.sessions import get_online_sessions_service


def register_sessions_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/online", "online_list", online_list, methods=["GET"])
    bp.add_url_rule("/online/live-status", "online_live_status", online_live_status, methods=["GET"])
    bp.add_url_rule("/online/reconcile", "online_reconcile", online_reconcile, methods=["POST"])
    bp.add_url_rule("/online/disconnect", "online_disconnect", online_disconnect, methods=["POST"])
    bp.add_url_rule("/online/force-close", "online_force_close", online_force_close, methods=["POST"])
    bp.add_url_rule("/online/lock-mac", "online_lock_mac", online_lock_mac, methods=["POST"])
    bp.add_url_rule("/online/lock-ip", "online_lock_ip", online_lock_ip, methods=["POST"])
    bp.add_url_rule("/online/temp-speed", "online_temp_speed", online_temp_speed, methods=["POST"])
    # Optional operator-triggered PoD fallback (never automatic) — apply the
    # temp speed by disconnecting so the user re-auths with the new rate.
    bp.add_url_rule("/online/temp-speed/reauth", "online_temp_speed_reauth", online_temp_speed_reauth, methods=["POST"])
    bp.add_url_rule("/online/temp-speed/cancel", "online_temp_speed_cancel", online_temp_speed_cancel, methods=["POST"])
    # Live CoA control (RFC 5176) — owner-triggered, one packet per click.
    # Owner proved on a live MikroTik that PPPoE Framed-IP-Address via CoA
    # changes the connected user's INTERNAL/session IP (LAN) WITHOUT
    # disconnect. This is the FREE «تغيير IP الجلسة الداخلية» — distinct
    # from the paid public «تغيير عنوان التصفح العام» (ip_change_service).
    bp.add_url_rule("/online/coa/set-ip",    "online_coa_set_ip",
                    online_coa_set_ip,    methods=["POST"])
    bp.add_url_rule("/online/coa/set-speed", "online_coa_set_speed",
                    online_coa_set_speed, methods=["POST"])


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


def _selected_online_pairs() -> list[tuple[str, str]]:
    """يقرأ أزواج (username, session_id) من النموذج — يدعم التحديد المتعدد.

    الواجهة تحقن حقل username + session_id لكل جلسة محددة، لذا نقرأها
    بـ getlist ونطابقها بالترتيب. تبقى متوافقة مع إرسال زوج واحد فقط.
    """
    usernames = [u.strip() for u in request.form.getlist("username")]
    session_ids = [s.strip() for s in request.form.getlist("session_id")]
    pairs = [(u, s) for u, s in zip(usernames, session_ids) if u and s]
    if not pairs:
        raise RadiusError("حدد جلسة أولًا.")
    return pairs


def _selected_online_row(username: str | None = None, session_id: str | None = None):
    if username is None or session_id is None:
        username, session_id = _selected_online_pairs()[0]
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
    # المصدر الوحيد لهذا المنطق صار services.temp_speed.temp_speed_states (كي
    # يستهلكه v1 API أيضًا). المخرجات مطابقة تمامًا لما كان هنا: نهاية النافذة
    # بدقّة من from+duration أو to الصريح، بلا fallback لـupdated_at (#50a).
    from ..services.temp_speed import temp_speed_states
    return temp_speed_states(_tid(), usernames, now)


def _temporary_speed_end(row) -> datetime | None:
    # #50a: strict window end — temporary_speed_from + duration (or explicit
    # _to). No updated_at fallback (see _temporary_speed_states).
    meta = _parse_meta(row["metadata"])
    started_at = _parse_datetime(_meta_value(meta, "temporary_speed_from"))
    ends_at = _parse_datetime(_meta_value(meta, "temporary_speed_to"))
    duration_min = _int_or_zero(_meta_value(meta, "temporary_speed_duration_minutes"))
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
    search_q = (request.args.get("q") or "").strip().lower()
    now = datetime.utcnow()
    # إنفاذ انتهاء السرعة المؤقتة (revert-CoA) نُقل خارج مسار التصيير: يُنفّذه
    # العامل الخلفيّ دوريّاً، وكذلك نقطة /online/live-status اللا-متزامنة عند فتح
    # الصفحة — فلا يَبقى في تحميل الصفحة أيّ I/O شبكيّ حاجب (الشِّل أوّلاً).

    # الشِّل أوّلاً («يكون تحميل الصفحة أوّل شي وبعدين القراءة»): لا استطلاع
    # للراوترات في مسار التصيير. المشكلة التي كانت تُعلّق الصفحة: كان
    # ``connected_live.refresh_and_reconcile`` يَستطلع كلّ راوتر بالتسلسل بمهلة
    # 4s لكلّ راوتر، فراوتر بطيء/مفصول كان يُجمّد تحميل الصفحة حتى يُقرّر أنه
    # غير متصل. الآن نُصيّر فوراً من radacct + آخر حالة liveness محفوظة (قراءة
    # ذاكرة/DB لحظيّة بلا شبكة)، والاستطلاع+المصالحة يجريان لاحقاً عبر نقطة
    # ``/online/live-status`` التي تَستدعيها الصفحة عند التحميل وتُكرّرها دوريّاً
    # (خارج مسار الطلب) — فلا يَحجب أيّ فحصِ راوترٍ تحميلَ الصفحة أبداً.
    try:
        items = svc.list(limit=500)
        error = None
    except RadiusError as e:
        items = []
        error = e.message

    # فلترة الحالة الحيّة: لا نَعرض جلسات على راوتر غير قابل للوصول (لا يمكن
    # التحقّق → لا بيانات). نُطبّق الفلترة فقط حين يوجد سجلّ liveness (المُستطلِع
    # يعمل)؛ وإلّا نَرتدّ لعرض الكلّ (نشرة بلا API / اختبارات).
    router_unreachable = False
    unreachable_routers: list[str] = []
    hidden_sessions = 0
    reach_by_ip: dict = {}
    try:
        from ..services import connected_live, nas_liveness
        # قراءة فقط لآخر حالة liveness محفوظة (ذاكرة) — بلا شبكة ولا استطلاع.
        if nas_liveness.has_data(_tid()):
            reach = connected_live.reachability_by_ip(_tid())
            reach_by_ip = reach
            unreachable_routers = connected_live.unreachable_router_labels(_tid())
            before = len(items)
            items = [it for it in items
                     if reach.get((it.nas_address or "").strip()) is True]
            # 🔴 كم جلسةً أخفينا؟ حين يسقط راوترٌ **واحد** من عدّة راوترات تبقى
            #    القائمة عامرةً فلا تظهر شارةُ «غير متصل» (شرطُها أن تفرغ
            #    القائمة) — فيرى المشغّل عددًا ناقصًا بلا أيّ تفسير ويظنّه عطبًا.
            #    مُبلَّغٌ من الإنتاج: راوترٌ حيٌّ أُعلن غيرَ مقروءٍ فاختفت
            #    13 جلسةً من الصفحة وأصحابُها متّصلون.
            hidden_sessions = max(0, before - len(items))
            router_unreachable = bool(unreachable_routers) or (
                before > 0 and not items)
    except Exception:  # noqa: BLE001
        pass

    if items:
        # التمييز مشترك/بطاقة عبر resolve_real_types على أسماء الجلسات الظاهرة
        # فقط (استعلام IN صغير). كان يُبنى من list_cards(limit=10000) الأحدث
        # أوّلًا — مستأجر لديه >10000 بطاقة (مثلاً 16,499 مرحَّلة) تسقط بطاقاته
        # الأقدم خارج المجموعة فتظهر في تبويب «المشتركون» («الكروت مع
        # المشتركين»). المُحلّل يقرأ جدولَي cards وsubscribers (user_type)
        # مباشرة بلا سقف، وهو نفسه مصدر عزل FIX A فيتطابق التبويب مع العدّ.
        try:
            from ..services.live_sessions import resolve_real_types
            kind_by_username = resolve_real_types(
                _tid(), [it.username for it in items if it.username])
        except Exception:
            kind_by_username = None  # فشل lookup → fallback لعرض الكل
        if kind_by_username is not None:
            if filter_type == "card":
                items = [it for it in items
                         if kind_by_username.get(it.username) == "card"]
            else:
                items = [it for it in items
                         if kind_by_username.get(it.username) != "card"]

    nas_options = sorted({it.nas_address for it in items if it.nas_address})
    plan_options = sorted({it.plan_name for it in items if it.plan_name})
    # MT85 — لون العرض في العمود. المشغّل يختار لونًا لكلّ باقة عند إنشائها،
    # ثمّ يرى هنا كلّ الأسماء بلونٍ واحد فلا يُميّز «أسبوعي» من «شهري» إلا
    # بالقراءة. اللون مخزَّن أصلًا (access_plans.color) — ينقصه الوصولُ فقط.
    # خريطةٌ بالمعرّف لا حقلٌ جديد على عنصر الجلسة: أقلّ مساسًا بالمسار الحارّ.
    plan_colors: dict = {}
    try:
        from ..db.repos import plans_repo as _plans_repo
        for _p in _plans_repo.list_plans(_tid(), limit=1000):
            plan_colors[_p.id] = _p.color or "#2BAACC"
    except Exception:  # noqa: BLE001 — اللون زينة، لا يُسقط الصفحة
        plan_colors = {}
    # عنوان NAS → اسم البرج الودّي (nas_devices): يُعرض «الاسم (IP)» في فلتر
    # السيرفر وصفوف الجدول بدل الـIP وحده، بنفس معالجة صفحة الإحصائيات.
    try:
        from ..services.nas_names import nas_name_map
        nas_name_by_ip = nas_name_map(_tid())
    except Exception:
        nas_name_by_ip = {}
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

    # بحث حرّ داخل «المتصلون الآن»: يطابق اسم الدخول/الاسم/الجوال/MAC/IP/الباقة/الراوتر
    # (تطابق جزئيّ غير حسّاس لحالة الأحرف). خادميّ ليتّسق مع بقيّة الفلاتر.
    if search_q:
        def _q_match(it) -> bool:
            hay = " ".join(
                str(v or "").lower()
                for v in (
                    getattr(it, "username", ""),
                    getattr(it, "full_name", ""),
                    getattr(it, "phone", ""),
                    getattr(it, "mac_address", ""),
                    getattr(it, "ip_address", ""),
                    getattr(it, "plan_name", ""),
                    getattr(it, "nas_address", ""),
                )
            )
            return search_q in hay
        items = [it for it in items if _q_match(it)]

    # #2: surface Called-Station-Id (hotspot-server / interface name) per
    # session. radacct stores it as `calledstationid` (read by card_checker but
    # not by the live list). We key by acctsessionid so the template can show
    # the interface name in «منفذ الاتصال» instead of the numeric nas_port_id.
    called_station_by_session: dict[str, str] = {}
    try:
        from ..db.connection import db as _db
        session_ids = [it.session_id for it in items if it.session_id]
        if session_ids:
            chunk = ",".join("?" for _ in session_ids)
            rows = _db().execute(
                f"""
                SELECT acctsessionid, calledstationid
                  FROM radacct
                 WHERE tenant_id = ?
                   AND acctstoptime IS NULL
                   AND acctsessionid IN ({chunk})
                """,
                (_tid(), *session_ids),
            ).fetchall()
            for r in rows:
                cs = (r["calledstationid"] or "").strip()
                if cs:
                    called_station_by_session[r["acctsessionid"]] = cs
    except Exception:
        called_station_by_session = {}

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

    # اعكس «تقسيم السرعة على الأجهزة» في عمود «السرعة الحالية»: لكلّ مشترك مفعِّل
    # التقسيم، تُقسَّم سرعته المعروضة على عدد جلساته الحيّة الظاهرة — تمامًا كما
    # يقسمها الإنفاذ عبر CoA. عمود «سرعة الباقة» يبقى كاملًا. استعلام دفعة واحد
    # لأعلام equal_share؛ بلا استدعاء لكلّ صفّ. محصّن — لا يكسر الصفحة.
    try:
        import dataclasses as _dc
        from collections import Counter as _Counter
        from ..db.connection import db as _dbc
        from ..services.bandwidth_rate import SPLIT_MIN_KBPS as _MINK
        _unames = [it.username for it in items if it.username]
        if _unames:
            _uniq = list(set(_unames))
            _ph = ",".join("?" for _ in _uniq)
            _rows = _dbc().execute(
                f"SELECT username, equal_share_download, equal_share_upload "
                f"FROM subscribers WHERE tenant_id=? AND username IN ({_ph})",
                (_tid(), *_uniq),
            ).fetchall()
            _split = {r["username"]: (bool(r["equal_share_download"]),
                                      bool(r["equal_share_upload"])) for r in _rows}
            _counts = _Counter(_unames)
            _new = []
            for it in items:
                dsp, usp = _split.get(it.username, (False, False))
                n = _counts.get(it.username, 1)
                if n > 1 and (dsp or usp):
                    rd = (max(_MINK, (it.rate_down_kbps or 0) // n)
                          if dsp and it.rate_down_kbps else it.rate_down_kbps)
                    ru = (max(_MINK, (it.rate_up_kbps or 0) // n)
                          if usp and it.rate_up_kbps else it.rate_up_kbps)
                    it = _dc.replace(it, rate_down_kbps=rd, rate_up_kbps=ru)
                _new.append(it)
            items = _new
    except Exception:  # noqa: BLE001
        pass

    # عمود «وقت اليوم»: «المُستهلَك / الإجماليّ» كشارة ملوّنة بالأثلاث
    # (المالك: «ثلث المدة أخضر، ثلثين أصفر، آخر ثلث أحمر»؛ بلا حدّ = رمادية
    # حيادية «/ ∞»). الإجماليّ بحسب النوع: بطاقة = ميزانية وقت حزمتها (نفس
    # مصدر فاحص الكروت — card_accounting)؛ مشترك = الحدّ اليوميّ/الإجماليّ
    # الفعّال (نفس منطق الإنفاذ). Latin unit letters عبر core.duration_fmt
    # (bidi-safe). محصّن: أيّ فشل → {} والعمود يُصيَّر «—».
    try:
        from ..services.online_time_budget import day_time_cells
        daily_time = day_time_cells(
            _tid(), items, card_view=(filter_type == "card"))
    except Exception:  # noqa: BLE001 — لا تَكسر الصفحة بسبب العمود
        daily_time = {}

    return render_template(
        "radius/sessions_list.html",
        items=items,
        daily_time=daily_time,
        settings=settings,
        error=error,
        filter_type=filter_type,
        nas_options=nas_options,
        nas_name_by_ip=nas_name_by_ip,
        plan_options=plan_options,
        plan_colors=plan_colors,
        selected_nas=selected_nas,
        selected_plan=selected_plan,
        selected_speed=selected_speed,
        selected_group_id=selected_group_id,
        search_q=search_q,
        group_options=group_options,
        device_by_mac=device_by_mac,
        called_station_by_session=called_station_by_session,
        temp_speed_state_by_username=temp_speed_state_by_username,
        router_unreachable=router_unreachable,
        unreachable_routers=unreachable_routers,
        hidden_sessions=hidden_sessions,
        reach_by_ip=reach_by_ip,
        now=now,
    )


def online_live_status():
    """إشارة JSON خفيفة للواجهة: «المتصلون الآن» الحيّ + قابليّة وصول الراوترات.

    تُمكّن الواجهة من استطلاع الحالة (مثلاً كلّ بضع ثوانٍ) فتُظهر «الراوتر غير
    متصل» وتُصفّر العدّاد فور الانقطاع، وتُحدّثه فور العودة — دون إعادة تحميل
    الصفحة. تَستطلع الراوترات بمهلة قصيرة وتُصالح القابل للوصول (أفضل-جهد)."""
    from ..services import connected_live
    try:
        connected_live.refresh_and_reconcile(_tid())
    except Exception:  # noqa: BLE001
        pass
    # إنفاذ انتهاء السرعة المؤقتة هنا (لا-متزامن) بدل مسار تصيير الصفحة — يَدفع
    # revert-CoA للجلسة الجارية فور فتح الصفحة دون حجب تحميلها.
    try:
        from ..services.temp_speed import expire_due_temp_speeds
        expire_due_temp_speeds(tenant_id=_tid(), now=datetime.utcnow())
    except Exception:  # noqa: BLE001
        pass
    info = connected_live.connected_count(_tid(), real_only=True)
    return jsonify({
        "connected": int(info.get("count") or 0),
        "source": info.get("source"),
        "reachable": info.get("reachable"),
        "unreachable_routers": info.get("unreachable_routers") or [],
    })


def online_reconcile():
    """«مصالحة الجلسات الآن» — تنظيف فوريّ للجلسات اليتيمة لهذا المستأجر.

    يُغلق الصفوف المفتوحة الزومبي (لا interim ضمن المهلة، أو غائبة عن مجموعة
    الجلسات الحيّة على راوتر قابل للوصول) عبر مسار الإغلاق القانوني — فيتطابق
    العدّاد مع القائمة الحيّة فورًا بدل الانتظار لدورة العامل الخلفيّة.
    آمن للتكرار (idempotent): الراوتر غير القابل للوصول لا تُقتَل جلساته إلّا
    بقاعدة المهلة.
    """
    from ..services import session_reconciler
    try:
        stats = session_reconciler.reconcile_now(_tid())
    except Exception as e:  # noqa: BLE001
        flash(f"تعذّرت مصالحة الجلسات: {e}", "error")
        return _return_to_online()
    closed = int(stats.get("closed_total") or 0)
    if closed:
        flash(
            f"تمّت المصالحة: أُغلقت {closed} جلسة يتيمة "
            f"(حيّة: {stats.get('live_closed', 0)}، مهلة: {stats.get('interim_closed', 0)}).",
            "success",
        )
    else:
        flash("تمّت المصالحة: لا جلسات يتيمة — العدّاد مطابق للجلسات الحيّة.",
              "success")
    return _return_to_online()


def online_disconnect():
    # تحديد متعدد: قد يصل أكثر من زوج username/session_id من شريط الإجراءات.
    usernames = [u.strip() for u in request.form.getlist("username")]
    session_ids = [s.strip() for s in request.form.getlist("session_id")]
    pairs = [
        (u, (s or None))
        for u, s in zip(usernames, session_ids or [""] * len(usernames))
        if u
    ]
    if not pairs and usernames:
        pairs = [(u, None) for u in usernames if u]
    if not pairs:
        flash("اسم المستخدم مطلوب", "error")
        return redirect(url_for("radius.online_list"))

    svc = get_online_sessions_service()
    ok, failed = [], []
    for username, session_id in pairs:
        try:
            svc.disconnect(actor=_actor(), username=username, session_id=session_id)
            ok.append(username)
        except RadiusError as e:
            failed.append(f"{username}: {e.message or 'تعذّر قطع الجلسة'}")
    if ok:
        ok_names = "، ".join(ok)
        flash(
            f"تم إرسال أمر قطع الجلسة لـ {ok[0]}." if len(ok) == 1
            else f"تم إرسال أمر قطع الجلسة لـ {len(ok)} جلسات: {ok_names}.",
            "success",
        )
    if failed:
        # تأطير الفشل: الراوتر المنقطع لا يَستقبل CoA — نُبيّن السبب بوضوح
        # ونُرشد إلى «الإغلاق الإجباري» الذي يُزيل الجلسة من العدّاد رغم تعذّر
        # الفصل الحيّ. (الحالة الحيّة للراوتر مصدر التأطير — لا نَخمّن.)
        unreachable = []
        try:
            from ..services import connected_live
            unreachable = connected_live.unreachable_router_labels(_tid())
        except Exception:  # noqa: BLE001
            unreachable = []
        if unreachable:
            flash(
                "الراوتر غير متصل — تعذّر الفصل ("
                + "، ".join(unreachable)
                + "). استخدم «الإغلاق الإجباري» لإزالة الجلسة من العدّاد.",
                "error",
            )
        else:
            flash("تعذّر قطع بعض الجلسات — " + " | ".join(failed)
                  + " — يمكنك «الإغلاق الإجباري» لإزالتها من العدّاد.", "error")
    return _return_to_online()


def online_force_close():
    """«إغلاق إجباري» — يَكتب acctstoptime عبر مسار الإغلاق القانوني
    (session_reconciler.force_close) فتَختفي الجلسة من العدّاد ومن القائمة
    الحيّة، حتى حين تعذّر تسليم CoA Disconnect (الراوتر منقطع). لا يَلمس
    الراوتر — إغلاق محاسبيّ في radacct فقط، آمن متعدّد المستأجرين.
    """
    usernames = [u.strip() for u in request.form.getlist("username")]
    session_ids = [s.strip() for s in request.form.getlist("session_id")]
    pairs = [
        (u, (s or None))
        for u, s in zip(usernames, session_ids or [""] * len(usernames))
        if u
    ]
    if not pairs and usernames:
        pairs = [(u, None) for u in usernames if u]
    if not pairs:
        flash("اسم المستخدم مطلوب", "error")
        return redirect(url_for("radius.online_list"))

    from ..services import session_reconciler
    from ..services.audit import get_audit_service
    from ..core.constants import AUDIT_ACTION_DISCONNECT
    closed_total = 0
    for username, session_id in pairs:
        try:
            n = session_reconciler.force_close(
                _tid(), username, session_id=session_id,
                cause=session_reconciler.CAUSE_FORCE)
            closed_total += int(n or 0)
            if n:
                # تدقيق: نوع الإجراء نفسه (disconnect) مع وسم force-close.
                get_audit_service().record(
                    actor=_actor(),
                    action=AUDIT_ACTION_DISCONNECT,
                    target_type="session",
                    target_id=username,
                    payload={"session_id": session_id or "", "mode": "force_close"},
                )
        except Exception as e:  # noqa: BLE001
            flash(f"تعذّر الإغلاق الإجباري لـ {username}: {e}", "error")
    if closed_total:
        flash(
            f"تمّ الإغلاق الإجباري: أُزيلت {closed_total} جلسة من العدّاد "
            "(لم يُرسَل أمر فصل للراوتر).",
            "success",
        )
    else:
        flash("لا جلسة مفتوحة مطابقة — قد تكون أُغلقت سلفًا.", "info")
    return _return_to_online()


def online_lock_mac():
    # تحديد متعدد: نثبّت MAC لكل جلسة محددة على حدة ونجمع النتائج.
    try:
        pairs = _selected_online_pairs()
    except RadiusError as e:
        flash(e.message or "حدد جلسة أولًا.", "error")
        return _return_to_online()

    ok, failed = [], []
    for username, session_id in pairs:
        try:
            row = _selected_online_row(username, session_id)
            mac = _normalise_mac(row["callingstationid"] or "")
            if row["card_id"]:
                from ..db.repos import cards_repo

                if not cards_repo.set_card_locked_mac(
                    _tid(), int(row["card_id"]), mac, actor=_actor()
                ):
                    raise RadiusError("تعذّر تثبيت MAC للبطاقة.")
            else:
                from ..services.users import get_users_service

                svc = get_users_service()
                sub = svc.get(username)
                svc.update(actor=_actor(), sub=replace(sub, mac_lock=mac, allowed_macs=mac))
            ok.append(f"{username} ({mac})")
        except RadiusError as e:
            failed.append(f"{username}: {e.message or 'تعذّر تثبيت MAC'}")
    if ok:
        ok_names = "، ".join(ok)
        flash(
            f"تم تثبيت MAC على {ok[0]}." if len(ok) == 1
            else f"تم تثبيت MAC على {len(ok)}: {ok_names}.",
            "success",
        )
    if failed:
        flash("تعذّر تثبيت MAC للبعض — " + " | ".join(failed), "error")
    return _return_to_online()


def online_lock_ip():
    # تحديد متعدد: نثبّت IP لكل جلسة محددة (مشتركون فقط) ونجمع النتائج.
    try:
        pairs = _selected_online_pairs()
    except RadiusError as e:
        flash(e.message or "حدد جلسة أولًا.", "error")
        return _return_to_online()

    ok, failed = [], []
    for username, session_id in pairs:
        try:
            row = _selected_online_row(username, session_id)
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
            ok.append(f"{username} ({ip})")
        except RadiusError as e:
            failed.append(f"{username}: {e.message or 'تعذّر تثبيت IP'}")
    if ok:
        ok_names = "، ".join(ok)
        flash(
            f"تم تثبيت IP على {ok[0]}." if len(ok) == 1
            else f"تم تثبيت IP على {len(ok)}: {ok_names}.",
            "success",
        )
    if failed:
        flash("تعذّر تثبيت IP للبعض — " + " | ".join(failed), "error")
    return _return_to_online()


def _apply_temp_speed_request(force_mode: str | None):
    """Shared body for the temp-speed apply routes. ``force_mode``:
    None → use the configured mode (default live_coa, a rate-CoA that changes
    the speed with NO disconnect); "disconnect_reauth" → the manual
    «تطبيق بالفصل وإعادة الاتصال» button (PoD then re-auth). Never
    auto-disconnects on a failed CoA — that path is operator-triggered only."""
    try:
        row = _selected_online_row()
        username = row["username"]
        if row["card_id"]:
            raise RadiusError("السرعة المؤقتة متاحة للمشتركين فقط.")
        _dur = _int_or_zero(request.form.get("duration")
                            or request.form.get("duration_minutes"))
        _unit = (request.form.get("duration_unit") or "").strip().lower()
        if _unit in ("hours", "hour", "ساعات", "ساعة", "h"):
            _dur *= 60
        from ..services.temp_speed import (
            apply_temp_speed, MODE_DISCONNECT_REAUTH)
        try:
            result = apply_temp_speed(
                tenant_id=_tid(),
                actor=_actor(),
                username=username,
                down_kbps=_int_or_zero(request.form.get("down_kbps")),
                up_kbps=_int_or_zero(request.form.get("up_kbps")),
                duration_minutes=_dur,
                force_mode=force_mode,
            )
        except ValueError as exc:
            raise RadiusError(str(exc)) from exc

        ok = result["coa"].get("ok")
        code = result["coa"].get("code") or "no_coa"
        mode = result.get("mode")
        if mode == MODE_DISCONNECT_REAUTH:
            # PoD path — the user is disconnected and reconnects with the new
            # rate from the DB. "no_active_session" here is benign.
            if ok:
                flash(f"طُبِّقت السرعة المؤقتة ({result['rate']}) على {username} "
                      f"بالفصل وإعادة الاتصال — سيعود بالسرعة الجديدة خلال ثوانٍ "
                      f"(حتى {result['ends_at']}).", "success")
            elif code == "no_active_session":
                flash(f"حُفظت السرعة المؤقتة ({result['rate']}) لـ {username} — "
                      f"لا جلسة نشطة الآن؛ ستُطبَّق تلقائيًا عند إعادة الاتصال.",
                      "info")
            else:
                flash(f"حُفظت السرعة المؤقتة ({result['rate']}) لـ {username}، "
                      f"لكن تعذّر الفصل ({code}) — تحقّق من اتصال الراوتر.",
                      "warning")
        else:
            # live_coa (default) — a live rate change with NO disconnect.
            if ok:
                flash(f"تم تطبيق السرعة المؤقتة ({result['rate']}) على {username} "
                      f"مباشرةً عبر CoA — بدون فصل المستخدم (حتى {result['ends_at']}).",
                      "success")
            elif code == "no_active_session":
                flash(f"حُفظت السرعة المؤقتة ({result['rate']}) لـ {username} — "
                      f"لا جلسة نشطة الآن؛ ستُطبَّق تلقائيًا فور إعادة اتصاله.",
                      "info")
            elif code == "empty_rate":
                flash("لم تُحدَّد سرعة صالحة للإرسال.", "warning")
            else:
                # CoA reached the router but was not confirmed. We do NOT
                # disconnect automatically — offer the manual force button.
                flash(f"حُفظت السرعة المؤقتة ({result['rate']}) لـ {username} حتى "
                      f"{result['ends_at']}، لكن الراوتر لم يؤكّد تطبيق CoA "
                      f"({code}). لم يُفصل المستخدم. إن لم تتغيّر سرعته، استخدم "
                      f"زر «تطبيق بالفصل وإعادة الاتصال». (تحقّق أيضًا من CoA: "
                      f"المنفذ 3799 والـ secret).", "warning")
    except RadiusError as e:
        flash(e.message or "تعذّر تطبيق السرعة المؤقتة", "error")
    return _return_to_online()


def online_temp_speed():
    """Apply a temporary speed to the selected session — LIVE via CoA (no
    disconnect) by default; honours the temporary_speed_apply_mode setting.
    Subscribers only; audited in the service; gated by ``users.edit``."""
    return _apply_temp_speed_request(force_mode=None)


def online_temp_speed_reauth():
    """Optional «تطبيق بالفصل وإعادة الاتصال» — the operator-triggered PoD
    fallback for when a live rate-CoA didn't take on the router. Persists the
    temp speed then disconnects so the user re-auths with the new rate. Never
    invoked automatically. Gated by ``users.edit``."""
    from ..services.temp_speed import MODE_DISCONNECT_REAUTH
    return _apply_temp_speed_request(force_mode=MODE_DISCONNECT_REAUTH)


def online_temp_speed_cancel():
    # ملاحظة: يعمل على أول جلسة محددة فقط (إجراء فردي).
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


# ── Live CoA control (RFC 5176) ─────────────────────────────────────
# Owner-triggered, one packet per click. NO background mutation.
# Errors surface verbatim via flash() — never fake success.


def _coa_collect_session_args() -> tuple[str, str]:
    username = (request.form.get("username") or "").strip()
    session_id = (request.form.get("session_id") or "").strip()
    return username, session_id


def _record_live(outcome, *, username: str, after: dict | None = None,
                 before: dict | None = None) -> None:
    """Persist a live-CoA outcome to the unified MikroTik-actions feed
    (fail-safe — never breaks the request)."""
    try:
        from ..services.mt_action_log import record_live_outcome
        record_live_outcome(outcome, actor=_actor(), username=username,
                            tenant_id=_tid(), before=before or {},
                            after=after or {})
    except Exception:  # noqa: BLE001
        pass


def _current_rate_limit(username: str) -> str:
    """Best-effort REAL current Mikrotik-Rate-Limit for a subscriber →
    «<up>k/<down>k», or "" when unknown. Never fabricates 0 (owner: «ياخذ
    السرعة الحالية الحقيقية»): a subscriber override wins, else the plan rate;
    if neither is known we return "" so the feed shows «غير معروف», not «0»."""
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT download_speed_kbps, upload_speed_kbps, "
            "bandwidth_control_enabled, plan_id FROM subscribers "
            "WHERE tenant_id=? AND username=? LIMIT 1",
            (_tid(), username)).fetchone()
        if not row:
            return ""
        down = int(row["download_speed_kbps"] or 0)
        up = int(row["upload_speed_kbps"] or 0)
        if row["bandwidth_control_enabled"] and (down or up):
            return f"{up}k/{down}k"
        pid = row["plan_id"]
        if pid:
            pr = db().execute(
                "SELECT speed_down_kbps, speed_up_kbps FROM access_plans "
                "WHERE tenant_id=? AND id=? LIMIT 1", (_tid(), pid)).fetchone()
            if pr and (pr["speed_down_kbps"] or pr["speed_up_kbps"]):
                return f"{int(pr['speed_up_kbps'] or 0)}k/{int(pr['speed_down_kbps'] or 0)}k"
    except Exception:  # noqa: BLE001
        pass
    return ""


def online_coa_set_ip():
    """Action (a) — تغيير IP المواقع. PPPoE only (hotspot surfaces unsupported)."""
    from ..services.live_session_control import change_ip_live
    username, session_id = _coa_collect_session_args()
    new_ip = (request.form.get("new_ip") or "").strip()
    if not username or not new_ip:
        flash("اسم المستخدم والـIP الجديد مطلوبان", "error")
        return _return_to_online()
    try:
        out = change_ip_live(
            tenant_id=_tid(), username=username,
            new_ip=new_ip, session_id=session_id,
        )
    except ValueError as e:
        flash(f"قيمة غير صالحة: {e}", "error")
        return _return_to_online()
    # Gap capture — persist the live CoA outcome (router + result) to the
    # unified MikroTik-actions feed so it is complete going forward.
    _record_live(out, username=username, after={"framed_ip": new_ip})
    if out.ok:
        flash(
            f"تم تغيير IP لـ {username} إلى {new_ip} على المايكروتيك/السيرفر "
            f"{out.nas_ip} — {out.code_name}.",
            "success",
        )
    else:
        flash(
            f"فشل تغيير IP لـ {username}: {out.code_name}"
            + (f" — {out.reply_message}" if out.reply_message else "")
            + (f" ({out.detail})" if out.detail else ""),
            "error",
        )
    return _return_to_online()


def online_coa_set_speed():
    """Action (b) — تطبيق سرعة حيّة عبر CoA (Mikrotik-Rate-Limit).
    Works for both PPPoE and hotspot (per-session match key).
    """
    from ..services.live_session_control import change_speed_live
    username, session_id = _coa_collect_session_args()
    try:
        rx = int((request.form.get("rx_kbps") or "0").strip())
        tx = int((request.form.get("tx_kbps") or "0").strip())
    except (TypeError, ValueError):
        flash("rx_kbps و tx_kbps يجب أن تكون أرقامًا", "error")
        return _return_to_online()
    if not username:
        flash("اسم المستخدم مطلوب", "error")
        return _return_to_online()
    try:
        out = change_speed_live(
            tenant_id=_tid(), username=username,
            rx_kbps=rx, tx_kbps=tx, session_id=session_id,
        )
    except ValueError as e:
        flash(f"قيمة غير صالحة: {e}", "error")
        return _return_to_online()
    # Gap capture — persist the live speed-change outcome (router + from→to +
    # result). The «from» is the REAL current rate read before the push (or ""
    # → «غير معروف» in the feed), never a fabricated 0.
    _before_rate = _current_rate_limit(username)
    _record_live(out, username=username,
                 before={"rate_limit": _before_rate} if _before_rate else {},
                 after={"rate_limit": f"{tx}k/{rx}k"})
    if out.ok:
        flash(
            f"تم تطبيق السرعة {rx}k/{tx}k على {username} (الجلسة {out.session_id}) "
            f"— {out.code_name}.",
            "success",
        )
    else:
        flash(
            f"فشل تطبيق السرعة على {username}: {out.code_name}"
            + (f" — {out.reply_message}" if out.reply_message else ""),
            "error",
        )
    return _return_to_online()
