"""لوحة شحن الرصيد (للمالك) — أرصدة وديون وسلف المدراء والموزّعين + شحن مباشر.

الحراسة: المالك الرئيسي فقط. مُثبَّتة خادميًّا في حارس الصلاحيات
(``blueprint._PERM_GUARDED`` + ``ui_permissions._NAV_PERM`` ⇐ ``_PERM_SUPER``)
على القراءة *و* الكتابة معًا — 403 لغير المالك، لا مجرّد إخفاء واجهة. يضيف
الحارس الصريح أدناه طبقة دفاع ثانية مستقلّة.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)

from ..services.credit_dashboard import CreditDashboardError, CreditDashboardService


def register_credit_dashboard_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/credit", "credit_dashboard", credit_dashboard, methods=["GET"])
    bp.add_url_rule(
        "/credit/recharge/<entity_type>/<int:entity_id>",
        "credit_recharge", credit_recharge, methods=["POST"],
    )


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _require_owner() -> None:
    """دفاع ثانٍ مستقلّ عن حارس البلوبرنت: المالك الرئيسي وحده (علم الجلسة
    is_super_admin أصبح ملكيًّا حصرًا — راجع session_helpers._resolve_is_super)."""
    if not session.get("is_super_admin"):
        abort(403)


def _service() -> CreditDashboardService:
    return CreditDashboardService(tenant_id=_tid())


def credit_dashboard():
    _require_owner()
    return render_template(
        "radius/credit_dashboard.html",
        data=_service().overview(),
    )


def credit_recharge(entity_type: str, entity_id: int):
    _require_owner()
    try:
        result = _service().recharge(
            entity_type=entity_type,
            entity_id=entity_id,
            amount=request.form.get("amount") or "0",
            method=request.form.get("method") or "cash",
            note=request.form.get("note") or "",
            actor=_actor(),
            actor_id=int(session.get("admin_id") or 0) or None,
        )
        settled = result["settled_debt"]
        credited = result["credited_wallet"]
        if float(settled) > 0 and float(credited) > 0:
            flash(f"تم الشحن: سُدّد دين {settled} وأُضيف للرصيد {credited}.", "success")
        elif float(settled) > 0:
            flash(f"تم تسديد دين بقيمة {settled}.", "success")
        else:
            flash(f"تمت إضافة {credited} إلى الرصيد.", "success")
    except CreditDashboardError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.credit_dashboard"))
