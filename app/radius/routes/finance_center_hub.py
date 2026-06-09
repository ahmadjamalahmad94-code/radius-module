"""Hub 4 — المركز المالي.

تجميع إداري موحّد: يعرض لوحة المركز المالي، المحافظ، الإيرادات، الديون،
والسلف داخل صفحة واحدة بتبويبات. لا يغيّر منطق المحافظ أو الإيرادات:
كل عمليات الشحن والخصم وإنشاء المحافظ تبقى على مساراتها الأصلية في
finance_center.py، والقراءة تتم عبر نفس خدمة FinanceCenterService.
"""
from __future__ import annotations

from flask import Blueprint, flash, render_template, request, session

from ..db.connection import db
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


def _enrich_wallets_names(wallets: list[dict]) -> list[dict]:
    """Attach owner_name (real full_name/display_name/username) to each wallet.

    Sources by owner_type — distributors live in their OWN table, not admins:
      manager      -> admins.full_name      | admins.username
      distributor  -> distributors.display_name | distributors.name
      subscriber   -> subscribers.full_name | subscribers.username
      card_user    -> card_users.display_name | card_users.mobile
      company      -> «الشركة» (no row to look up)
    """
    conn = db()

    mgr_ids  = list({w["owner_id"] for w in wallets
                     if w.get("owner_type") == "manager"     and w.get("owner_id")})
    dist_ids = list({w["owner_id"] for w in wallets
                     if w.get("owner_type") == "distributor" and w.get("owner_id")})
    sub_ids  = list({w["owner_id"] for w in wallets
                     if w.get("owner_type") == "subscriber"  and w.get("owner_id")})
    cu_ids   = list({w["owner_id"] for w in wallets
                     if w.get("owner_type") == "card_user"   and w.get("owner_id")})

    def _ph(ids: list) -> str:
        return ",".join("?" * len(ids))

    mgr_names:  dict[int, str] = {}
    dist_names: dict[int, str] = {}
    sub_names:  dict[int, str] = {}
    cu_names:   dict[int, str] = {}

    if mgr_ids:
        for r in conn.execute(
            f"SELECT id, full_name, username FROM admins WHERE id IN ({_ph(mgr_ids)})",
            mgr_ids,
        ).fetchall():
            mgr_names[r["id"]] = (r["full_name"] or "").strip() or (r["username"] or "").strip()

    if dist_ids:
        for r in conn.execute(
            f"SELECT id, display_name, name FROM distributors WHERE id IN ({_ph(dist_ids)})",
            dist_ids,
        ).fetchall():
            dist_names[r["id"]] = (r["display_name"] or "").strip() or (r["name"] or "").strip()

    if sub_ids:
        for r in conn.execute(
            f"SELECT id, full_name, username FROM subscribers WHERE id IN ({_ph(sub_ids)})",
            sub_ids,
        ).fetchall():
            sub_names[r["id"]] = (r["full_name"] or "").strip() or (r["username"] or "").strip()

    if cu_ids:
        for r in conn.execute(
            f"SELECT id, display_name, mobile FROM card_users WHERE id IN ({_ph(cu_ids)})",
            cu_ids,
        ).fetchall():
            cu_names[r["id"]] = (r["display_name"] or "").strip() or (r["mobile"] or "").strip()

    for w in wallets:
        ot  = w.get("owner_type", "")
        oid = w.get("owner_id")
        if ot == "company":
            w["owner_name"] = "الشركة"
        elif ot == "manager" and oid:
            w["owner_name"] = mgr_names.get(oid, "")
        elif ot == "distributor" and oid:
            w["owner_name"] = dist_names.get(oid, "")
        elif ot == "subscriber" and oid:
            w["owner_name"] = sub_names.get(oid, "")
        elif ot == "card_user" and oid:
            w["owner_name"] = cu_names.get(oid, "")
        else:
            w["owner_name"] = ""
    return wallets


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
    wallets = _enrich_wallets_names(svc.wallets(tenant_id=tenant_id, limit=150))
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
