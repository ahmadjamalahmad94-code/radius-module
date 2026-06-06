"""routes إدارة API Tokens."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..auth.session_helpers import is_super_admin
from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import api_tokens_repo, tenants_repo

# مفتاح الفرض المركزي لمصادقة الـAPI — يُقرأ في app/api/auth.py:api_auth_required.
# نظام-عام لذا نقرؤه/نكتبه على tenant 1 الثابت.
_ENFORCE_KEY = "security.api_auth_required"


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _enforcement_on() -> bool:
    val = (tenants_repo.get_setting(DEFAULT_TENANT_ID, _ENFORCE_KEY, "0") or "0").strip().lower()
    return val in {"1", "true", "yes", "on"}


def register_tokens_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/tokens", "tok_list", tok_list, methods=["GET"])
    bp.add_url_rule("/tokens", "tok_create", tok_create, methods=["POST"])
    bp.add_url_rule("/tokens/<int:tid>/revoke", "tok_revoke", tok_revoke, methods=["POST"])
    bp.add_url_rule("/tokens/enforcement", "tok_enforcement", tok_enforcement, methods=["POST"])


def tok_list():
    items = api_tokens_repo.list_tokens(_tid())
    new_plain = session.pop("_new_token_plain", None)
    return render_template(
        "radius/tokens_list.html",
        items=items,
        new_plain=new_plain,
        enforcement_on=_enforcement_on(),
        can_toggle_enforcement=is_super_admin(),
    )


def tok_enforcement():
    """تفعيل/تعطيل الفرض المركزي لمصادقة الـAPI (تدريجي). super_admin فقط:
    قرار أمني نظام-عام قد يقطع تطبيقات Flutter التي لم تُحدَّث بعد."""
    if not is_super_admin():
        flash("هذا الإجراء متاح لمدير النظام الأعلى فقط.", "danger")
        return redirect(url_for("radius.tok_list"))
    enable = (request.form.get("enable") or "").strip() == "1"
    tenants_repo.set_setting(
        DEFAULT_TENANT_ID, _ENFORCE_KEY, "1" if enable else "0",
        by=session.get("admin_id") or 0,
    )
    if enable:
        flash("تم تفعيل فرض المصادقة على كل نقاط الواجهة البرمجية. أي طلب بلا "
              "مفتاح صحيح أو اعتماد أدمن سيُرفض الآن — تأكّد أن تطبيقاتك تُرسل الاعتماد.",
              "warning")
    else:
        flash("تم تعطيل الفرض المركزي. النقاط المحمية صراحةً تبقى محمية؛ "
              "النقاط غير المزخرفة تعود مفتوحة.", "info")
    return redirect(url_for("radius.tok_list"))


def tok_create():
    name = (request.form.get("name") or "").strip() or "untitled"
    rec, plain = api_tokens_repo.create_token(
        tenant_id=_tid(), name=name,
        created_by=session.get("admin_id") or 0,
    )
    session["_new_token_plain"] = plain
    flash(f"تم إنشاء رمز «{name}». انسخه الآن — لن يُعرض مرة أخرى.", "success")
    return redirect(url_for("radius.tok_list"))


def tok_revoke(tid: int):
    api_tokens_repo.revoke_token(_tid(), tid)
    flash("تم إلغاء الرمز.", "warning")
    return redirect(url_for("radius.tok_list"))
