"""
routes للوحدات الجديدة: Bandwidth, IpPools, Vouchers, Invoices, Tickets, Services.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types_saas import (
    BandwidthProfile, Invoice, IpPool, Service, Ticket, Voucher,
)
from ..db.repos import (
    bandwidth_repo, invoices_repo, plans_repo, pools_repo, services_repo,
    subscribers_repo, tickets_repo, vouchers_repo, nas_repo,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _i(name, d=0):
    try: return int(request.form.get(name) or d)
    except (TypeError, ValueError): return d


def _f(name, d=0.0):
    try: return float(request.form.get(name) or d)
    except (TypeError, ValueError): return d


def _date(name):
    v = request.form.get(name)
    if not v: return None
    try: return datetime.fromisoformat(v)
    except ValueError: return None


def register_saas_routes(bp: Blueprint) -> None:
    # ─── Bandwidth ───
    bp.add_url_rule("/bandwidth", "bw_list", bw_list, methods=["GET"])
    bp.add_url_rule("/bandwidth/new", "bw_new", bw_new, methods=["GET"])
    bp.add_url_rule("/bandwidth", "bw_create", bw_create, methods=["POST"])
    bp.add_url_rule("/bandwidth/<int:bw_id>/edit", "bw_edit", bw_edit, methods=["GET"])
    bp.add_url_rule("/bandwidth/<int:bw_id>", "bw_update", bw_update, methods=["POST"])
    bp.add_url_rule("/bandwidth/<int:bw_id>/delete", "bw_delete", bw_delete, methods=["POST"])
    # ─── Pools ───
    bp.add_url_rule("/pools", "pool_list", pool_list, methods=["GET"])
    bp.add_url_rule("/pools/new", "pool_new", pool_new, methods=["GET"])
    bp.add_url_rule("/pools", "pool_create", pool_create, methods=["POST"])
    bp.add_url_rule("/pools/<int:pid>/edit", "pool_edit", pool_edit, methods=["GET"])
    bp.add_url_rule("/pools/<int:pid>", "pool_update", pool_update, methods=["POST"])
    bp.add_url_rule("/pools/<int:pid>/delete", "pool_delete", pool_delete, methods=["POST"])
    # ─── Vouchers ───
    bp.add_url_rule("/vouchers", "vch_list", vch_list, methods=["GET"])
    bp.add_url_rule("/vouchers/generate", "vch_generate", vch_generate, methods=["GET", "POST"])
    bp.add_url_rule("/vouchers/redeem", "vch_redeem", vch_redeem, methods=["POST"])
    bp.add_url_rule("/vouchers/<int:vid>/revoke", "vch_revoke", vch_revoke, methods=["POST"])
    # ─── Invoices ───
    bp.add_url_rule("/invoices", "inv_list", inv_list, methods=["GET"])
    bp.add_url_rule("/invoices/new", "inv_new", inv_new, methods=["GET"])
    bp.add_url_rule("/invoices", "inv_create", inv_create, methods=["POST"])
    bp.add_url_rule("/invoices/<int:iid>/status", "inv_status", inv_status, methods=["POST"])
    # ─── Tickets ───
    bp.add_url_rule("/tickets", "tk_list", tk_list, methods=["GET"])
    bp.add_url_rule("/tickets/new", "tk_new", tk_new, methods=["GET"])
    bp.add_url_rule("/tickets", "tk_create", tk_create, methods=["POST"])
    bp.add_url_rule("/tickets/<int:tid>", "tk_view", tk_view, methods=["GET"])
    bp.add_url_rule("/tickets/<int:tid>/reply", "tk_reply", tk_reply, methods=["POST"])
    bp.add_url_rule("/tickets/<int:tid>/status", "tk_status", tk_status, methods=["POST"])
    # ─── Services ───
    bp.add_url_rule("/services", "svc_list", svc_list, methods=["GET"])
    bp.add_url_rule("/services/new", "svc_new", svc_new, methods=["GET"])
    bp.add_url_rule("/services", "svc_create", svc_create, methods=["POST"])
    bp.add_url_rule("/services/<int:sid>/edit", "svc_edit", svc_edit, methods=["GET"])
    bp.add_url_rule("/services/<int:sid>", "svc_update", svc_update, methods=["POST"])
    bp.add_url_rule("/services/<int:sid>/delete", "svc_delete", svc_delete, methods=["POST"])


# ───────────────────────────────── Bandwidth ─────────────────────────────────

def bw_list():
    items = bandwidth_repo.list_all(_tid())
    return render_template("radius/bandwidth_list.html", items=items)


def bw_new():
    blank = BandwidthProfile(id=None, tenant_id=_tid(), name="")
    return render_template("radius/bandwidth_form.html", item=blank, is_new=True)


def bw_create():
    b = BandwidthProfile(id=None, tenant_id=_tid(),
        name=(request.form.get("name") or "").strip(),
        rate_down=_i("rate_down"), rate_down_unit=request.form.get("rate_down_unit") or "Kbps",
        rate_up=_i("rate_up"), rate_up_unit=request.form.get("rate_up_unit") or "Kbps",
        burst=(request.form.get("burst") or "").strip(), priority=_i("priority"))
    bandwidth_repo.upsert(b)
    flash(f"تم إنشاء «{b.name}».", "success")
    return redirect(url_for("radius.bw_list"))


def bw_edit(bw_id: int):
    it = bandwidth_repo.get(_tid(), bw_id)
    if not it: abort(404)
    return render_template("radius/bandwidth_form.html", item=it, is_new=False)


def bw_update(bw_id: int):
    it = bandwidth_repo.get(_tid(), bw_id)
    if not it: abort(404)
    from dataclasses import replace
    b = replace(it,
        name=(request.form.get("name") or it.name).strip(),
        rate_down=_i("rate_down", it.rate_down),
        rate_down_unit=request.form.get("rate_down_unit") or it.rate_down_unit,
        rate_up=_i("rate_up", it.rate_up),
        rate_up_unit=request.form.get("rate_up_unit") or it.rate_up_unit,
        burst=(request.form.get("burst") or it.burst).strip(),
        priority=_i("priority", it.priority))
    bandwidth_repo.upsert(b)
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.bw_list"))


def bw_delete(bw_id: int):
    bandwidth_repo.delete(_tid(), bw_id)
    flash("تم الحذف.", "success")
    return redirect(url_for("radius.bw_list"))


# ───────────────────────────────── Pools ─────────────────────────────────

def pool_list():
    items = pools_repo.list_all(_tid())
    nas = {n.id: n for n in nas_repo.list_nas(_tid(), limit=500)}
    return render_template("radius/pools_list.html", items=items, nas=nas)


def pool_new():
    blank = IpPool(id=None, tenant_id=_tid(), pool_name="", range_ip="")
    nas = nas_repo.list_nas(_tid(), limit=500)
    return render_template("radius/pools_form.html", item=blank, nas=nas, is_new=True)


def _pool_dto_from_form(existing: Optional[IpPool] = None) -> IpPool:
    return IpPool(
        id=existing.id if existing else None, tenant_id=_tid(),
        pool_name=(request.form.get("pool_name") or "").strip(),
        range_ip=(request.form.get("range_ip") or "").strip(),
        local_ip=(request.form.get("local_ip") or "").strip(),
        router_id=int(request.form["router_id"]) if request.form.get("router_id") else None,
    )


def pool_create():
    pools_repo.upsert(_pool_dto_from_form())
    flash("تم الإنشاء.", "success")
    return redirect(url_for("radius.pool_list"))


def pool_edit(pid: int):
    it = pools_repo.get(_tid(), pid)
    if not it: abort(404)
    nas = nas_repo.list_nas(_tid(), limit=500)
    return render_template("radius/pools_form.html", item=it, nas=nas, is_new=False)


def pool_update(pid: int):
    it = pools_repo.get(_tid(), pid)
    if not it: abort(404)
    pools_repo.upsert(_pool_dto_from_form(it))
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.pool_list"))


def pool_delete(pid: int):
    pools_repo.delete(_tid(), pid)
    flash("تم الحذف.", "success")
    return redirect(url_for("radius.pool_list"))


# ───────────────────────────────── Vouchers ─────────────────────────────────

def vch_list():
    # Consolidated into the billing hub (Hub 1). Keep this URL alive as
    # a redirect so bookmarks + POST success-redirects keep working.
    status = request.args.get("status")
    args = {"tab": "vouchers"}
    if status:
        args["status"] = status
    return redirect(url_for("radius.billing_hub", **args))


def vch_generate():
    if request.method == "POST":
        try:
            count = int(request.form.get("count") or 0)
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            flash("قيم غير صحيحة", "error")
            return redirect(url_for("radius.vch_generate"))
        if count <= 0 or amount <= 0:
            flash("العدد والمبلغ مطلوبان وأكبر من صفر", "error")
            return redirect(url_for("radius.vch_generate"))
        plan_id = request.form.get("plan_id")
        expire = _date("expire_at")
        # عدد خانات الكود (اختياري) — الافتراضي 12 خانة كما كان سابقًا،
        # والحدود الآمنة (6–16) تُفرض داخل الـ repo أيضًا.
        code_length = _i("code_length", vouchers_repo.CODE_LEN_DEFAULT)
        code_length = min(max(code_length, vouchers_repo.CODE_LEN_MIN), vouchers_repo.CODE_LEN_MAX)
        new_items = vouchers_repo.generate_bulk(
            tenant_id=_tid(), amount=amount, count=count,
            plan_id=int(plan_id) if plan_id else None,
            expire_at=expire,
            generated_by=session.get("admin_id") or 0,
            code_length=code_length,
        )
        flash(f"تم توليد {len(new_items)} كوبون.", "success")
        return redirect(url_for("radius.billing_hub", tab="vouchers", status="active"))
    # GET: the generate form now lives in a modal on the billing hub.
    return redirect(url_for("radius.billing_hub", tab="vouchers"))


def vch_redeem():
    """صرف كوبون لمشترك: تحقق (موجود/نشط/غير منتهٍ) ثم تعليمه مستخدمًا
    وإضافة قيمته إلى رصيد المشترك عبر خدمة الرصيد النقدي القائمة
    (نفس مسار «إضافة رصيد نقدي» — لا منطق مالي جديد)."""
    back = redirect(url_for("radius.billing_hub", tab="vouchers"))
    code = (request.form.get("code") or "").strip()
    username = (request.form.get("username") or "").strip()
    if not code or not username:
        flash("كود الكوبون والمشترك مطلوبان.", "error")
        return back

    v = vouchers_repo.get_by_code(_tid(), code)
    if not v:
        flash("الكوبون غير موجود — تأكد من الكود.", "error")
        return back
    if v.status == "used":
        flash("هذا الكوبون استُخدم من قبل.", "error")
        return back
    if v.status != "active":
        flash("هذا الكوبون ملغى ولا يمكن صرفه.", "error")
        return back
    if v.expire_at and v.expire_at < datetime.now():
        flash("انتهت صلاحية هذا الكوبون.", "error")
        return back
    if float(v.amount or 0) <= 0:
        flash("لا توجد قيمة صالحة لهذا الكوبون.", "error")
        return back

    sub = subscribers_repo.get_subscriber(_tid(), username)
    if not sub:
        flash("المشترك غير موجود.", "error")
        return back

    # تعليم الكوبون مستخدمًا أولًا وذريًا (يمنع الصرف المزدوج عند التزامن)،
    # ثم إضافة القيمة للرصيد. لو فشل الإيداع نعيد الكوبون نشطًا.
    if not vouchers_repo.mark_used(_tid(), v.id, subscriber_id=int(sub.id)):
        flash("تم صرف هذا الكوبون للتو من جهة أخرى.", "error")
        return back
    try:
        from ..services.users import get_users_service
        saved = get_users_service().add_cash_balance(
            actor=_actor(),
            username=sub.username,
            amount=float(v.amount),
            notes=f"صرف كوبون {v.code}",
        )
    except Exception as e:  # noqa: BLE001 — إعادة الكوبون نشطًا عند أي فشل في الإيداع
        from ..db.connection import transaction as _txn
        with _txn() as conn:
            conn.execute(
                "UPDATE vouchers SET status='active', used_by_subscriber_id=NULL, used_at=NULL "
                "WHERE tenant_id=? AND id=?", (_tid(), v.id))
        msg = getattr(e, "message", None) or str(e)
        flash(f"تعذّر إضافة الرصيد — لم يُصرف الكوبون: {msg}", "error")
        return back

    flash(
        f"تم صرف الكوبون {v.code} بقيمة {float(v.amount):.2f} للمشترك {sub.username}. "
        f"الرصيد الحالي {float(saved.balance or 0):.2f}.",
        "success",
    )
    return redirect(url_for("radius.billing_hub", tab="vouchers", status="used"))


def vch_revoke(vid: int):
    vouchers_repo.revoke(_tid(), vid)
    flash("تم الإلغاء.", "warning")
    return redirect(url_for("radius.vch_list"))


# ───────────────────────────────── Invoices ─────────────────────────────────

def inv_list():
    # Consolidated into the billing hub (Hub 1). Redirect keeps the old
    # URL + POST success-redirects working.
    status = request.args.get("status")
    args = {"tab": "invoices"}
    if status:
        args["status"] = status
    return redirect(url_for("radius.billing_hub", **args))


def inv_new():
    # The "new invoice" form now lives in a modal on the billing hub.
    return redirect(url_for("radius.billing_hub", tab="invoices"))


def inv_create():
    sub_id = _i("subscriber_id")
    sub = next((s for s in subscribers_repo.list_subscribers(_tid(), limit=10_000)
                if s.id == sub_id), None)
    if not sub:
        flash("اختر مشتركًا صحيحًا", "error")
        return redirect(url_for("radius.inv_new"))
    plan = None
    plan_id_str = request.form.get("plan_id")
    if plan_id_str:
        plan = plans_repo.get_plan(_tid(), int(plan_id_str))
    inv = Invoice(
        id=None, tenant_id=_tid(), invoice_number="",
        subscriber_id=sub.id, username=sub.username,
        amount=_f("amount"),
        admin_id=session.get("admin_id") or 0,
        plan_id=plan.id if plan else None,
        plan_name=plan.name if plan else "",
        service_type=request.form.get("service_type") or "Hotspot",
        direction=request.form.get("direction") or "charge",
        balance_before=sub.balance, balance_after=sub.balance + _f("amount"),
        recharged_on=datetime.utcnow(),
        expiration_at=_date("expiration_at"),
        payment_method=request.form.get("payment_method") or "cash",
        status=request.form.get("status") or "paid",
        note=(request.form.get("note") or "").strip(),
    )
    invoices_repo.create(inv)
    flash(f"تم إنشاء فاتورة بقيمة {inv.amount} لـ {sub.username}.", "success")
    return redirect(url_for("radius.inv_list"))


def inv_status(iid: int):
    new_status = request.form.get("status") or "paid"
    note = request.form.get("note") or ""
    invoices_repo.update_status(_tid(), iid, new_status, note=note)
    flash("تم تحديث الحالة.", "success")
    return redirect(url_for("radius.inv_list"))


# ───────────────────────────────── Tickets ─────────────────────────────────

def tk_list():
    status = request.args.get("status") or None
    items = tickets_repo.list_tickets(_tid(), status=status, limit=500)
    # المشتركون مطلوبون لنموذج «تذكرة جديدة» الذي يعيش الآن كصندوق عائم في هذه الصفحة
    subs = subscribers_repo.list_subscribers(_tid(), limit=500)
    return render_template(
        "radius/tickets_list.html", items=items, status=status, subs=subs,
        # ?new=1 يفتح الصندوق العائم تلقائيًا (الرابط القديم /tickets/new يبقى حيًّا)
        open_new_modal=(request.args.get("new") == "1"),
    )


def tk_new():
    # نموذج الإنشاء أصبح صندوقًا عائمًا داخل صفحة القائمة —
    # الرابط القديم يبقى حيًّا ويفتح النافذة تلقائيًا عبر ?new=1.
    return redirect(url_for("radius.tk_list", new=1))


def tk_create():
    sub_id = _i("subscriber_id")
    if not sub_id:
        flash("اختر مشتركًا", "error")
        return redirect(url_for("radius.tk_new"))
    t = Ticket(
        id=None, tenant_id=_tid(), subscriber_id=sub_id,
        subject=(request.form.get("subject") or "").strip(),
        category=request.form.get("category") or "general",
        priority=request.form.get("priority") or "normal",
        body=(request.form.get("body") or "").strip(),
    )
    saved = tickets_repo.create_ticket(t)
    flash("تم إنشاء التذكرة.", "success")
    return redirect(url_for("radius.tk_view", tid=saved.id))


def tk_view(tid: int):
    t = tickets_repo.get_ticket(_tid(), tid)
    if not t: abort(404)
    replies = tickets_repo.list_replies(_tid(), tid)
    return render_template("radius/ticket_view.html", ticket=t, replies=replies)


def tk_reply(tid: int):
    from ..core.types_saas import TicketReply
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("الرد فارغ", "error")
        return redirect(url_for("radius.tk_view", tid=tid))
    tickets_repo.add_reply(TicketReply(
        id=None, ticket_id=tid, body=body,
        author_type="admin", author_id=session.get("admin_id") or 0,
        tenant_id=_tid(),
    ))
    flash("تمت الإضافة.", "success")
    return redirect(url_for("radius.tk_view", tid=tid))


def tk_status(tid: int):
    new_status = request.form.get("status") or "open"
    tickets_repo.update_ticket(_tid(), tid, status=new_status)
    flash("تم تحديث الحالة.", "success")
    return redirect(url_for("radius.tk_view", tid=tid))


# ───────────────────────────────── Services ─────────────────────────────────

def svc_list():
    items = services_repo.list_all(_tid(), limit=500)
    # المشتركون مطلوبون لنموذج «إضافة معدّة» الذي يعيش الآن كصندوق عائم في هذه الصفحة
    subs = subscribers_repo.list_subscribers(_tid(), limit=500)
    return render_template(
        "radius/services_list.html", items=items, subs=subs,
        # ?new=1 يفتح الصندوق العائم تلقائيًا (الرابط القديم /services/new يبقى حيًّا)
        open_new_modal=(request.args.get("new") == "1"),
    )


def svc_new():
    # نموذج الإنشاء أصبح صندوقًا عائمًا داخل صفحة القائمة —
    # الرابط القديم يبقى حيًّا ويفتح النافذة تلقائيًا عبر ?new=1.
    return redirect(url_for("radius.svc_list", new=1))


def _svc_dto(existing=None) -> Service:
    return Service(
        id=existing.id if existing else None, tenant_id=_tid(),
        subscriber_id=_i("subscriber_id"),
        name=(request.form.get("name") or "").strip(),
        serial=(request.form.get("serial") or "").strip(),
        mac=(request.form.get("mac") or "").strip(),
        type=request.form.get("type") or "router",
        rent_per_month=_f("rent_per_month"),
        status=request.form.get("status") or "given",
        given_at=_date("given_at"),
        returned_at=_date("returned_at"),
        notes=(request.form.get("notes") or "").strip(),
    )


def svc_create():
    services_repo.upsert(_svc_dto())
    flash("تم الإضافة.", "success")
    return redirect(url_for("radius.svc_list"))


def svc_edit(sid: int):
    it = services_repo.get(_tid(), sid)
    if not it: abort(404)
    subs = subscribers_repo.list_subscribers(_tid(), limit=500)
    return render_template("radius/services_form.html", item=it, subs=subs, is_new=False)


def svc_update(sid: int):
    it = services_repo.get(_tid(), sid)
    if not it: abort(404)
    services_repo.upsert(_svc_dto(it))
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.svc_list"))


def svc_delete(sid: int):
    services_repo.delete(_tid(), sid)
    flash("تم الحذف.", "success")
    return redirect(url_for("radius.svc_list"))
