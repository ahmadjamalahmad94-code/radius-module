"""routes إدارة API Tokens."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import api_tokens_repo


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_tokens_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/tokens", "tok_list", tok_list, methods=["GET"])
    bp.add_url_rule("/tokens", "tok_create", tok_create, methods=["POST"])
    bp.add_url_rule("/tokens/<int:tid>/revoke", "tok_revoke", tok_revoke, methods=["POST"])


def tok_list():
    items = api_tokens_repo.list_tokens(_tid())
    new_plain = session.pop("_new_token_plain", None)
    return render_template("radius/tokens_list.html", items=items, new_plain=new_plain)


def tok_create():
    name = (request.form.get("name") or "").strip() or "untitled"
    rec, plain = api_tokens_repo.create_token(
        tenant_id=_tid(), name=name,
        created_by=session.get("admin_id") or 0,
    )
    session["_new_token_plain"] = plain
    flash(f"تم إنشاء توكن «{name}». انسخه الآن — لن يُعرض مرة أخرى.", "success")
    return redirect(url_for("radius.tok_list"))


def tok_revoke(tid: int):
    api_tokens_repo.revoke_token(_tid(), tid)
    flash("تم إلغاء التوكن.", "warning")
    return redirect(url_for("radius.tok_list"))
