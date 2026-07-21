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
    # جانب الشبكة
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

def provider_chat_home():
    _require_provider()
    from ..services.tenants import get_tenants_service
    unread = provider_chat.unread_by_tenant()
    last = provider_chat.last_activity_by_tenant()
    items = []
    for t in get_tenants_service().list():
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


def provider_chat_send(tenant_id: int):
    _require_provider()
    _tenant_or_404(tenant_id)
    try:
        provider_chat.post_message(tenant_id=tenant_id, sender="provider",
                                   body=request.form.get("body", ""),
                                   sender_name=_actor() or "المزوّد")
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


# ─────────────── جانب الشبكة ───────────────

def network_support_chat():
    tid = _tid()
    msgs = provider_chat.list_messages(tenant_id=tid, limit=300)
    provider_chat.mark_read(tenant_id=tid, side="network")
    return render_template("radius/network_support_chat.html",
                           messages=msgs,
                           last_id=(msgs[-1]["id"] if msgs else 0))


def network_support_send():
    try:
        provider_chat.post_message(tenant_id=_tid(), sender="network",
                                   body=request.form.get("body", ""),
                                   sender_name=_actor() or "الشبكة")
    except provider_chat.ProviderChatError as e:
        flash(str(e), "error")
    return redirect(url_for("radius.network_support_chat"))


def network_support_poll():
    tid = _tid()
    after = request.args.get("after_id", type=int) or 0
    msgs = provider_chat.list_messages(tenant_id=tid, after_id=after)
    if msgs:
        provider_chat.mark_read(tenant_id=tid, side="network")
    return jsonify({"ok": True, "messages": msgs})
