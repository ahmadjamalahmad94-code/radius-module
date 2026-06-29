"""مركز الإشعارات الموحّد + قناة التواصل مع لوحة التراخيص (واجهة المشغّل).

الصفحة: قائمة كل الإشعارات (فلتر الكل/غير المقروء) + علِّم كمقروء / علِّم
الكل + نموذج «تواصل مع المزوّد» (تذكرة/شكوى تصل اللوحة عبر الجسر). الجرس في
شريط الأعلى يُغذّى من topbar_notifications (سياق) ويربط كل عنصر لهدفه.
"""
from __future__ import annotations

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from ..auth.session_helpers import current_admin
from ..db.repos import notifications_repo, provider_messages_repo
from ..services.provider_comms import ProviderCommsService


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def register_notifications_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/notifications", "notifications_center",
                    notifications_center, methods=["GET"])
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


def notifications_center():
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    tid = _tid()
    unread_only = (request.args.get("filter") == "unread")
    items = notifications_repo.list_for(tid, unread_only=unread_only, limit=200)
    from ..services import notifications as notif_svc
    return render_template(
        "radius/notifications_center.html",
        items=items,
        unread_count=notifications_repo.unread_count(tid),
        unread_only=unread_only,
        provider_messages=provider_messages_repo.list_for(tid, limit=20),
        push=notif_svc.push_status(tid),
    )


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
