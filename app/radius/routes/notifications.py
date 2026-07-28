"""مركز الإشعارات الموحّد + قناة التواصل مع لوحة التراخيص (واجهة المشغّل).

الصفحة: قائمة كل الإشعارات (فلتر الكل/غير المقروء) + علِّم كمقروء / علِّم
الكل + نموذج «تواصل مع المزوّد» (تذكرة/شكوى تصل اللوحة عبر الجسر). الجرس في
شريط الأعلى يُغذّى من topbar_notifications (سياق) ويربط كل عنصر لهدفه.
"""
from __future__ import annotations

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from datetime import datetime, timezone

from ..auth.session_helpers import current_admin
from ..core.system_config import _coerce_dt, to_local
from ..db.repos import notifications_repo, provider_messages_repo
from ..services.provider_comms import ProviderCommsService


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _ar_unit(n: int, one: str, two: str, few: str, many: str) -> str:
    """Arabic count phrasing (1 / 2 / 3-10 / 11+)."""
    if n == 1:
        return one
    if n == 2:
        return two
    if 3 <= n <= 10:
        return f"{n} {few}"
    return f"{n} {many}"


def _humanize_rel(value, now: datetime | None = None) -> str:
    """Friendly Arabic relative time («منذ ٦ دقائق»). Falls back to the
    localized absolute date for anything older than ~30 days so old rows
    stay readable. Never raises — bad input returns ''."""
    dt = _coerce_dt(value)
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    secs = (now - dt).total_seconds()
    if secs < 0:
        secs = 0
    if secs < 60:
        return "الآن"
    mins = int(secs // 60)
    if mins < 60:
        return "منذ " + _ar_unit(mins, "دقيقة", "دقيقتين", "دقائق", "دقيقة")
    hours = int(secs // 3600)
    if hours < 24:
        return "منذ " + _ar_unit(hours, "ساعة", "ساعتين", "ساعات", "ساعة")
    days = int(secs // 86400)
    if days <= 30:
        return "منذ " + _ar_unit(days, "يوم", "يومين", "أيام", "يومًا")
    return to_local(value, fmt="%Y-%m-%d")


def register_notifications_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/notifications", "notifications_center",
                    notifications_center, methods=["GET"])
    bp.add_url_rule("/notifications/timeline", "notifications_timeline",
                    notifications_timeline, methods=["GET"])
    bp.add_url_rule("/notifications/poll", "notifications_poll",
                    notifications_poll, methods=["GET"])
    bp.add_url_rule("/notifications/<int:notif_id>/open", "notification_open",
                    notification_open, methods=["GET"])
    bp.add_url_rule("/notifications/<int:notif_id>/read", "notification_read",
                    notification_read, methods=["POST"])
    bp.add_url_rule("/notifications/read-all", "notifications_read_all",
                    notifications_read_all, methods=["POST"])
    bp.add_url_rule("/notifications/contact", "notifications_contact",
                    notifications_contact, methods=["POST"])
    bp.add_url_rule("/notifications/test-push", "notifications_test_push",
                    notifications_test_push, methods=["POST"])
    # MT90 — أصوات الإشعارات المخصّصة
    bp.add_url_rule("/notifications/sounds", "notification_sounds_page",
                    notification_sounds_page, methods=["GET"])
    bp.add_url_rule("/notifications/sound.audio", "notification_sound_audio",
                    notification_sound_audio, methods=["GET"])
    bp.add_url_rule("/notifications/sounds/save", "notification_sound_save",
                    notification_sound_save, methods=["POST"])
    bp.add_url_rule("/notifications/sounds/clear", "notification_sound_clear",
                    notification_sound_clear, methods=["POST"])


def notifications_center():
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    tid = _tid()
    unread_only = (request.args.get("filter") == "unread")
    items = notifications_repo.list_for(tid, unread_only=unread_only, limit=200)
    # Enrich each row with a friendly relative time + a localized absolute
    # tooltip so the template never has to print the raw ISO timestamp.
    _now = datetime.now(timezone.utc)
    for _n in items:
        _n["created_rel"] = _humanize_rel(_n.get("created_at"), _now)
        _n["created_local"] = to_local(_n.get("created_at"), tenant_id=tid)
    from ..services import notifications as notif_svc
    return render_template(
        "radius/notifications_center.html",
        items=items,
        unread_count=notifications_repo.unread_count(tid),
        unread_only=unread_only,
        provider_messages=provider_messages_repo.list_for(tid, limit=20),
        push=notif_svc.push_status(tid),
    )


def notifications_timeline():
    """سجل الإشعارات — عرض موحّد: أُرسِلت / بالانتظار / فشل + المجدولة القادمة
    (تذكيرات قرب الانتهاء) حتى أسبوع. للقراءة فقط."""
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    from ..services import notification_timeline as ntl
    try:
        within = max(1, min(30, int(request.args.get("days") or 7)))
    except (TypeError, ValueError):
        within = 7
    data = ntl.build_timeline(_tid(), scheduled_within_days=within)
    return render_template(
        "radius/notifications_timeline.html",
        data=data,
        counts=data["counts"],
        scheduled=data["scheduled"],
        within_days=within,
        active="timeline",
    )


def notifications_poll():
    """استطلاع خفيف لجرسَي الأعلى (تنبيهات + إشعارات) — مصادقة الجلسة، بلا
    توكن. يستدعيه JS دوريًّا كي تصل الإشعارات بلا تحديث الصفحة، ويقارن
    JS العدّ الجديد بالسابق ليُشغّل الصوت عند وصول جديد. لا يكسر أبدًا."""
    if not current_admin():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    tid = _tid()
    # التنبيهات الذكية المفتوحة (جرس «مركز التنبيهات»).
    alerts_count, alerts_items = 0, []
    try:
        from ..db.repos import alerts_repo
        rows = alerts_repo.list_open(tid, limit=50)
        alerts_count = len(rows)
        alerts_items = [{
            "id": int(r["id"]),
            "title": r.get("title_ar") or "",
            "severity": r.get("severity") or "info",
        } for r in rows[:6]]
    except Exception:  # noqa: BLE001 — الاستطلاع لا يكسر أبدًا
        alerts_count, alerts_items = 0, []
    # مركز الإشعارات الموحّد (جرس «الظرف»).
    notif_count, notif_items = 0, []
    try:
        from ..services import notifications as notif_svc
        notif_count = int(notif_svc.unread_count(tid))
        notif_items = [{
            "id": n.get("id"),
            "title": n.get("title") or "",
            "severity": n.get("severity") or "info",
            "is_read": bool(n.get("is_read")),
            "link": n.get("link") or "",
            # MT90 — مفتاح الحدث ونوعه: بهما يعرف JS أيّ صوتٍ يطلب. الأقدم
            # بلا مفتاح يسقط على صوت النوع ثمّ العامّ ثمّ النغمة.
            "event": n.get("event_key") or "",
            "type": n.get("type") or "",
        } for n in notif_svc.recent_for_bell(tid, limit=6)]
    except Exception:  # noqa: BLE001
        notif_count, notif_items = 0, []
    return jsonify({
        "ok": True,
        "alerts": {"count": alerts_count, "items": alerts_items},
        "notif": {"count": notif_count, "items": notif_items},
    })


def notifications_test_push():
    """يُحوّل إشعار دفع تجريبيًّا إلى لوحة التراخيص (سلطة FCM المركزيّة) وتُرسله
    اللوحة لأجهزة العميل المُسجَّلة، ثم يُبلّغ النتيجة بضغطة.

    يُتيح للمالك التأكّد من سلسلة الدفع كاملةً (الراديوس → الجسر → لوحة
    التراخيص → FCM → الجهاز). يُميّز الفشل: لا أجهزة مسجّلة، أو الدفع غير
    مُفعَّل مركزيًّا، أو الجسر غير مُهيّأ."""
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    from ..services import notifications as notif_svc
    res = notif_svc.send_test_push(_tid())
    reason = res.get("reason") or ""
    if res.get("ok"):
        flash(f"تم تحويل الإشعار التجريبي للوحة التراخيص وإرساله إلى الأجهزة "
              f"المُسجَّلة (نجح {res.get('sent', 0)}، فشل {res.get('failed', 0)}). "
              f"تحقّق من جوّالك.", "success")
    elif reason == "no_tokens":
        flash("لا توجد أجهزة مُسجَّلة بعد. افتح التطبيق على جوّالك، سجّل "
              "الدخول، واسمح بالإشعارات — ثم أعد المحاولة.", "error")
    elif reason == "fcm_disabled":
        flash("دفع الجوال غير مُفعَّل مركزيًّا (لم يُرفَع اعتماد Firebase في لوحة "
              "التراخيص). أبلِغ المزوّد لتفعيله من إعدادات اللوحة.", "error")
    elif reason in ("https_required", "disabled", "config_missing", "unavailable",
                    "timeout"):
        flash("تعذّر الوصول إلى لوحة التراخيص لتحويل الإشعار. تأكّد من تهيئة "
              "ربط لوحة التراخيص ثم أعد المحاولة.", "error")
    else:
        flash("تعذّر إرسال الإشعار التجريبي. حاول مرة أخرى.", "error")
    return redirect(request.referrer or url_for("radius.notifications_center"))


def notification_open(notif_id: int):
    """يفتح إشعارًا: يُعلّمه مقروءًا ثم يُحوّل لهدفه (الرابط العميق)."""
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    tid = _tid()
    notif = notifications_repo.get(tid, notif_id)
    notifications_repo.mark_read(tid, notif_id)
    target = (notif or {}).get("link") or url_for("radius.notifications_center")
    # روابطنا داخلية نسبية فقط — لا نُحوّل لأي مضيف خارجي.
    if not str(target).startswith("/"):
        target = url_for("radius.notifications_center")
    return redirect(target)


def notification_read(notif_id: int):
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    notifications_repo.mark_read(_tid(), notif_id)
    flash("تم تعليم الإشعار كمقروء.", "success")
    return redirect(request.referrer or url_for("radius.notifications_center"))


def notifications_read_all():
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    n = notifications_repo.mark_all_read(_tid())
    flash(f"تم تعليم {n} إشعارًا كمقروء." if n else "لا إشعارات غير مقروءة.",
          "success" if n else "info")
    return redirect(request.referrer or url_for("radius.notifications_center"))


def notifications_contact():
    """يرفع تذكرة/شكوى للمزوّد عبر الجسر + يَحفظها محلّيًّا."""
    admin = current_admin()
    if not admin:
        return redirect(url_for("radius.auth_login"))
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    kind = (request.form.get("kind") or "ticket").strip()
    category = (request.form.get("category") or "general").strip()
    priority = (request.form.get("priority") or "normal").strip()
    if not subject:
        flash("اكتب موضوع الرسالة أولًا.", "error")
        return redirect(url_for("radius.notifications_center"))
    result = ProviderCommsService().submit_ticket(
        _tid(), subject=subject, body=body, kind=kind, category=category,
        priority=priority, created_by=str(getattr(admin, "username", "") or ""))
    if result.get("bridge_status") == "sent":
        flash("تم إرسال رسالتك إلى لوحة التراخيص.", "success")
    else:
        flash("حُفظت رسالتك محلّيًّا وستُرسَل عند توفّر الاتصال باللوحة.", "info")
    return redirect(url_for("radius.notifications_center"))


# ═══════════════════════════════════════════════════════════════════════
# MT90 — أصوات الإشعارات المخصّصة (صوتٌ مسجَّل بدل النغمة، لكلّ حدثٍ صوتُه)
# ═══════════════════════════════════════════════════════════════════════

def notification_sounds_page():
    """صفحة كل أنواع الإشعارات: رفع/تسجيل/معاينة/حذف صوتٍ لكلٍّ منها."""
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    from ..services import notification_sounds as snd
    tid = _tid()
    return render_template(
        "radius/notification_sounds.html",
        groups=snd.catalog(tid),
        global_sound=snd.status_map(tid).get(snd.GLOBAL_KEY),
        global_key=snd.GLOBAL_KEY,
        max_mb=snd.MAX_BYTES // (1024 * 1024),
    )


def notification_sound_audio():
    """يُرجع الصوت الأنسب للحدث المطلوب — أو 404 فيسقط JS على النغمة.

    404 هنا ليست خطأً بل **إشارة**: «لا صوت مخصّص، شغّل النغمة». لذلك لا
    تُسجَّل ولا تُزعج، وهي المسار الطبيعيّ قبل أن يرفع المالك أيّ صوت.
    """
    from flask import Response
    if not current_admin():
        return Response("", status=401)
    from ..services import notification_sounds as snd
    got = snd.resolve(_tid(),
                      event_key=(request.args.get("event") or "").strip(),
                      ntype=(request.args.get("type") or "").strip())
    if not got:
        return Response("", status=404)
    mime, raw = got
    resp = Response(raw, mimetype=mime or "audio/mpeg")
    # لا تخزين: تغيير الصوت في الصفحة يجب أن يُسمع فورًا لا بعد انتهاء كاش.
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _sounds_guard():
    """الأصوات إعدادٌ عامّ للوحة → صلاحية إعدادات النظام (أو المالك)."""
    from ..core.constants import PERM_SETTINGS_EDIT
    perms = set(session.get("permissions") or [])
    if session.get("is_super_admin") or PERM_SETTINGS_EDIT in perms:
        return None
    return jsonify({"ok": False, "message": "لا تملك صلاحية تعديل الإعدادات."}), 403


def notification_sound_save():
    if not current_admin():
        return jsonify({"ok": False, "message": "الجلسة منتهية."}), 401
    denied = _sounds_guard()
    if denied:
        return denied
    from ..services import notification_sounds as snd
    f = request.files.get("sound")
    if not f or not f.filename:
        return jsonify({"ok": False, "message": "لم يُرفَع أيّ ملفّ صوتيّ."}), 400
    ok, message = snd.save_sound(
        _tid(), (request.form.get("sound_key") or "").strip(), f.read(),
        mime=(f.mimetype or "audio/mpeg"), filename=f.filename, origin="local")
    return jsonify({"ok": ok, "message": message}), (200 if ok else 400)


def notification_sound_clear():
    if not current_admin():
        return jsonify({"ok": False, "message": "الجلسة منتهية."}), 401
    denied = _sounds_guard()
    if denied:
        return denied
    from ..services import notification_sounds as snd
    ok, message = snd.clear_sound(
        _tid(), (request.form.get("sound_key") or "").strip())
    return jsonify({"ok": ok, "message": message}), (200 if ok else 400)
