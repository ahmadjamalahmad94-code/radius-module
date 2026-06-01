"""Hub 4 — المركز المالي.

تجميع واجهة فقط: يعرض لوحة المركز المالي، المحافظ، الإيرادات، الديون،
والسلف داخل صفحة واحدة بتبويبات. لا يغيّر منطق المحافظ أو الإيرادات:
كل عمليات الشحن والخصم وإنشاء المحافظ تبقى على مساراتها الأصلية في
finance_center.py، والقراءة تتم عبر نفس خدمة FinanceCenterService.
"""
from __future__ import annotations

from flask import Blueprint, flash, render_template, request, session

from ..services.business_os_access import PERM_WALLET_CREDIT, PERM_WALLET_DEBIT, SafetyGateService
from ..services.business_os_finance_center import FinanceCenterService

_BASE = "/finance-center"
_TABS = ("dashboard", "wallets", "revenue", "loans_debts")


def register_finance_center_hub_routes(bp: Blueprint) -> None:
    bp.add_url_rule(_BASE, "finance_center_hub", finance_center_hub, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _permissions() -> tuple[str, ...]:
    if session.get("is_super_admin"):
        return ("admin:full", PERM_WALLET_CREDIT, PERM_WALLET_DEBIT)
    return tuple(session.get("permissions") or ())


def _can_wallet_credit() -> bool:
    return SafetyGateService().check("wallet.credit", permissions=_permissions()).allowed


def _can_wallet_debit(amount: str = "1.00") -> bool:
    return SafetyGateService().check(
        "wallet.debit",
        permissions=_permissions(),
        amount=amount or "1.00",
    ).allowed


def finance_center_hub():
    svc = FinanceCenterService()
    tab = (request.args.get("tab") or "dashboard").strip()
    if tab not in _TABS:
        flash("تم تجاهل تبويب مالي غير معروف.", "warning")
        tab = "dashboard"

    loan_status = (request.args.get("status") or "").strip()
    if loan_status not in {"", "open", "settled", "voided"}:
        flash("فلتر حالة السلف غير صالح.", "warning")
        loan_status = ""

    tenant_id = _tid()
    wallets = svc.wallets(tenant_id=tenant_id, limit=150)
    tx_by_wallet = {
        wallet["id"]: svc.wallet_transactions(
            tenant_id=tenant_id,
            wallet_id=int(wallet["id"]),
            limit=5,
        )
        for wallet in wallets[:20]
    }

    return render_template(
        "radius/finance_center_hub.html",
        tab=tab,
        summary=svc.dashboard(tenant_id=tenant_id),
        wallets=wallets,
        tx_by_wallet=tx_by_wallet,
        revenue=svc.revenue(tenant_id=tenant_id),
        debts=svc.debts(tenant_id=tenant_id),
        loans=svc.loans(tenant_id=tenant_id, status=loan_status),
        loan_status=loan_status,
        can_wallet_credit=_can_wallet_credit(),
        can_wallet_debit=_can_wallet_debit(),
    )
