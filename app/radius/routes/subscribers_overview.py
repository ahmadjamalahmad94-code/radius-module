"""Subscribers Overview — read-only monthly/yearly snapshot.

Mirrors cards_overview (the route owns DATA aggregation only; every detail /
per-subscriber drill-down links out to the Finance section — see
SERVICES_COOKBOOK.md §14 + §20). Periods: **monthly + yearly only** (the
daily/weekly grains are deliberately dropped).

Metrics, per the operator's brief:
  • المُحصّل (دخل)   — accounting_repo.sales_summary           (collected from subscribers)
  • السلف (صرف)      — accounting_repo.loans_summary            (extended to subscribers)
  • التفعيل          — accounting_repo.activation_summary       (payments that granted time)
  • الجيجات          — accounting_repo.data_usage_summary       (consumed GB)
  • الكوتة           — per-plan allocation (point-in-time)
  • شو ضل / الديون   — accounting_repo.outstanding_summary      (open loans + negative balances, as-of-now)
"""
from __future__ import annotations

from flask import Blueprint, render_template, request, session

from ..db.connection import db
from ..db.repos import accounting_repo

_GB = 1073741824.0  # bytes per GB
_MB_PER_GB = 1024.0


def register_subscribers_overview_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/subscribers/overview",
        "subscribers_overview",
        subscribers_overview,
        methods=["GET"],
    )


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def subscribers_overview():
    """Lightweight subscribers overview: fast read-only snapshot."""
    period = (request.args.get("period") or "monthly").strip().lower()
    if period not in ("monthly", "yearly"):
        period = "monthly"
    overview = _subscribers_overview_snapshot(_tid(), period)
    return render_template("radius/subscribers_overview.html", **overview)


# ───────────────────────── helpers ─────────────────────────


def _short_period_label(period: str) -> str:
    """'2026-06' → '06' (month) ; '2026' → '2026' (year)."""
    p = str(period or "")
    return p[5:7] if len(p) >= 7 else p


def _bars(rows: list[dict], field: str, *, limit: int = 12) -> tuple[list[dict], float]:
    """Repo rows are DESC by period; return an ASC bar series + the max value.

    Each bar: {period, label, value, pct} where pct is height vs the series max.
    """
    asc = list(reversed(rows))[-limit:]
    values = [float(r.get(field) or 0) for r in asc]
    mx = max(values) if values else 0.0
    bars = []
    for row, value in zip(asc, values):
        bars.append(
            {
                "period": row.get("period"),
                "label": _short_period_label(row.get("period")),
                "value": value,
                "pct": (value / mx * 100.0) if mx else 0.0,
            }
        )
    return bars, mx


def _pick(rows: list[dict], selected: str | None) -> dict:
    """Pick the selected bucket (by period) for the KPI strip; default = latest."""
    if not rows:
        return {}
    if selected:
        for row in rows:
            if str(row.get("period")) == str(selected):
                return row
    return rows[0]  # rows are DESC → [0] is the latest bucket


