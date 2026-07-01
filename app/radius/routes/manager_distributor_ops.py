"""Manager/distributor operational profile routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.manager_distributor_ops import ManagerDistributorError, ManagerDistributorOpsService


def register_manager_distributor_ops_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/business-operators", "business_operators", business_operators, methods=["GET"])
    bp.add_url_rule("/business-operators/<entity_type>/<int:entity_id>", "business_operator_profile", business_operator_profile, methods=["GET"])
    bp.add_url_rule("/business-operators/<entity_type>/<int:entity_id>/policy", "business_operator_policy", business_operator_policy, methods=["POST"])
    bp.add_url_rule("/business-operators/<entity_type>/<int:entity_id>/recharge", "business_operator_recharge", business_operator_recharge, methods=["POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _service() -> ManagerDistributorOpsService:
    return ManagerDistributorOpsService(tenant_id=_tid())


def business_operators():
    service = _service()
    return render_template(
        "radius/business_operators.html",
        managers=service.list_scope(entity_type="manager"),
        distributors=service.list_scope(entity_type="distributor"),
    )


def business_operator_profile(entity_type: str, entity_id: int):
    try:
        profile = _service().profile(entity_type=entity_type, entity_id=entity_id)
    except ManagerDistributorError:
        return redirect(url_for("radius.business_operators"))
    # مصفوفة الأقسام (3 حالات) للمدير فقط — الموزّع لا لوحة له في اللوحة.
    section_catalog = []
    section_states = ()
    if entity_type == "manager":
        from ..services import manager_grants as _mg
        section_catalog = _mg.section_catalog(int(entity_id), tenant_id=_tid())
        section_states = _mg.SECTION_STATES
    return render_template(
        "radius/business_operator_profile.html",
        profile=profile,
        section_catalog=section_catalog,
        section_states=section_states,
    )


def business_operator_policy(entity_type: str, entity_id: int):
    try:
        permissions = {
            key: request.form.get(key) in {"1", "on", "true", "yes"}
            for key in (
                "can_create_batch",
                "can_create_subscriber",
                "can_activate_subscriber",
                "can_give_free_days",
                "can_give_trial_days",
                "can_give_loan",
                "can_manage_distributors",
                "can_view_all_subscribers",
                "can_view_all_card_batches",
                "can_import_batches",
            )
        }
        limits = {
            "max_free_days": int(request.form.get("max_free_days") or 0),
            "max_trial_days": int(request.form.get("max_trial_days") or 0),
            "loan_wallet_deducted": request.form.get("loan_wallet_deducted") in {"1", "on", "true", "yes"},
        }
        _service().set_policy(
            entity_type=entity_type,
            entity_id=entity_id,
            permissions=permissions,
            limits=limits,
            profit_share_percent=float(request.form.get("profit_share_percent") or 0),
            credit_limit=request.form.get("credit_limit") or "0",
            require_approval_above=request.form.get("require_approval_above") or "0",
        )
        # المستوى 1: وصول القسم (3 حالات) — للمدير فقط. حقول النموذج اسمها
        # ``section_<name>`` وقيمتها open/locked/hidden. غير المُرسَل = open.
        if entity_type == "manager":
            from ..services import manager_grants as _mg
            section_map = {
                name: request.form.get(f"section_{name}")
                for name in _mg.section_names()
                if request.form.get(f"section_{name}")
            }
            _mg.set_section_access(int(entity_id), section_map, tenant_id=_tid(),
                                   by=int(session.get("admin_id") or 0))
        flash("تم تحديث صلاحيات وحدود المشغل.", "success")
    except (ManagerDistributorError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_operator_profile", entity_type=entity_type, entity_id=entity_id))


def business_operator_recharge(entity_type: str, entity_id: int):
    try:
        _service().recharge_wallet(
            entity_type=entity_type,
            entity_id=entity_id,
            amount=request.form.get("amount") or "0",
            method=request.form.get("method") or "cash",
            actor=_actor(),
        )
        flash("تم شحن محفظة المشغل.", "success")
    except (ManagerDistributorError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_operator_profile", entity_type=entity_type, entity_id=entity_id))
