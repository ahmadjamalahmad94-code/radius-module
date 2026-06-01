"""Hub 2 — التحصيل والمدفوعات (Payment Collection) consolidated page.

UI-ONLY consolidation: renders the existing payment-collection
workflow (requests, review queue, reconciliation) as tabs of one hub,
with the settings screen in a floating <dialog> modal. It does NOT
change any payment logic — reads go through the same repos, the
settings POST still targets the original endpoint, and the request
detail page + approve/reject/apply-service actions stay standalone
(reached from the requests table).

See docs/finance_hubs/FINANCE_HUBS_PLAN.md (Hub 2).
"""
from __future__ import annotations

from flask import Blueprint, flash, render_template, request, session

from ..db.repos.payments_repo import (
    PaymentReconciliationRepository,
    PaymentRequestRepository,
    PaymentSettingsRepository,
)

_BASE = "/finance/collection"
_TABS = ("requests", "review", "reconciliation", "settings")

# Stable status set used for the requests filter (mirrors
# payments_repo.PAYMENT_STATUSES).
_STATUSES = (
    "pending", "proof_submitted", "under_review", "paid",
    "rejected", "expired", "cancelled", "failed",
)


def register_finance_collection_routes(bp: Blueprint) -> None:
    bp.add_url_rule(_BASE, "collection_hub", collection_hub, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def collection_hub():
    tid = _tid()
    tab = request.args.get("tab", "requests").strip()
    if tab not in _TABS:
        tab = "requests"

    status = (request.args.get("status") or "").strip()
    purpose = (request.args.get("purpose") or "").strip()
    payer_type = (request.args.get("payer_type") or "").strip()

    req_repo = PaymentRequestRepository()
    try:
        requests_list = req_repo.list(
            tid, status=status, purpose=purpose, payer_type=payer_type
        )
    except ValueError as exc:
        flash(f"فلتر غير صالح: {exc}", "warning")
        requests_list = []

    return render_template(
        "radius/finance_collection.html",
        tab=tab,
        statuses=_STATUSES,
        status=status,
        purpose=purpose,
        payer_type=payer_type,
        requests_list=requests_list,
        review_list=req_repo.list_for_review(tid),
        reconciliation=PaymentReconciliationRepository().summary(tenant_id=tid),
        settings=PaymentSettingsRepository().get(tid),
    )
