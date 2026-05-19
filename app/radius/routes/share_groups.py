"""Share Groups routes — مجموعات مشاركة الباندويث."""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import audit_repo, share_groups_repo, subscribers_repo


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def register_share_groups_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/share_groups", "sgrp_list", sgrp_list, methods=["GET"])
    bp.add_url_rule("/share_groups/new", "sgrp_new", sgrp_new, methods=["GET"])
    bp.add_url_rule("/share_groups", "sgrp_create", sgrp_create, methods=["POST"])
    bp.add_url_rule("/share_groups/<int:gid>", "sgrp_view", sgrp_view, methods=["GET"])
    bp.add_url_rule("/share_groups/<int:gid>/edit", "sgrp_edit", sgrp_edit, methods=["GET"])
    bp.add_url_rule("/share_groups/<int:gid>", "sgrp_update", sgrp_update, methods=["POST"])
    bp.add_url_rule("/share_groups/<int:gid>/delete", "sgrp_delete", sgrp_delete, methods=["POST"])
    bp.add_url_rule("/share_groups/<int:gid>/add_member", "sgrp_add_member",
                    sgrp_add_member, methods=["POST"])
    bp.add_url_rule("/share_groups/<int:gid>/remove/<int:sid>", "sgrp_remove_member",
                    sgrp_remove_member, methods=["POST"])


def _form() -> dict:
    f = request.form
    def _i(k, d=0):
        try: return int(f.get(k) or d)
        except (TypeError, ValueError): return d
    return dict(
        name=(f.get("name") or "").strip(),
        description=(f.get("description") or "").strip(),
        shared_quota_mb=_i("shared_quota_mb"),
        shared_speed_down_kbps=_i("shared_speed_down_kbps"),
        shared_speed_up_kbps=_i("shared_speed_up_kbps"),
        max_members=_i("max_members"),
        enabled=bool(f.get("enabled")),
    )


def sgrp_list():
    items = share_groups_repo.list_groups(_tid())
    return render_template("radius/sgrp_list.html", items=items)


def sgrp_new():
    return render_template("radius/sgrp_form.html", item={}, is_new=True)


def sgrp_create():
    d = _form()
    if not d["name"]:
        flash("الاسم مطلوب", "error")
        return redirect(url_for("radius.sgrp_new"))
    gid = share_groups_repo.create(tenant_id=_tid(), **d)
    audit_repo.record(tenant_id=_tid(), actor=_actor(), action="create",
                       target_type="share_group", target_id=str(gid),
                       payload={"name": d["name"]})
    flash(f"تم إنشاء مجموعة «{d['name']}».", "success")
    return redirect(url_for("radius.sgrp_view", gid=gid))


def sgrp_view(gid: int):
    g_data = share_groups_repo.get(_tid(), gid)
    if not g_data: abort(404)
    members = share_groups_repo.list_members(gid)
    # subscribers غير الأعضاء (لإضافتهم)
    member_ids = {m["id"] for m in members}
    all_subs = subscribers_repo.list_subscribers(_tid(), limit=1000)
    candidates = [s for s in all_subs if s.id not in member_ids]
    return render_template("radius/sgrp_view.html",
                            grp=g_data, members=members, candidates=candidates)


def sgrp_edit(gid: int):
    g_data = share_groups_repo.get(_tid(), gid)
    if not g_data: abort(404)
    return render_template("radius/sgrp_form.html", item=g_data, is_new=False)


def sgrp_update(gid: int):
    if not share_groups_repo.get(_tid(), gid): abort(404)
    share_groups_repo.update(_tid(), gid, **_form())
    audit_repo.record(tenant_id=_tid(), actor=_actor(), action="update",
                       target_type="share_group", target_id=str(gid))
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.sgrp_view", gid=gid))


def sgrp_delete(gid: int):
    share_groups_repo.delete(_tid(), gid)
    audit_repo.record(tenant_id=_tid(), actor=_actor(), action="delete",
                       target_type="share_group", target_id=str(gid))
    flash("تم الحذف.", "warning")
    return redirect(url_for("radius.sgrp_list"))


def sgrp_add_member(gid: int):
    try: sid = int(request.form.get("subscriber_id") or 0)
    except ValueError: sid = 0
    if not sid:
        flash("اختر مشتركًا", "error")
        return redirect(url_for("radius.sgrp_view", gid=gid))
    share_groups_repo.add_member(_tid(), gid, sid)
    audit_repo.record(tenant_id=_tid(), actor=_actor(), action="add_member",
                       target_type="share_group", target_id=str(gid),
                       payload={"subscriber_id": sid})
    flash("تمت الإضافة.", "success")
    return redirect(url_for("radius.sgrp_view", gid=gid))


def sgrp_remove_member(gid: int, sid: int):
    share_groups_repo.remove_member(gid, sid)
    flash("تمت الإزالة.", "warning")
    return redirect(url_for("radius.sgrp_view", gid=gid))
