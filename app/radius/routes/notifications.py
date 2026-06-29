"""مركز الإشعارات الموحّد + قناة التواصل مع لوحة التراخيص (واجهة المشغّل).

الصفحة: قائمة كل الإشعارات (فلتر الكل/غير المقروء) + علِّم كمقروء / علِّم
الكل + نموذج «تواصل مع المزوّد» (تذكرة/شكوى تصل اللوحة عبر الجسر). الجرس في
شريط الأعلى يُغذّى من topbar_notifications (سياق) ويربط كل عنصر لهدفه.
"""
from __future__ import annotations

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)

from ..auth.session_helpers import current_admin, is_super_admin
from ..db.repos import notifications_repo, provider_messages_repo
from ..services.provider_comms import ProviderCommsService

# أقصى حجم معقول لملفّ حساب خدمة Firebase (نصّ JSON صغير ~2KB؛ نسمح هامشًا).
_MAX_CRED_BYTES = 64 * 1024


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
    bp.add_url_rule("/notifications/push-credential",
                    "notifications_push_credential",
                    notifications_push_credential, methods=["POST"])


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
    """يُرسِل إشعار دفع تجريبيًّا إلى أجهزة المستأجر المُسجَّلة ويُبلّغ النتيجة.

    يُتيح للمالك التأكّد من سلسلة الدفع كاملةً (الخادم → FCM → الجهاز) بضغطة.
    يُميّز الفشل: لا أجهزة مسجّلة، أو الدفع غير مُفعَّل على الخادم."""
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    from ..services import notifications as notif_svc
    res = notif_svc.send_test_push(_tid())
    reason = res.get("reason") or ""
    if res.get("ok"):
        flash(f"تم إرسال الإشعار التجريبي إلى الأجهزة المُسجَّلة "
              f"(نجح {res.get('sent', 0)}، فشل {res.get('failed', 0)}). "
              f"تحقّق من جوّالك.", "success")
    elif reason == "no_tokens":
        flash("لا توجد أجهزة مُسجَّلة بعد. افتح التطبيق على جوّالك، سجّل "
              "الدخول، واسمح بالإشعارات — ثم أعد المحاولة.", "error")
    elif reason in ("fcm_disabled", "import_failed"):
        flash("دفع الجوال غير مُفعَّل على الخادم (لم يُضبَط اعتماد Firebase). "
              "راجع دليل الإعداد docs/PUSH_FCM_SERVER_SETUP.md.", "error")
    else:
        flash("تعذّر إرسال الإشعار التجريبي. حاول مرة أخرى.", "error")
    return redirect(request.referrer or url_for("radius.notifications_center"))


def notifications_push_credential():
    """يَرفع ملفّ اعتماد Firebase (حساب الخدمة) ويُخزّنه على الخادم.

    بمجرّد الرفع يَلتقطه ``fcm_push`` تلقائيًّا فيُفعَّل الدفع دون أيّ خطوة
    خادم/طرفية. مقصور على المالك (السوبر). يَتحقّق أنّ الملفّ حساب خدمة حقيقيّ
    ويَرفض غيره. لا يُخزَّن إلّا ملفّ صالح، ولا يُعرَض محتواه أبدًا."""
    if not current_admin():
        return redirect(url_for("radius.auth_login"))
    if not is_super_admin():
        flash("رفع اعتماد الدفع مقصور على المالك.", "error")
        return redirect(url_for("radius.notifications_center"))

    file = request.files.get("credential")
    if file is None or not (file.filename or "").strip():
        flash("اختر ملفّ firebase-admin-sdk.json أولًا.", "error")
        return redirect(url_for("radius.notifications_center"))

    raw = file.read(_MAX_CRED_BYTES + 1)
    if len(raw) > _MAX_CRED_BYTES:
        flash("الملفّ أكبر من المتوقَّع لملفّ حساب خدمة. تأكّد من الملفّ الصحيح.",
              "error")
        return redirect(url_for("radius.notifications_center"))

    from ..auth.session_helpers import current_admin_id
    from app.services import fcm_credentials
    try:
        info = fcm_credentials.store_uploaded(raw, by=int(current_admin_id() or 0))
    except ValueError as exc:
        flash(f"ملفّ غير صالح: {exc}", "error")
        return redirect(url_for("radius.notifications_center"))
    except Exception:  # noqa: BLE001 — فشل تخزين غير متوقَّع
        flash("تعذّر حفظ الاعتماد على الخادم. حاول مرة أخرى.", "error")
        return redirect(url_for("radius.notifications_center"))

    flash(f"تم رفع اعتماد Firebase وتفعيل الدفع ✓ — المشروع "
          f"{info.get('project_id') or '—'}. جرّب «أرسل إشعار تجريبي».",
          "success")
    return redirect(url_for("radius.notifications_center"))


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
