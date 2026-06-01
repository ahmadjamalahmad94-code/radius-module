"""Hub 3 — السجل والتقارير المحاسبية.

تجميع واجهة فقط: يعرض السجل المالي (ledger) والتقارير المحاسبية
(reports) داخل صفحة واحدة بتبويبين. لا يغيّر أي منطق محاسبي:
القراءة تتم عبر نفس خدمة `AccountingService`، ودفتر القيود يبقى
append-only (التصحيح بقيد عكسي عبر المسار الأصلي finance_ledger_void)،
والتصدير (CSV/XLSX/PDF) واللقطات الثابتة تبقى على مساراتها المستقلة،
وصفحة المالية لكل مستخدم (/users/<username>/finance) تبقى مستقلة.
"""
from __future__ import annotations

from flask import Blueprint, flash, render_template, request

from ..core.errors import RadiusValidationError
from ..services.accounting import service_from_context

# Single source of truth for the report catalogue lives on the legacy
# module; reuse it so the hub never drifts from the standalone page.
from .accounting import _REPORTS

_BASE = "/finance/accounting"
_TABS = ("ledger", "reports")

# Mirrors the entry-type filter offered by the standalone ledger page.
_ENTRY_TYPES = ("payment", "loan", "settlement", "void", "reversal", "correction")


def register_finance_accounting_routes(bp: Blueprint) -> None:
    bp.add_url_rule(_BASE, "accounting_hub", accounting_hub, methods=["GET"])


def accounting_hub():
    svc = service_from_context()

    tab = (request.args.get("tab") or "ledger").strip()
    if tab not in _TABS:
        tab = "ledger"

    # ── Ledger context (same call + filters as radius.finance_ledger) ──
    entry_type = (request.args.get("entry_type") or "").strip()
    subscriber_id = request.args.get("subscriber_id")
    try:
        ledger_items = svc.list_ledger(
            entry_type=entry_type,
            subscriber_id=int(subscriber_id) if subscriber_id else None,
            limit=300,
        )
    except (ValueError, RadiusValidationError) as exc:
        flash(getattr(exc, "message", str(exc)), "error")
        ledger_items = []

    # KPIs are a pure aggregation over the rows the legacy page already
    # fetched — no new query, no business logic.
    ledger_kpis = {
        "count": len(ledger_items),
        "payments": sum(1 for e in ledger_items if e.get("entry_type") == "payment"),
        "loans": sum(1 for e in ledger_items if e.get("entry_type") == "loan"),
        "corrections": sum(
            1 for e in ledger_items
            if e.get("entry_type") in ("void", "reversal", "correction")
        ),
    }

    # ── Reports context (same calls as radius.finance_reports) ──
    report_type = (request.args.get("type") or "daily").strip()
    if report_type not in _REPORTS:
        report_type = "daily"
    try:
        report_items = svc.reports(report_type=report_type)
        snapshots = svc.list_report_snapshots(report_type=report_type, limit=10)
    except RadiusValidationError as exc:
        flash(exc.message, "error")
        report_items = []
        snapshots = []

    return render_template(
        "radius/finance_accounting.html",
        tab=tab,
        entry_types=_ENTRY_TYPES,
        entry_type=entry_type,
        subscriber_id=subscriber_id or "",
        ledger_items=ledger_items,
        ledger_kpis=ledger_kpis,
        reports=_REPORTS,
        report_type=report_type,
        report_label=_REPORTS[report_type],
        report_items=report_items,
        snapshots=snapshots,
    )
