"""إدارة حسابات VPN — عرض، إنشاء، تعطيل.

مُسجَّل على blueprint الرئيسي (radius) ضمن _register_all.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for


def register_vpn_accounts_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/vpn-accounts", "vpn_accounts_list", vpn_accounts_list, methods=["GET"])
    bp.add_url_rule("/vpn-accounts/create", "vpn_accounts_create", vpn_accounts_create, methods=["POST"])
    bp.add_url_rule("/vpn-accounts/<int:account_id>/delete", "vpn_accounts_delete", vpn_accounts_delete, methods=["POST"])
    bp.add_url_rule("/vpn-accounts/<int:account_id>/suspend", "vpn_accounts_suspend", vpn_accounts_suspend, methods=["POST"])
    bp.add_url_rule("/vpn-accounts/<int:account_id>/activate", "vpn_accounts_activate", vpn_accounts_activate, methods=["POST"])
    bp.add_url_rule("/vpn-accounts/sync", "vpn_accounts_sync", vpn_accounts_sync, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def vpn_accounts_list():
    from ..services.vpn_account_service import list_accounts
    from ..services.license_sync_service import get_active_allocations, get_current_snapshot
    from ..db.connection import db

    tid = _tid()
    include_inactive = request.args.get("all") == "1"
    alloc_id = request.args.get("alloc_id", type=int)

    accounts = list_accounts(tid, allocation_mirror_id=alloc_id, include_inactive=include_inactive)
    allocations = db().execute(
        "SELECT * FROM service_allocation_mirror WHERE tenant_id=? ORDER BY service_type, id",
        (tid,),
    ).fetchall()
    snapshot = get_current_snapshot(tid)

    return render_template(
        "radius/vpn_accounts.html",
        accounts=accounts,
        allocations=[dict(a) for a in allocations],
        snapshot=snapshot,
        include_inactive=include_inactive,
        selected_alloc_id=alloc_id,
    )


def vpn_accounts_create():
    from ..services.vpn_account_service import create_account

    tid = _tid()
    alloc_id = request.form.get("allocation_mirror_id", type=int)
    username = (request.form.get("username") or "").strip()
    raw_password = request.form.get("password") or ""
    display_name = (request.form.get("display_name") or "").strip()
    max_concurrent = request.form.get("max_concurrent", 1, type=int)
    speed_mbps = request.form.get("speed_limit_mbps", type=int)

    if not alloc_id:
        flash("اختر التخصيص أولًا.", "error")
        return redirect(url_for("radius.vpn_accounts_list"))

    try:
        result = create_account(
            tenant_id=tid,
            allocation_mirror_id=alloc_id,
            username=username,
            raw_password=raw_password,
            display_name=display_name,
            max_concurrent=max_concurrent,
            speed_limit_mbps=speed_mbps,
        )
        flash(f"تم إنشاء الحساب «{result['username']}» بنجاح.", "success")
    except ValueError as exc:
        flash(str(exc), "error")

    return redirect(url_for("radius.vpn_accounts_list", alloc_id=alloc_id))


def vpn_accounts_delete(account_id: int):
    from ..services.vpn_account_service import delete_account

    tid = _tid()
    ok = delete_account(tid, account_id)
    flash("تم حذف الحساب." if ok else "لم يُعثر على الحساب.", "success" if ok else "error")
    return redirect(url_for("radius.vpn_accounts_list"))


def vpn_accounts_suspend(account_id: int):
    from ..services.vpn_account_service import suspend_account

    tid = _tid()
    ok = suspend_account(tid, account_id)
    flash("تم تعطيل الحساب." if ok else "لم يُعثر على الحساب.", "success" if ok else "error")
    return redirect(url_for("radius.vpn_accounts_list"))


def vpn_accounts_activate(account_id: int):
    from ..services.vpn_account_service import activate_account

    tid = _tid()
    ok = activate_account(tid, account_id)
    flash("تم تفعيل الحساب." if ok else "لم يُعثر على الحساب.", "success" if ok else "error")
    return redirect(url_for("radius.vpn_accounts_list"))


def vpn_accounts_sync():
    """تزامن يدوي فوري مع لوحة التراخيص."""
    from ..services.license_sync_service import sync

    tid = _tid()
    result = sync(tid)
    if result.get("ok"):
        flash(
            f"تمت المزامنة: {result['allocations_synced']} تخصيص محدَّث "
            f"(الترخيص: {result.get('license_status', '?')}).",
            "success",
        )
    else:
        flash(f"فشلت المزامنة: {result.get('reason', 'خطأ غير معروف')}.", "error")
    return redirect(url_for("radius.vpn_accounts_list"))