def _subscriber_census(tenant_id: int) -> dict:
    row = db().execute(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN status = 'enabled'   THEN 1 ELSE 0 END), 0) AS enabled,
               COALESCE(SUM(CASE WHEN status = 'disabled'  THEN 1 ELSE 0 END), 0) AS disabled,
               COALESCE(SUM(CASE WHEN status = 'expired'   THEN 1 ELSE 0 END), 0) AS expired,
               COALESCE(SUM(CASE WHEN status = 'suspended' THEN 1 ELSE 0 END), 0) AS suspended
        FROM subscribers
        WHERE tenant_id = ? AND deleted_at IS NULL
        """,
        (tenant_id,),
    ).fetchone()
    online = db().execute(
        "SELECT COUNT(DISTINCT username) AS c FROM radacct "
        "WHERE tenant_id = ? AND acctstoptime IS NULL",
        (tenant_id,),
    ).fetchone()
    return {
        "total": int((row and row["total"]) or 0),
        "enabled": int((row and row["enabled"]) or 0),
        "disabled": int((row and row["disabled"]) or 0),
        "expired": int((row and row["expired"]) or 0),
        "suspended": int((row and row["suspended"]) or 0),
        "online": int((online and online["c"]) or 0),
    }


def _quota_allocation(tenant_id: int) -> list[dict]:
    """الكوتة — point-in-time allocated quota per active plan (operator chose
    quota = allocation, GB = consumption). per-plan allocated GB = quota × subs.
    """
    rows = db().execute(
        """
        SELECT p.id, p.name,
               COALESCE(p.monthly_combined_quota_mb, 0) AS monthly_mb,
               COALESCE(p.quota_total_mb, 0) AS total_mb,
               COUNT(s.id) AS subs
        FROM access_plans p
        LEFT JOIN subscribers s
          ON s.plan_id = p.id AND s.tenant_id = p.tenant_id AND s.deleted_at IS NULL
        WHERE p.tenant_id = ? AND p.deleted_at IS NULL
        GROUP BY p.id
        HAVING subs > 0
        ORDER BY subs DESC
        LIMIT 12
        """,
        (tenant_id,),
    ).fetchall()
    plans = []
    total_alloc = 0.0
    for row in rows:
        item = dict(row)
        per_sub_mb = float(item["monthly_mb"] or 0) or float(item["total_mb"] or 0)
        item["per_sub_mb"] = per_sub_mb
        item["allocated_gb"] = (per_sub_mb * int(item["subs"] or 0)) / _MB_PER_GB
        item["metered"] = per_sub_mb > 0
        total_alloc += item["allocated_gb"]
        plans.append(item)
    for item in plans:
        item["share_pct"] = (item["allocated_gb"] / total_alloc * 100.0) if total_alloc else 0.0
    return plans


def _subscribers_overview_snapshot(tenant_id: int, period: str) -> dict:
    grain = period  # "monthly" | "yearly"
    selected = request.args.get("month") if grain == "monthly" else request.args.get("year")

    # ── period-bucketed series (DESC by period) ──
    sales_rows = accounting_repo.sales_summary(tenant_id, grain=grain)        # المُحصّل
    loan_rows = accounting_repo.loans_summary(tenant_id, grain=grain)         # السلف
    act_rows = accounting_repo.activation_summary(tenant_id, grain=grain)     # التفعيل
    data_rows = accounting_repo.data_usage_summary(tenant_id, grain=grain)    # الجيجات
    for row in data_rows:
        row["gb"] = (float(row.get("bytes_in") or 0) + float(row.get("bytes_out") or 0)) / _GB

    # ── selected-bucket KPI numbers ──
    sales_sel = _pick(sales_rows, selected)
    loan_sel = _pick(loan_rows, selected)
    act_sel = _pick(act_rows, selected)
    data_sel = _pick(data_rows, selected)

    # ── ascending bar series for the mini-charts ──
    sales_bars, sales_max = _bars(sales_rows, "total")
    loan_bars, loan_max = _bars(loan_rows, "total")
    act_bars, act_max = _bars(act_rows, "amount")
    data_bars, data_max = _bars(data_rows, "gb")

    # ── point-in-time (as-of-now) ──
    outstanding = accounting_repo.outstanding_summary(tenant_id)
    census = _subscriber_census(tenant_id)
    quota = _quota_allocation(tenant_id)
    debtors = accounting_repo.top_debtors(tenant_id, limit=50)

    selected_label = (
        sales_sel.get("period")
        or loan_sel.get("period")
        or act_sel.get("period")
        or data_sel.get("period")
        or selected
        or "—"
    )

    # ── الفترات المتاحة للاختيار (سنوات أو أشهر) — union كل السلاسل ──
    period_options: list[str] = []
    for rows in (sales_rows, loan_rows, act_rows, data_rows):
        for row in rows:
            p = str(row.get("period") or "")
            if p and p not in period_options:
                period_options.append(p)
    period_options.sort(reverse=True)

    return {
        "period": period,
        "grain": grain,
        "selected_label": selected_label,
        "period_options": period_options,
        # دخل + صرف headline (الاثنين)
        "collected_total": float(sales_sel.get("total") or 0),   # المُحصّل من المشتركين
        "disbursed_total": float(loan_sel.get("total") or 0),    # المصروف (سلف) للمشتركين
        # collected / المُحصّل
        "sales_sel": sales_sel, "sales_bars": sales_bars, "sales_max": sales_max,
        # loans / السلف
        "loan_sel": loan_sel, "loan_bars": loan_bars, "loan_max": loan_max,
        # activation / التفعيل
        "act_sel": act_sel, "act_bars": act_bars, "act_max": act_max,
        # data / الجيجات
        "data_sel": data_sel, "data_bars": data_bars, "data_max": data_max,
        # outstanding / شو ضل
        "outstanding": outstanding,
        # census + quota + drill-down
        "census": census,
        "quota": quota,
        "debtors": debtors,
    }
