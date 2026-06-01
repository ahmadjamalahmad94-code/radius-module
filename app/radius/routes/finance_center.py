"""Business OS Finance Center web routes."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..core.system_config import default_currency
from ..services.business_os_access import PERM_WALLET_CREDIT, PERM_WALLET_DEBIT, SafetyGateService
from ..services.business_os_finance import BusinessOSValidationError, WalletService
from ..services.business_os_finance_center import FinanceCenterService


def register_finance_center_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/finance", "business_finance", finance_dashboard, methods=["GET"])
    bp.add_url_rule("/finance/wallets", "business_finance_wallets", finance_wallets, methods=["GET"])
    bp.add_url_rule("/finance/wallets", "business_finance_wallets_create", finance_wallet_create, methods=["POST"])
    bp.add_url_rule(
        "/finance/wallets/<int:wallet_id>/credit",
        "business_finance_wallet_credit",
        finance_wallet_credit,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/finance/wallets/<int:wallet_id>/debit",
        "business_finance_wallet_debit",
        finance_wallet_debit,
        methods=["POST"],
    )
    bp.add_url_rule("/finance/revenue", "business_finance_revenue", finance_revenue, methods=["GET"])
    bp.add_url_rule("/finance/debts", "business_finance_debts", finance_debts, methods=["GET"])
    bp.add_url_rule("/finance/loans", "business_finance_loans", finance_loans, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _permissions() -> tuple[str, ...]:
    if session.get("is_super_admin"):
        return ("admin:full", PERM_WALLET_CREDIT, PERM_WALLET_DEBIT)
    return tuple(session.get("permissions") or ())


def _field(name: str) -> str:
    return (request.form.get(name) or "").strip()


def _svc() -> FinanceCenterService:
    return FinanceCenterService()


def _can_wallet_credit() -> bool:
    return SafetyGateService().check("wallet.credit", permissions=_permissions()).allowed


def _can_wallet_debit(amount: str = "0") -> bool:
    return SafetyGateService().check("wallet.debit", permissions=_permissions(), amount=amount or "0").allowed


def _common_context(active: str) -> dict:
    return {
        "active": active,
        "can_wallet_credit": _can_wallet_credit(),
        "can_wallet_debit": _can_wallet_debit("1.00"),
    }


def finance_dashboard():
    return render_template(
        "radius/finance_center.html",
        summary=_svc().dashboard(tenant_id=_tid()),
        **_common_context("dashboard"),
    )


def finance_wallets():
    wallets = _svc().wallets(tenant_id=_tid(), limit=150)
    tx_by_wallet = {
        wallet["id"]: _svc().wallet_transactions(tenant_id=_tid(), wallet_id=int(wallet["id"]), limit=5)
        for wallet in wallets[:20]
    }
    return render_template(
        "radius/finance_wallets.html",
        wallets=wallets,
        tx_by_wallet=tx_by_wallet,
        **_common_context("wallets"),
    )


def finance_wallet_create():
    try:
        WalletService().create_wallet(
            tenant_id=_tid(),
            owner_type=_field("owner_type") or "company",
            owner_id=int(_field("owner_id")) if _field("owner_id") else None,
            currency=_field("currency") or default_currency(),
            metadata={"source": "finance_center"},
        )
        flash("تم إنشاء المحفظة المالية.", "success")
    except (BusinessOSValidationError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_finance_wallets"))


def finance_wallet_credit(wallet_id: int):
    if not _can_wallet_credit():
        flash("لا تملك صلاحية شحن المحفظة.", "error")
        return redirect(url_for("radius.business_finance_wallets"))
    try:
        WalletService().credit(
            tenant_id=_tid(),
            wallet_id=wallet_id,
            amount=_field("amount"),
            actor_type="admin",
            actor_id=session.get("admin_id"),
            reference_type="finance_center",
            notes=_field("notes"),
            metadata={"source": "finance_center"},
        )
        flash("تم شحن المحفظة وتسجيل القيد المالي.", "success")
    except BusinessOSValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_finance_wallets"))


def finance_wallet_debit(wallet_id: int):
    amount = _field("amount")
    gate = SafetyGateService().check("wallet.debit", permissions=_permissions(), amount=amount)
    if not gate.allowed:
        flash("تم منع الخصم بسبب الصلاحيات أو حدود الأمان.", "error")
        return redirect(url_for("radius.business_finance_wallets"))
    try:
        WalletService().debit(
            tenant_id=_tid(),
            wallet_id=wallet_id,
            amount=amount,
            actor_type="admin",
            actor_id=session.get("admin_id"),
            reference_type="finance_center",
            notes=_field("notes"),
            metadata={"source": "finance_center", "requires_approval": gate.requires_approval},
        )
        flash("تم خصم المبلغ وتسجيل القيد المالي.", "success")
    except BusinessOSValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.business_finance_wallets"))


def finance_revenue():
    return render_template(
        "radius/finance_revenue.html",
        revenue=_svc().revenue(tenant_id=_tid()),
        summary=_svc().dashboard(tenant_id=_tid()),
        **_common_context("revenue"),
    )


def finance_debts():
    return render_template(
        "radius/finance_debts.html",
        debts=_svc().debts(tenant_id=_tid()),
        summary=_svc().dashboard(tenant_id=_tid()),
        **_common_context("debts"),
    )


def finance_loans():
    status = _field("status")
    return render_template(
        "radius/finance_loans.html",
        loans=_svc().loans(tenant_id=_tid(), status=status),
        status=status,
        summary=_svc().dashboard(tenant_id=_tid()),
        **_common_context("loans"),
    )
