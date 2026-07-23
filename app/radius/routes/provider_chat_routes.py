"""MT32 — مسارات شات المزوّد ↔ الشبكة.

طرفان، صفحتان، عزلٌ صارم:

* **المزوّد** (`/admin/radius/provider/chat`) — يرى كل الشبكات وخيوطها.
  محروسٌ بـ``_PERM_SUPER`` (لوحة المزوّد) في ``blueprint._PERM_GUARDED``.
* **الشبكة** (`/[slug]/admin/radius/support`) — خيطها هي فقط. الجهة
  تُؤخذ من ``g.tenant_id`` (الذي يحلّه حارس المسار) لا من الطلب، فلا
  يمكن لشبكةٍ قراءة خيط أخرى ولو خمّنت رقمها.
"""
from __future__ import annotations

from flask import (Blueprint, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

from ..auth.session_helpers import is_super_admin
from ..services import provider_chat


def register_provider_chat_routes(bp: Blueprint) -> None:
    # جانب المزوّد
    bp.add_url_rule("/provider/chat", "provider_chat_home",
                    provider_chat_home, methods=["GET"])
    bp.add_url_rule("/provider/chat/<int:tenant_id>", "provider_chat_thread",
                    provider_chat_thread, methods=["GET"])
    bp.add_url_rule("/provider/chat/<int:tenant_id>/send", "provider_chat_send",
                    provider_chat_send, methods=["POST"])
    bp.add_url_rule("/provider/chat/<int:tenant_id>/messages",
                    "provider_chat_poll", provider_chat_poll, methods=["GET"])
    # MT43 — تقديم مرفقات المحادثة (كلا الجانبين).
    bp.add_url_rule("/provider/chat/<int:tenant_id>/file/<int:message_id>",
                    "provider_chat_file", provider_chat_file, methods=["GET"])
    bp.add_url_rule("/support/file/<int:message_id>",
                    "network_support_file", network_support_file, methods=["GET"])
    # جانب الشبكة
    # MT40 — إشعارات المزوّد الحيّة (استطلاع خفيف من الشريط العلويّ).
    bp.add_url_rule("/provider/notifications", "provider_notifications",
                    provider_notifications, methods=["GET"])
    bp.add_url_rule("/support", "network_support_chat",
                    network_support_chat, methods=["GET"])
    bp.add_url_rule("/support/send", "network_support_send",
                    network_support_send, methods=["POST"])
    bp.add_url_rule("/support/messages", "network_support_poll",
                    network_support_poll, methods=["GET"])


def _tid() -> int:
    from ..core.tenant import DEFAULT_TENANT_ID
    return int(getattr(g, "tenant_id", None) or DEFAULT_TENANT_ID)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or ""


def _require_provider() -> None:
    """جانب المزوّد مقصورٌ على المالك الرئيسي — لا مالك شبكةٍ ولا مدير.

    حارسٌ ثانٍ داخل الدالة (إلى جانب ``_PERM_SUPER``) كي لا يعتمد سرّ
    مراسلات كل العملاء على قائمةٍ واحدة قد تُنسى عند إضافة مسار."""
    if not is_super_admin():
        abort(403)


# ─────────────── جانب المزوّد ───────────────

def provider_notifications():
    """MT40 — ما يَستحقّ جرسًا الآن: رسائل غير مقروءة + طلبات اشتراك
    معلّقة + تجارب توشك أن تنتهي.

    نقطةٌ خفيفة عمدًا (أعداد وعناوين قصيرة لا محتوى): يَستدعيها الشريط
    كل ٢٠ ثانية، فأيّ استعلامٍ ثقيل هنا يَصير ضريبةً دائمة على الخادم.
    وهي owner-only كبقيّة أسطح المزوّد — تُسرّب أسماء عملاء لو انفتحت.
    """
    _require_provider()
    from datetime import datetime
    from ..services.tenants import get_tenants_service

    items, now = [], datetime.utcnow()

    unread = provider_chat.unread_by_tenant() or {}
    if unread:
        names = {t.id: (t.display_name or t.name)
                 for t in get_tenants_service().list()}
        for tid, n in sorted(unread.items(), key=lambda kv: -kv[1])[:5]:
            items.append({
                "kind": "chat", "icon": "comments",
                "text": f"{n} رسالة غير مقروءة من «{names.get(tid, tid)}»",
                "url": url_for("radius.provider_chat_thread", tenant_id=tid),
            })

    pending = 0
    try:
        from ..db.repos import signup_requests_repo
        pending = signup_requests_repo.pending_count()
    except Exception:  # noqa: BLE001 — ترحيل ١٦٧ قد لا يكون طُبّق
        pending = 0
    if pending:
        items.append({
            "kind": "signup", "icon": "envelope-open-text",
            "text": f"{pending} طلب اشتراك ينتظر مراجعتك",
            "url": url_for("radius.provider_home"),
        })

    try:
        for t in get_tenants_service().list():
            if t.id == 1 or getattr(t, "status", "") != "trial":
                continue
            ends = getattr(t, "trial_ends_at", None)
            if not ends:
                continue
            left = (ends - now).days
            if left <= 3:
                items.append({
                    "kind": "trial", "icon": "hourglass-half",
                    "text": (f"تجربة «{t.display_name or t.name}» "
                             + ("تنتهي اليوم" if left <= 0 else f"تنتهي خلال {left} يوم")),
                    "url": url_for("radius.tenants_edit", tenant_id=t.id),
                })
    except Exception:  # noqa: BLE001
        pass

    return jsonify({
        "ok": True,
        "count": sum(unread.values()) + pending
                 + sum(1 for i in items if i["kind"] == "trial"),
        "items": items[:8],
    })


def provider_chat_home():
    _require_provider()
    from ..services.tenants import get_tenants_service
    unread = provider_chat.unread_by_tenant()
    last = provider_chat.last_activity_by_tenant()
    items = []
    # MT40 — الشبكة ١ هي مساحة المزوّد نفسه: إدراجها كان يَعرض عليه
    # «محادثة مع نفسه» في القائمة. تُستثنى كما تُستثنى من كل أسطح المزوّد.
    for t in (x for x in get_tenants_service().list() if x.id != 1):
        summary = provider_chat.thread_summary(t.id)
        items.append({
            "tenant": t,
            "unread": unread.get(int(t.id), 0),
            "total": summary["total"],
            "last": summary["last"],
            "last_at": last.get(int(t.id), ""),
        })
    # غير المقروء أولًا، ثم الأحدث نشاطًا
    items.sort(key=lambda x: (-x["unread"], x["last_at"] or ""), reverse=False)
    items.sort(key=lambda x: (x["unread"] > 0, x["last_at"] or ""), reverse=True)
    return render_template("radius/provider_chat_home.html", items=items,
                           total_unread=sum(unread.values()))


def _tenant_or_404(tenant_id: int):
    from ..services.tenants import get_tenants_service
    t = next((x for x in get_tenants_service().list() if int(x.id) == int(tenant_id)), None)
    if not t:
        abort(404)
    return t


def provider_chat_thread(tenant_id: int):
    _require_provider()
    t = _tenant_or_404(tenant_id)
    msgs = provider_chat.list_messages(tenant_id=tenant_id, limit=300)
    provider_chat.mark_read(tenant_id=tenant_id, side="provider")
    return render_template("radius/provider_chat_thread.html",
                           tenant=t, messages=msgs,
                           last_id=(msgs[-1]["id"] if msgs else 0))


def _extract_attachment(tenant_id: int):
    """MT43 — يَحفظ ملفًّا مُرفَقًا إن وُجد، أو None. يَرمي ProviderChatError
    برسالةٍ عربيّة عند رفض النوع/الحجم — يَلتقطها المُتّصِل."""
    f = request.files.get("attachment")
    if not f or not getattr(f, "filename", ""):
        return None
    return provider_chat.save_attachment(tenant_id=tenant_id, file_storage=f)


def provider_chat_send(tenant_id: int):
    _require_provider()
    _tenant_or_404(tenant_id)
    try:
        att = _extract_attachment(tenant_id)
        provider_chat.post_message(tenant_id=tenant_id, sender="provider",
                                   body=request.form.get("body", ""),
                                   sender_name=_actor() or "المزوّد",
                                   attachment=att)
    except provider_chat.ProviderChatError as e:
        flash(str(e), "error")
    return redirect(url_for("radius.provider_chat_thread", tenant_id=tenant_id))


def provider_chat_poll(tenant_id: int):
    _require_provider()
    _tenant_or_404(tenant_id)
    after = request.args.get("after_id", type=int) or 0
    msgs = provider_chat.list_messages(tenant_id=tenant_id, after_id=after)
    if msgs:
        provider_chat.mark_read(tenant_id=tenant_id, side="provider")
    return jsonify({"ok": True, "messages": msgs})


def provider_chat_file(tenant_id: int, message_id: int):
    """تقديم مرفق (جانب المزوّد). المسار يُقرأ من القاعدة لا من الطلب،
    والحارس owner-only + tenant_id في الاستعلام = عزلٌ مزدوج."""
    _require_provider()
    _tenant_or_404(tenant_id)
    return _serve_attachment(tenant_id, message_id)


def _serve_attachment(tenant_id: int, message_id: int):
    from flask import send_file
    msg = provider_chat.message_by_id(tenant_id=tenant_id, message_id=message_id)
    if not msg or not msg.get("attachment_path"):
        abort(404)
    fp = provider_chat.attachment_fspath(tenant_id=tenant_id,
                                         stored_path=msg["attachment_path"])
    if not fp:
        abort(404)
    # download_name = الاسم الأصليّ للعرض؛ as_attachment كي لا يُنفَّذ في
    # المتصفّح (طبقة دفاع ثانية فوق allowlist النوع).
    return send_file(str(fp), mimetype=msg.get("attachment_mime") or None,
                     as_attachment=True,
                     download_name=msg.get("attachment_name") or "attachment")


# ─────────────── جانب الشبكة ───────────────

def network_support_chat():
    tid = _tid()
    msgs = provider_chat.list_messages(tenant_id=tid, limit=300)
    provider_chat.mark_read(tenant_id=tid, side="network")
    return render_template("radius/network_support_chat.html",
                           messages=msgs,
                           last_id=(msgs[-1]["id"] if msgs else 0))


def network_support_send():
    tid = _tid()
    try:
        att = _extract_attachment(tid)
        provider_chat.post_message(tenant_id=tid, sender="network",
                                   body=request.form.get("body", ""),
                                   sender_name=_actor() or "الشبكة",
                                   attachment=att)
    except provider_chat.ProviderChatError as e:
        flash(str(e), "error")
    return redirect(url_for("radius.network_support_chat"))


def network_support_file(message_id: int):
    """تقديم مرفق (جانب الشبكة). ``_tid()`` من الجلسة، والاستعلام مُقيَّد
    به — فلا تَرى شبكةٌ مرفقات أخرى."""
    return _serve_attachment(_tid(), message_id)


def network_support_poll():
    tid = _tid()
    after = request.args.get("after_id", type=int) or 0
    msgs = provider_chat.list_messages(tenant_id=tid, after_id=after)
    if msgs:
        provider_chat.mark_read(tenant_id=tid, side="network")
    return jsonify({"ok": True, "messages": msgs})
