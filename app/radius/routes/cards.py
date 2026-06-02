"""Cards routes — batches + generate + list.

RM-H4: extended generate form with full AdvRadius batch options +
metadata JSON for future fields.
"""
from __future__ import annotations

import csv
import io
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError, RadiusValidationError
from ..db.connection import db
from ..db.helpers import json_dump
from ..db.repos import admins_repo, operations_repo
from ..services.card_checker import check_card
from ..services.cards import get_cards_service
from ..services import cards_import_engine
from ..services.operations import get_operations_service
from ..services.plans import get_plans_service
from .speed_rules_ui import create_staged_speed_rules, handle_embedded_speed_rule, speed_rules_panel


def register_cards_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/overview", "cards_overview", cards_overview, methods=["GET"])
    bp.add_url_rule("/cards/checker", "cards_checker", cards_checker, methods=["GET", "POST"])
    # ـ R13.A.1: JSON API لـ Card Checker AJAX (foundation للـ UI rebuild) ـ
    bp.add_url_rule("/cards/checker/api/lookup", "cards_checker_api_lookup",
                     cards_checker_api_lookup, methods=["GET"])
    # On-demand password reveal — separate endpoint so the password
    # never lives in the default Checker payload. Role-gated + audited.
    bp.add_url_rule("/cards/checker/api/reveal-password", "cards_checker_api_reveal_password",
                     cards_checker_api_reveal_password, methods=["POST"])
    # ـ R13.A.2: v2 template preview — side-by-side with v1 حتى A.4 ـ
    bp.add_url_rule("/cards/checker/v2", "cards_checker_v2",
                     cards_checker_v2, methods=["GET"])
    bp.add_url_rule("/cards/batches", "cards_batches", cards_batches, methods=["GET"])
    bp.add_url_rule("/cards/batches/bulk", "cards_batches_bulk", cards_batches_bulk, methods=["POST"])
    bp.add_url_rule("/cards/batches/export.csv", "cards_batches_export_csv", cards_batches_export_csv, methods=["GET"])
    bp.add_url_rule("/cards/batches/export.xlsx", "cards_batches_export_xlsx", cards_batches_export_xlsx, methods=["GET"])
    bp.add_url_rule("/cards/batches/export.pdf", "cards_batches_export_pdf", cards_batches_export_pdf, methods=["GET"])
    bp.add_url_rule("/cards/batches/import", "cards_batches_import", cards_batches_import, methods=["GET", "POST"])
    # Intelligent multi-format preview — accepts an uploaded
    # CSV/XLSX/PDF and returns parsed cards as canonical
    # username,password CSV so the existing import flow can consume
    # the result unchanged.
    bp.add_url_rule(
        "/cards/batches/import/preview",
        "cards_batches_import_preview",
        cards_batches_import_preview,
        methods=["POST"],
    )
    bp.add_url_rule("/cards/generate", "cards_generate", cards_generate, methods=["GET", "POST"])
    bp.add_url_rule("/cards/generate/progress", "cards_generate_progress_start", cards_generate_progress_start, methods=["POST"])
    bp.add_url_rule("/cards/generate/progress/<job_id>", "cards_generate_progress_status", cards_generate_progress_status, methods=["GET"])
    bp.add_url_rule("/cards", "cards_list", cards_list, methods=["GET"])
    bp.add_url_rule("/cards/<int:card_id>/revoke", "cards_revoke", cards_revoke, methods=["POST"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/edit", "cards_batch_edit", cards_batch_edit, methods=["GET", "POST"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/cards/actions", "cards_batch_cards_actions", cards_batch_cards_actions, methods=["POST"])
    bp.add_url_rule("/cards/batches/<int:batch_id>/cards", "cards_of_batch", cards_of_batch, methods=["GET"])


def cards_overview():
    """Lightweight cards overview: fast read-only snapshot."""
    overview = _cards_overview_snapshot(_tid())
    return render_template("radius/cards_overview.html", **overview)


def _row_dict(row) -> dict:
    return dict(row) if row else {}


def _cards_overview_snapshot(tenant_id: int) -> dict:
    from ..db.repos import cards_repo

    today = datetime.utcnow().strftime("%Y-%m-%d")
    month = datetime.utcnow().strftime("%Y-%m")
    last_week = [
        (datetime.utcnow() - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(6, -1, -1)
    ]

    counts = _row_dict(db().execute(
        """
        SELECT
          COUNT(*) AS total,
          COALESCE(SUM(CASE WHEN revoked = 0 AND used = 0
             AND (expire_at IS NULL OR expire_at >= datetime('now')) THEN 1 ELSE 0 END), 0) AS available,
          COALESCE(SUM(CASE WHEN used = 1 THEN 1 ELSE 0 END), 0) AS used,
          COALESCE(SUM(CASE WHEN revoked = 1 THEN 1 ELSE 0 END), 0) AS revoked,
          COALESCE(SUM(CASE WHEN revoked = 0 AND expire_at IS NOT NULL
             AND expire_at < datetime('now') THEN 1 ELSE 0 END), 0) AS expired
        FROM cards
        WHERE tenant_id = ?
          AND deleted_at IS NULL
        """,
        (tenant_id,),
    ).fetchone())
    batch_count = int(db().execute(
        "SELECT COUNT(*) AS c FROM card_batches WHERE tenant_id=? AND deleted_at IS NULL",
        (tenant_id,),
    ).fetchone()["c"] or 0)

    totals = cards_repo.batch_operations_totals(tenant_id)
    recent_batches = cards_repo.list_batch_operations(tenant_id, limit=5)

    trend_rows = db().execute(
        """
        SELECT SUBSTR(COALESCE(first_used_at, ''), 1, 10) AS day,
               COUNT(*) AS used_count
        FROM cards
        WHERE tenant_id = ?
          AND used = 1
          AND first_used_at IS NOT NULL
          AND SUBSTR(first_used_at, 1, 10) >= ?
        GROUP BY day
        """,
        (tenant_id, last_week[0]),
    ).fetchall()
    trend_map = {row["day"]: int(row["used_count"] or 0) for row in trend_rows}
    trend = [
        {
            "day": day,
            "label": day[5:],
            "used": trend_map.get(day, 0),
        }
        for day in last_week
    ]
    trend_max = max([item["used"] for item in trend] or [0])

    printed_stock_packages = [
        dict(row)
        for row in db().execute(
            """
            WITH purchased_cards AS (
              SELECT tenant_id, card_id
              FROM card_user_purchases
              WHERE status = 'completed'
              GROUP BY tenant_id, card_id
            ),
            online_cards AS (
              SELECT tenant_id, username,
                     COUNT(*) AS online_sessions
              FROM radacct
              WHERE acctstoptime IS NULL
              GROUP BY tenant_id, username
            )
            SELECT b.id AS batch_id,
                   b.batch_code,
                   COALESCE(NULLIF(b.package_name, ''), p.name, b.batch_code, 'بدون حزمة') AS package_name,
                   COUNT(c.id) AS total_cards,
                   COALESCE(SUM(CASE WHEN c.revoked = 0 AND c.used = 0
                      AND (c.expire_at IS NULL OR c.expire_at >= datetime('now')) THEN 1 ELSE 0 END), 0) AS available_cards,
                   COALESCE(SUM(CASE
                      WHEN c.revoked = 0
                       AND c.expire_at IS NOT NULL AND c.expire_at < datetime('now')
                       THEN 1 ELSE 0 END), 0) AS expired_cards,
                   COALESCE(SUM(CASE
                      WHEN c.revoked = 0 AND c.used = 1
                       AND (c.expire_at IS NULL OR c.expire_at >= datetime('now'))
                       AND COALESCE(oc.online_sessions, 0) = 0 THEN 1 ELSE 0 END), 0) AS used_offline_cards,
                   COALESCE(SUM(CASE
                      WHEN c.revoked = 0
                       AND COALESCE(oc.online_sessions, 0) > 0 THEN 1 ELSE 0 END), 0) AS online_cards
            FROM card_batches b
            LEFT JOIN cards c
              ON c.tenant_id = b.tenant_id AND c.batch_id = b.id AND c.deleted_at IS NULL
            LEFT JOIN purchased_cards cup
              ON cup.tenant_id = c.tenant_id AND cup.card_id = c.id
            LEFT JOIN online_cards oc
              ON oc.tenant_id = c.tenant_id AND oc.username = c.username
            LEFT JOIN access_plans p
              ON p.tenant_id = b.tenant_id AND p.id = b.plan_id
            WHERE b.tenant_id = ?
              AND b.deleted_at IS NULL
              AND COALESCE(b.created_by, '') != 'card_marketplace'
              AND cup.card_id IS NULL
            GROUP BY b.id
            HAVING total_cards > 0
            ORDER BY b.created_at DESC, b.id DESC
            LIMIT 8
            """,
            (tenant_id,),
        ).fetchall()
    ]

    electronic_stock_packages = [
        dict(row)
        for row in db().execute(
            """
            WITH online_cards AS (
              SELECT tenant_id, username,
                     COUNT(*) AS online_sessions
              FROM radacct
              WHERE acctstoptime IS NULL
              GROUP BY tenant_id, username
            )
            SELECT pkg.id AS package_id,
                   pkg.name AS package_name,
                   COUNT(c.id) AS total_cards,
                   0 AS available_cards,
                   COALESCE(SUM(CASE
                      WHEN c.revoked = 0
                       AND c.expire_at IS NOT NULL AND c.expire_at < datetime('now')
                       THEN 1 ELSE 0 END), 0) AS expired_cards,
                   COALESCE(SUM(CASE
                      WHEN c.revoked = 0 AND c.used = 1
                       AND (c.expire_at IS NULL OR c.expire_at >= datetime('now'))
                       AND COALESCE(oc.online_sessions, 0) = 0 THEN 1 ELSE 0 END), 0) AS used_offline_cards,
                   COALESCE(SUM(CASE
                      WHEN c.revoked = 0
                       AND COALESCE(oc.online_sessions, 0) > 0 THEN 1 ELSE 0 END), 0) AS online_cards
            FROM card_marketplace_packages pkg
            LEFT JOIN card_user_purchases cup
              ON cup.tenant_id = pkg.tenant_id
             AND cup.package_id = pkg.id
             AND cup.status = 'completed'
            LEFT JOIN cards c
              ON c.tenant_id = cup.tenant_id
             AND c.id = cup.card_id
             AND c.deleted_at IS NULL
            LEFT JOIN online_cards oc
              ON oc.tenant_id = c.tenant_id AND oc.username = c.username
            WHERE pkg.tenant_id = ?
            GROUP BY pkg.id
            ORDER BY pkg.active DESC, pkg.id DESC
            LIMIT 8
            """,
            (tenant_id,),
        ).fetchall()
    ]

    last_used = _row_dict(db().execute(
        """
        SELECT c.username, c.first_used_at, b.batch_code, COALESCE(p.name, b.package_name, '') AS plan_name
        FROM cards c
        LEFT JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        LEFT JOIN access_plans p ON p.tenant_id = c.tenant_id AND p.id = c.plan_id
        WHERE c.tenant_id = ?
          AND c.used = 1
          AND c.first_used_at IS NOT NULL
        ORDER BY c.first_used_at DESC
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone())
    last_batch = _row_dict(db().execute(
        """
        SELECT batch_code, package_name, created_at, generated
        FROM card_batches
        WHERE tenant_id = ?
          AND deleted_at IS NULL
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone())
    online_cards = int(db().execute(
        """
        SELECT COUNT(DISTINCT r.username) AS c
        FROM radacct r
        JOIN cards c ON c.tenant_id = r.tenant_id AND c.username = r.username
        WHERE r.tenant_id = ?
          AND r.acctstoptime IS NULL
        """,
        (tenant_id,),
    ).fetchone()["c"] or 0)
    sales = _cards_sales_snapshot(tenant_id)
    created_today = int(db().execute(
        """
        SELECT COUNT(*) AS c
        FROM card_batches
        WHERE tenant_id = ?
          AND deleted_at IS NULL
          AND SUBSTR(COALESCE(created_at, ''), 1, 10) = ?
        """,
        (tenant_id, today),
    ).fetchone()["c"] or 0)

    available = int(counts.get("available") or 0)
    total = int(counts.get("total") or 0)
    used = int(counts.get("used") or 0)
    expired = int(counts.get("expired") or 0)
    revoked = int(counts.get("revoked") or 0)
    used_today = int(totals.get("used_today") or 0)
    alerts = []
    if total and available == 0:
        alerts.append({"level": "red", "text": "لا يوجد مخزون كروت متاح حاليًا."})
    elif 0 < available <= 10:
        alerts.append({"level": "amber", "text": f"المخزون المتاح منخفض: {available} كرت فقط."})
    for package in printed_stock_packages:
        package_total = int(package.get("total_cards") or 0)
        package_available = int(package.get("available_cards") or 0)
        if package_total and package_available <= max(3, int(package_total * 0.1)):
            alerts.append({
                "level": "amber",
                "text": f"{package.get('package_name')} قريب من النفاد: {package_available} متاح.",
            })
            break
    if expired:
        alerts.append({"level": "red", "text": f"{expired} كرت منتهي يحتاج مراجعة."})
    if revoked:
        alerts.append({"level": "grey", "text": f"{revoked} كرت محظور ضمن المخزون."})
    if not alerts:
        alerts.append({"level": "green", "text": "وضع الكروت مستقر ولا توجد ملاحظات عاجلة."})

    return {
        "cards": {
            "total": total,
            "available": available,
            "used": used,
            "expired": expired,
            "revoked": revoked,
            "batches": batch_count,
        },
        "money": {
            "today": float(totals.get("value_today") or 0),
            "month": float(totals.get("value_month") or 0),
            "configured": float(totals.get("configured_value") or 0),
        },
        "activity": {
            "used_today": used_today,
            "used_month": int(totals.get("used_month") or 0),
            "online_cards": online_cards,
            "created_today": created_today,
            "last_used": last_used,
            "last_batch": last_batch,
        },
        "recent_batches": recent_batches,
        "sales": sales,
        "trend": trend,
        "trend_max": trend_max,
        "stock_packages": {
            "printed": printed_stock_packages,
            "electronic": electronic_stock_packages,
        },
        "alerts": alerts[:4],
    }


def _parse_day(value: str, fallback: date) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


def _parse_month(value: str, fallback: date) -> str:
    try:
        datetime.strptime(value, "%Y-%m")
        return value
    except (TypeError, ValueError):
        return fallback.strftime("%Y-%m")


def _parse_year(value: str, fallback: date) -> str:
    text = str(value or "").strip()
    if text.isdigit() and 2000 <= int(text) <= 2100:
        return text
    return str(fallback.year)


def _saturday_week_start(value: date) -> date:
    return value - timedelta(days=(value.weekday() + 2) % 7)


def _saturday_week_one(year: int) -> date:
    return _saturday_week_start(date(year, 1, 1))


def _saturday_week_value(start: date) -> str:
    year = start.year
    if start < _saturday_week_one(year):
        year -= 1
    week = ((start - _saturday_week_one(year)).days // 7) + 1
    return f"{year}-W{week:02d}"


def _parse_week(value: str, fallback: date) -> tuple[str, date]:
    fallback_start = _saturday_week_start(fallback)
    fallback_value = _saturday_week_value(fallback_start)
    text = str(value or "").strip()
    try:
        year_text, week_text = text.split("-W", 1)
        start = _saturday_week_one(int(year_text)) + timedelta(days=(int(week_text) - 1) * 7)
        return _saturday_week_value(start), start
    except (TypeError, ValueError):
        return fallback_value, fallback_start


def _sales_period_filters() -> dict:
    today = datetime.utcnow().date()
    daily = _parse_day(request.args.get("sales_day") or "", today)
    week_value, week_start = _parse_week(request.args.get("sales_week") or "", today)
    month_value = _parse_month(request.args.get("sales_month") or "", today)
    year_value = _parse_year(request.args.get("sales_year") or "", today)
    return {
        "inputs": {
            "daily": daily.isoformat(),
            "weekly": week_value,
            "monthly": month_value,
            "yearly": year_value,
        },
        "daily": {"value": daily.isoformat(), "label": daily.isoformat()},
        "weekly": {
            "value": week_start.isoformat(),
            "end": (week_start + timedelta(days=7)).isoformat(),
            "label": f"{week_start.strftime('%Y-%m-%d')} - {(week_start + timedelta(days=6)).strftime('%Y-%m-%d')}",
        },
        "monthly": {"value": month_value, "label": month_value},
        "yearly": {"value": year_value, "label": year_value},
    }


def _period_condition(column: str, period: str, filters: dict) -> tuple[str, tuple[object, ...]]:
    if period == "daily":
        return f"SUBSTR(COALESCE({column}, ''), 1, 10) = ?", (filters["daily"]["value"],)
    if period == "weekly":
        return (
            f"SUBSTR(COALESCE({column}, ''), 1, 10) >= ? AND SUBSTR(COALESCE({column}, ''), 1, 10) < ?",
            (filters["weekly"]["value"], filters["weekly"]["end"]),
        )
    if period == "monthly":
        return f"SUBSTR(COALESCE({column}, ''), 1, 7) = ?", (filters["monthly"]["value"],)
    return f"SUBSTR(COALESCE({column}, ''), 1, 4) = ?", (filters["yearly"]["value"],)


def _printed_sales_total(tenant_id: int, period: str, filters: dict) -> dict:
    where, values = _period_condition("c.first_used_at", period, filters)
    row = db().execute(
        f"""
        SELECT COUNT(c.id) AS count,
               COALESCE(SUM(CASE
                 WHEN b.price_per_card > 0 THEN b.price_per_card
                 WHEN b.total_price > 0 AND b.generated > 0 THEN b.total_price * 1.0 / b.generated
                 ELSE 0
               END), 0) AS amount
        FROM cards c
        JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        LEFT JOIN card_user_purchases p
          ON p.tenant_id = c.tenant_id AND p.card_id = c.id AND p.status = 'completed'
        WHERE c.tenant_id = ?
          AND c.deleted_at IS NULL
          AND c.used = 1
          AND p.id IS NULL
          AND {where}
        """,
        (tenant_id, *values),
    ).fetchone()
    return {"count": int(row["count"] or 0), "amount": float(row["amount"] or 0)}


def _electronic_sales_total(tenant_id: int, period: str, filters: dict) -> dict:
    where, values = _period_condition("p.created_at", period, filters)
    row = db().execute(
        f"""
        SELECT COUNT(p.id) AS count,
               COALESCE(SUM(p.amount_minor), 0) AS amount_minor
        FROM card_user_purchases p
        WHERE p.tenant_id = ?
          AND p.status = 'completed'
          AND {where}
        """,
        (tenant_id, *values),
    ).fetchone()
    return {
        "count": int(row["count"] or 0),
        "amount": float(row["amount_minor"] or 0) / 100.0,
    }


def _recent_printed_sales(tenant_id: int, limit: int = 8, period: str | None = None, filters: dict | None = None) -> list[dict]:
    period_sql = ""
    params: list[object] = [tenant_id]
    if period:
        where, values = _period_condition("c.first_used_at", period, filters or _sales_period_filters())
        period_sql = f"AND {where}"
        params.extend(values)
    params.append(int(limit))
    return [
        dict(row)
        for row in db().execute(
            f"""
            SELECT 'printed' AS sale_type,
                   c.id AS card_id,
                   c.username,
                   c.password,
                   c.first_used_at AS sold_at,
                   b.batch_code,
                   COALESCE(p.name, b.package_name, '') AS plan_name,
                   CASE
                     WHEN b.price_per_card > 0 THEN b.price_per_card
                     WHEN b.total_price > 0 AND b.generated > 0 THEN b.total_price * 1.0 / b.generated
                     ELSE 0
                   END AS amount,
                   COALESCE(p.currency, 'JOD') AS currency,
                   '' AS buyer_name
            FROM cards c
            JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
            LEFT JOIN access_plans p ON p.tenant_id = c.tenant_id AND p.id = c.plan_id
            LEFT JOIN card_user_purchases cup
              ON cup.tenant_id = c.tenant_id AND cup.card_id = c.id AND cup.status = 'completed'
            WHERE c.tenant_id = ?
              AND c.deleted_at IS NULL
              AND c.used = 1
              AND c.first_used_at IS NOT NULL
              AND cup.id IS NULL
              {period_sql}
            ORDER BY c.first_used_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    ]


def _recent_electronic_sales(tenant_id: int, limit: int = 8, period: str | None = None, filters: dict | None = None) -> list[dict]:
    period_sql = ""
    params: list[object] = [tenant_id]
    if period:
        where, values = _period_condition("p.created_at", period, filters or _sales_period_filters())
        period_sql = f"AND {where}"
        params.extend(values)
    params.append(int(limit))
    return [
        dict(row)
        for row in db().execute(
            f"""
            SELECT 'electronic' AS sale_type,
                   c.id AS card_id,
                   c.username,
                   c.password,
                   p.created_at AS sold_at,
                   b.batch_code,
                   COALESCE(pkg.name, ap.name, b.package_name, '') AS plan_name,
                   p.amount_minor / 100.0 AS amount,
                   p.currency,
                   cu.display_name AS buyer_name
            FROM card_user_purchases p
            LEFT JOIN cards c ON c.tenant_id = p.tenant_id AND c.id = p.card_id
            LEFT JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
            LEFT JOIN card_marketplace_packages pkg
              ON pkg.tenant_id = p.tenant_id AND pkg.id = p.package_id
            LEFT JOIN access_plans ap ON ap.tenant_id = c.tenant_id AND ap.id = c.plan_id
            LEFT JOIN card_users cu ON cu.tenant_id = p.tenant_id AND cu.id = p.card_user_id
            WHERE p.tenant_id = ?
              AND p.status = 'completed'
              {period_sql}
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    ]


def _cards_sales_snapshot(tenant_id: int) -> dict:
    periods = ("daily", "weekly", "monthly", "yearly")
    filters = _sales_period_filters()
    currency_row = db().execute(
        "SELECT currency FROM tenants WHERE id = ?",
        (tenant_id,),
    ).fetchone()
    currency = str((currency_row and currency_row["currency"]) or "ILS").upper()
    printed = {period: _printed_sales_total(tenant_id, period, filters) for period in periods}
    electronic = {period: _electronic_sales_total(tenant_id, period, filters) for period in periods}
    total = {
        period: {
            "count": printed[period]["count"] + electronic[period]["count"],
            "amount": printed[period]["amount"] + electronic[period]["amount"],
        }
        for period in periods
    }
    details = {}
    for period in periods:
        period_rows = _recent_printed_sales(tenant_id, 40, period, filters) + _recent_electronic_sales(tenant_id, 40, period, filters)
        period_rows.sort(key=lambda item: str(item.get("sold_at") or ""), reverse=True)
        details[period] = period_rows[:60]
    period_labels = {
        "daily": "اليوم",
        "weekly": "الأسبوع",
        "monthly": "الشهر",
        "yearly": "السنة",
    }
    return {
        "printed": printed,
        "electronic": electronic,
        "total": total,
        "details": details,
        "currency": currency,
        "filters": filters["inputs"],
        "periods": [
            {"key": period, "label": period_labels[period], "range": filters[period]["label"]}
            for period in periods
        ],
    }


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _form_int(name: str, d: int = 0) -> int:
    try: return int(request.form.get(name) or d)
    except (TypeError, ValueError): return d


def _form_float(name: str, d: float = 0.0) -> float:
    try: return float(request.form.get(name) or d)
    except (TypeError, ValueError): return d


def _form_bool(name: str) -> bool:
    return request.form.get(name, "") in ("1", "on", "true", "yes")


def _form_str(name: str) -> str:
    return (request.form.get(name) or "").strip()


_GENERATE_JOBS: dict[str, dict] = {}
_GENERATE_JOBS_LOCK = threading.Lock()


def _set_generate_job(job_id: str, **changes) -> dict:
    with _GENERATE_JOBS_LOCK:
        job = _GENERATE_JOBS.setdefault(job_id, {})
        job.update(changes)
        job["updated_at"] = time.time()
        return dict(job)


def _get_generate_job(job_id: str) -> dict | None:
    with _GENERATE_JOBS_LOCK:
        job = _GENERATE_JOBS.get(job_id)
        return dict(job) if job else None


def _cleanup_generate_jobs() -> None:
    cutoff = time.time() - 3600
    with _GENERATE_JOBS_LOCK:
        for key, job in list(_GENERATE_JOBS.items()):
            if float(job.get("updated_at") or job.get("created_at") or 0) < cutoff:
                _GENERATE_JOBS.pop(key, None)


def _query_int(name: str) -> int | None:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _batch_filters_from_request() -> dict:
    return {
        "q": (request.args.get("q") or "").strip()[:120],
        "status": (request.args.get("status") or "").strip()[:40],
        "plan_id": _query_int("plan_id"),
        "manager": (request.args.get("manager") or "").strip()[:80],
        "distributor_id": _query_int("distributor_id"),
    }


def _page_args() -> tuple[int, int]:
    try:
        per_page = int(request.args.get("per_page") or "20")
    except ValueError:
        per_page = 20
    if per_page not in (10, 20, 50, 100):
        per_page = 20
    try:
        page = max(1, int(request.args.get("page") or "1"))
    except ValueError:
        page = 1
    return page, per_page


def _parse_import_cards_text(raw: str) -> list[dict[str, str]]:
    text = (raw or "").strip()
    if not text:
        return []
    reader = csv.reader(io.StringIO(text))
    rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []

    header = [cell.lower().replace(" ", "_") for cell in rows[0]]
    has_header = any(cell in {"username", "user", "login", "card", "password", "pass"} for cell in header)
    if has_header:
        username_idx = next((i for i, cell in enumerate(header) if cell in {"username", "user", "login", "card"}), 0)
        password_idx = next((i for i, cell in enumerate(header) if cell in {"password", "pass"}), None)
        data_rows = rows[1:]
    else:
        username_idx = 0
        password_idx = 1 if len(rows[0]) > 1 else None
        data_rows = rows

    cards: list[dict[str, str]] = []
    for row in data_rows:
        username = row[username_idx].strip() if username_idx < len(row) else ""
        password = row[password_idx].strip() if password_idx is not None and password_idx < len(row) else ""
        if username:
            cards.append({"username": username, "password": password})
    return cards


def _import_form_context() -> dict:
    return {
        "plans": list(get_plans_service().list(limit=500)),
        "form": request.form,
        "preview_rows": [],
    }


def _collect_batch_options() -> dict:
    """جمع كل خيارات AdvRadius من POST. dict جاهز للتمرير لـ generate_batch."""
    batch_type = _form_str("batch_type") or "printed"
    if batch_type not in {"printed", "electronic"}:
        batch_type = "printed"
    quota_value = _form_int("quick_quota_value")
    quota_unit = (_form_str("quick_quota_unit") or "mb").lower()
    total_quota_mb = _form_int("total_quota_mb")
    if quota_value > 0:
        total_quota_mb = quota_value * 1024 if quota_unit == "gb" else quota_value
    validity_value = _form_int("quick_validity_value")
    validity_unit = (_form_str("quick_validity_unit") or "days").lower()
    count_by_seconds = _form_bool("count_by_seconds")
    validity_after_first_login_days = _form_int("validity_after_first_login_days")
    if validity_value > 0:
        validity_minutes = validity_value
        if validity_unit == "hours":
            validity_minutes = validity_value * 60
        elif validity_unit == "days":
            validity_minutes = validity_value * 1440
        validity_after_first_login_days = max(1, (validity_minutes + 1439) // 1440)
    if count_by_seconds and validity_after_first_login_days <= 0:
        raise RadiusValidationError("عند اختيار المحاسبة بالثانية يجب تحديد صلاحية البطاقة بعد أول اتصال.")
    metadata = {
        "batch_type": batch_type,
        "quota": {"value": quota_value, "unit": quota_unit} if quota_value > 0 else None,
        "seconds_validity": {"value": validity_value, "unit": validity_unit} if validity_value > 0 else None,
    }
    # مدة البطاقة: نقبل إمّا time_limit_minutes (من شاشة التوليد) أو time_value/time_unit
    # (من تعديل الحزمة). نطبّع القيمة إلى value+unit قبل التمرير لـ generate_batch.
    time_minutes = _form_int("time_limit_minutes")
    time_value = _form_int("time_value")
    time_unit = _form_str("time_unit") or "days"
    if time_minutes > 0:
        if time_minutes % 1440 == 0:
            time_value = time_minutes // 1440
            time_unit = "days"
        elif time_minutes % 60 == 0:
            time_value = time_minutes // 60
            time_unit = "hours"
        else:
            time_value = time_minutes
            time_unit = "minutes"
    return {
        # توليد
        "username_prefix":           _form_str("username_prefix"),
        "username_suffix":           _form_str("username_suffix"),
        "username_length":           _form_int("username_length", 8),
        "password_length":           _form_int("password_length", 6),
        "password_charset":          _form_str("password_charset") or "digits",
        "password_generation_type":  _form_str("password_generation_type") or "medium",
        "include_batch_number":      _form_bool("include_batch_number"),
        "starts_with_or_ends_with":  _form_str("starts_with_or_ends_with"),
        "prefix_or_suffix_value":    _form_str("prefix_or_suffix_value"),
        "random_generation_enabled": _form_bool("random_generation_enabled") or True,
        # وقت
        "time_value":                time_value,
        "time_unit":                 time_unit,
        "device_count":              max(1, _form_int("device_count", 1)),
        "duration_mode":             _form_str("duration_mode") or "time_unit",
        "validity_after_first_login_days": validity_after_first_login_days,
        "count_by_seconds":          count_by_seconds,
        "count_from_first_connect":  _form_bool("count_from_first_connect"),
        # السلوك عند انتهاء الكوتا + خيارات
        "on_quota_exhaust":          _form_str("on_quota_exhaust") or "stop",
        "auto_renew_after_first_use":            _form_bool("auto_renew_after_first_use"),
        "transfer_to_student_status_on_connect": _form_bool("transfer_to_student_status_on_connect"),
        "close_user_session_on_disconnect":      _form_bool("close_user_session_on_disconnect"),
        "allow_entry_by_previous_card_palestine":_form_bool("allow_entry_by_previous_card_palestine"),
        "switch_to_mac_on_connect":  _form_bool("switch_to_mac_on_connect"),
        "lock_to_mac_on_close":      _form_bool("lock_to_mac_on_close"),
        "phone_only_login":          _form_bool("phone_only_login"),
        # تجاري (مرجعي) + meta
        "price_per_card":            _form_float("price_per_card"),
        "price_bulk":                _form_float("price_bulk"),
        "total_price":               _form_float("total_price"),
        "total_quota_mb":            total_quota_mb,
        "package_name":              _form_str("package_name"),
        "service_name":              _form_str("service_name"),
        "manager_id":                _form_int("manager_id"),
        "distributor_id":            _form_int("distributor_id") or None,
        "source_type":               "generated",
        "metadata":                  json_dump(metadata),
        "notes":                     _form_str("notes"),
    }


def _pending_batch_speed_rule_requested(form) -> bool:
    fields = (
        "sr_name",
        "sr_starts_at_time",
        "sr_ends_at_time",
        "sr_speed_down_kbps",
        "sr_speed_up_kbps",
        "sr_source_schedule_id",
    )
    if any((form.get(name) or "").strip() for name in fields):
        return True
    try:
        return bool(form.getlist("sr_days"))
    except AttributeError:
        return bool((form.get("sr_days") or "").strip())


def _apply_pending_batch_speed_rule(batch, form, *, tenant_id: int | None = None, actor: str | None = None) -> None:
    """Create the optional advanced speed rule after a new batch gets an id."""
    created = create_staged_speed_rules(
        tenant_id=tenant_id if tenant_id is not None else _tid(),
        actor=actor or _actor(),
        form=form,
        target_type="card_batch",
        plan_id=batch.plan_id,
        card_batch_id=batch.id,
        metadata={"created_with_card_batch": True},
    )
    if created:
        return
    if not _pending_batch_speed_rule_requested(form):
        return
    speed_form = form.copy()
    if not (speed_form.get("_speed_rule_action") or "").strip():
        manual_fields = (
            "sr_name",
            "sr_starts_at_time",
            "sr_ends_at_time",
            "sr_speed_down_kbps",
            "sr_speed_up_kbps",
        )
        has_manual_values = any((speed_form.get(name) or "").strip() for name in manual_fields)
        try:
            has_manual_values = has_manual_values or bool(speed_form.getlist("sr_days"))
        except AttributeError:
            has_manual_values = has_manual_values or bool((speed_form.get("sr_days") or "").strip())
        has_source = bool((speed_form.get("sr_source_schedule_id") or "").strip())
        speed_form["_speed_rule_action"] = "copy" if has_source and not has_manual_values else "manual"
    handle_embedded_speed_rule(
        tenant_id=tenant_id if tenant_id is not None else _tid(),
        actor=actor or _actor(),
        form=speed_form,
        target_type="card_batch",
        plan_id=batch.plan_id,
        card_batch_id=batch.id,
    )


def _batch_form_data(batch) -> dict:
    return {
        "package_name": batch.package_name,
        "plan_id": batch.plan_id,
        "count": batch.count,
        "manager_id": batch.manager_id,
        "price_per_card": batch.price_per_card,
        "price_bulk": batch.price_bulk,
        "total_price": batch.total_price,
        "total_quota_mb": batch.total_quota_mb,
        "service_name": batch.service_name,
        "username_prefix": batch.username_prefix,
        "username_suffix": batch.username_suffix,
        "username_length": batch.username_length,
        "password_length": batch.password_length,
        "password_charset": batch.password_charset,
        "password_generation_type": batch.password_generation_type,
        "include_batch_number": batch.include_batch_number,
        "random_generation_enabled": batch.random_generation_enabled,
        "starts_with_or_ends_with": batch.starts_with_or_ends_with,
        "prefix_or_suffix_value": batch.prefix_or_suffix_value,
        "time_value": batch.time_value,
        "time_unit": batch.time_unit,
        "device_count": batch.device_count,
        "duration_mode": batch.duration_mode,
        "validity_after_first_login_days": batch.validity_after_first_login_days,
        "count_by_seconds": batch.count_by_seconds,
        "count_from_first_connect": batch.count_from_first_connect,
        "on_quota_exhaust": batch.on_quota_exhaust,
        "auto_renew_after_first_use": batch.auto_renew_after_first_use,
        "transfer_to_student_status_on_connect": batch.transfer_to_student_status_on_connect,
        "close_user_session_on_disconnect": batch.close_user_session_on_disconnect,
        "allow_entry_by_previous_card_palestine": batch.allow_entry_by_previous_card_palestine,
        "switch_to_mac_on_connect": batch.switch_to_mac_on_connect,
        "lock_to_mac_on_close": batch.lock_to_mac_on_close,
        "phone_only_login": batch.phone_only_login,
        "status": batch.status,
        "notes": batch.notes,
    }


def cards_batches():
    svc = get_cards_service()
    filters = _batch_filters_from_request()
    page, per_page = _page_args()
    total = svc.count_batch_operations(**filters)
    pages_count = max(1, (total + per_page - 1) // per_page)
    if page > pages_count:
        page = pages_count
    batches = svc.list_batch_operations(
        **filters,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    totals = svc.batch_operations_totals(**filters)
    plans_list = list(get_plans_service().list(limit=500))
    managers = admins_repo.list_admins()
    distributors = operations_repo.list_distributors(_tid(), limit=500)
    operations_service = get_operations_service()
    print_templates = operations_service.list_print_templates(tenant_id=_tid(), limit=500)
    default_print_template_id = operations_service.get_default_print_template_id(tenant_id=_tid())
    return render_template(
        "radius/cards_batches.html",
        batches=batches,
        plans=plans_list,
        managers=managers,
        distributors=distributors,
        print_templates=print_templates,
        default_print_template_id=default_print_template_id,
        totals=totals,
        filters=filters,
        page=page,
        per_page=per_page,
        total=total,
        pages_count=pages_count,
        status_options=[
            ("", "النشطة فقط"),
            ("all", "كل الحزم"),
            ("available", "بها بطاقات جاهزة"),
            ("used", "بها استخدام"),
            ("expired", "بها بطاقات منتهية"),
            ("revoked", "بها بطاقات ملغاة"),
            ("exhausted", "مستهلكة"),
            ("deleted", "مؤرشفة"),
        ],
    )


def cards_batches_import():
    if request.method == "GET":
        # No-store: keeps the CSRF token rendered in the page in lock
        # step with the server-side session. A stale cached copy would
        # send an old token and trip the CSRF guard on the smart-import
        # POST.
        from flask import make_response
        ctx = _import_form_context()
        # Expose the engine version + build note + module mtime so the
        # operator can visually confirm the running container has the
        # latest code (no more "did the deploy actually take?").
        import os
        try:
            engine_mtime = os.path.getmtime(cards_import_engine.__file__)
        except OSError:
            engine_mtime = 0
        ctx.update({
            "engine_version":    cards_import_engine.ENGINE_VERSION,
            "engine_build_note": cards_import_engine.ENGINE_BUILD_NOTE,
            "engine_mtime":      engine_mtime,
        })
        html = render_template("radius/cards_import.html", **ctx)
        resp = make_response(html)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    plan_id = _form_int("plan_id")
    source_type = (_form_str("source_type") or "external").lower()
    csv_text = request.form.get("csv_text") or ""
    rows = _parse_import_cards_text(csv_text)

    if source_type not in {"external", "imported"}:
        flash("نوع الملف يجب أن يكون خارجي أو مستورد.", "error")
        return render_template("radius/cards_import.html", **_import_form_context()), 422
    if not plan_id:
        flash("اختر الباقة المرتبطة قبل الاستيراد.", "error")
        return render_template("radius/cards_import.html", **_import_form_context()), 422
    if not rows:
        flash("أدخل كروت للاستيراد بصيغة username,password أو عمود username فقط.", "error")
        return render_template("radius/cards_import.html", **_import_form_context()), 422
    if len(rows) > 5000:
        flash("الحد الأقصى للاستيراد هو 5000 بطاقة في العملية الواحدة.", "error")
        return render_template("radius/cards_import.html", **_import_form_context()), 422

    try:
        result = get_cards_service().import_batch(
            actor=_actor(),
            plan_id=plan_id,
            cards=rows,
            source_type=source_type,
            package_name=_form_str("package_name"),
            service_name=_form_str("service_name"),
            notes=_form_str("notes"),
            price_per_card=_form_float("price_per_card"),
            total_price=_form_float("total_price"),
            sync_to_radius=_form_bool("sync_to_radius") and source_type != "external",
        )
    except RadiusValidationError as exc:
        flash(exc.message, "error")
        return render_template("radius/cards_import.html", **_import_form_context()), 422
    except RadiusError as exc:
        flash(exc.message, "error")
        return render_template("radius/cards_import.html", **_import_form_context()), 500

    batch = result["batch"]
    skipped = result["skipped_count"]
    synced = result["radius_synced_count"]
    sync_label = f" وتمت مزامنة {synced} حساب RADIUS." if result["radius_sync_enabled"] else ""
    skipped_label = f" تم تخطي {skipped} مكرر/غير صالح." if skipped else ""
    flash(
        f"تم استيراد {result['inserted_count']} بطاقة داخل الحزمة {batch.batch_code}.{skipped_label}{sync_label}",
        "success",
    )
    return redirect(url_for("radius.cards_batches", q=batch.batch_code, status="all"))


# Cap the upload payload at 12 MB. Anything beyond this is almost
# certainly the wrong file — cards files in any reasonable format
# (CSV/XLSX/PDF) for ≤ 5000 cards land well under 2 MB.
_IMPORT_MAX_BYTES = 12 * 1024 * 1024


def cards_batches_import_preview():
    """Receive an uploaded file, run the intelligent parser, and return
    the extracted (username, password) pairs as JSON.

    Front-end consumes the response to populate the existing textarea —
    so the final commit still goes through ``cards_batches_import``
    unchanged.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "اختر ملفًا قبل الرفع."}), 400

    raw = upload.read(_IMPORT_MAX_BYTES + 1)
    if not raw:
        return jsonify({"ok": False, "error": "الملف فارغ أو غير قابل للقراءة."}), 400
    if len(raw) > _IMPORT_MAX_BYTES:
        return jsonify({
            "ok": False,
            "error": "حجم الملف يتجاوز 12MB — قسّمه إلى دفعات أصغر.",
        }), 413

    result = cards_import_engine.parse(raw, upload.filename or "")
    payload = {
        "ok": bool(result.cards) or not result.warnings,
        "engine_version": cards_import_engine.ENGINE_VERSION,
        "engine_build_note": cards_import_engine.ENGINE_BUILD_NOTE,
        "fmt": result.fmt,
        "count": len(result.cards),
        "strategy": result.detected.strategy,
        "username_index": result.detected.username_index,
        "password_index": result.detected.password_index,
        "header_row_present": result.detected.header_row_present,
        "rows_seen": result.rows_seen,
        "rows_skipped": result.rows_skipped,
        "sheet_names": result.sheet_names,
        "warnings": result.warnings,
        "csv_text": cards_import_engine.cards_to_csv(result.cards),
        "preview": [
            {"username": c.username, "password": c.password}
            for c in result.cards[:5]
        ],
    }
    status = 200 if result.cards else 422
    return jsonify(payload), status


def _selected_batch_ids() -> list[int]:
    ids: list[int] = []
    for raw in request.form.getlist("batch_ids"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    return sorted(set(ids))


def cards_batches_bulk():
    svc = get_cards_service()
    action = _form_str("bulk_action")
    batch_ids = _selected_batch_ids()
    return_to = request.form.get("return_to") or url_for("radius.cards_batches")
    if not batch_ids:
        flash("اختر حزمة واحدة على الأقل لتنفيذ الإجراء.", "error")
        return redirect(return_to)

    changed = 0
    try:
        if action == "archive":
            reason = _form_str("reason") or "أرشفة من مركز عمليات حزم البطاقات"
            for batch_id in batch_ids:
                if svc.archive_batch(actor=_actor(), batch_id=batch_id, reason=reason):
                    changed += 1
            flash(f"تمت أرشفة {changed} حزمة بدون حذف البطاقات.", "warning")
        elif action == "restore":
            for batch_id in batch_ids:
                if svc.restore_batch(actor=_actor(), batch_id=batch_id):
                    changed += 1
            flash(f"تمت استعادة {changed} حزمة مؤرشفة.", "success")
        elif action == "refresh":
            flash("تم تحديث إحصاءات الحزم من البيانات الحالية.", "success")
        else:
            flash("إجراء جماعي غير معروف.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(return_to)


def _csv_text(value) -> str:
    if value is None:
        return ""
    return str(value)


def cards_batches_export_csv():
    svc = get_cards_service()
    filters = _batch_filters_from_request()
    rows = svc.list_batch_operations(**filters, limit=5000, offset=0)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "رقم الحزمة",
        "اسم الحزمة",
        "الباقة",
        "الحالة",
        "العدد",
        "المولد",
        "الجاهز",
        "النشط",
        "المنتهي",
        "الملغى",
        "المتبقي",
        "جلسات",
        "MAC مختلف",
        "قواعد سرعة",
        "سعر البطاقة",
        "قيمة الحزمة",
        "المدير",
        "الموزع",
        "تاريخ الإنشاء",
    ])
    for item in rows:
        unit_price = float(item.get("estimated_unit_price") or 0)
        configured_value = float(item.get("total_price") or 0)
        if configured_value <= 0:
            configured_value = unit_price * int(item.get("generated") or 0)
        writer.writerow([
            _csv_text(item.get("batch_code")),
            _csv_text(item.get("package_name")),
            _csv_text(item.get("plan_name")),
            _csv_text(item.get("operational_status")),
            _csv_text(item.get("count")),
            _csv_text(item.get("generated")),
            _csv_text(item.get("available_count")),
            _csv_text(item.get("active_count")),
            _csv_text(item.get("expired_count")),
            _csv_text(item.get("revoked_count")),
            _csv_text(item.get("remaining_count")),
            _csv_text(item.get("sessions_count")),
            _csv_text(item.get("unique_macs")),
            _csv_text(item.get("active_speed_rules")),
            f"{unit_price:.2f}",
            f"{configured_value:.2f}",
            _csv_text(item.get("created_by") or item.get("manager_id")),
            _csv_text(item.get("distributor_display_name") or item.get("distributor_name")),
            _csv_text(item.get("created_at")),
        ])
    payload = "\ufeff" + out.getvalue()
    return Response(
        payload,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=card-batches.csv"},
    )


def _batch_export_rows() -> list[dict]:
    svc = get_cards_service()
    filters = _batch_filters_from_request()
    return svc.list_batch_operations(**filters, limit=5000, offset=0)


def _batch_export_value(item: dict, key: str):
    if key == "estimated_value":
        unit_price = float(item.get("estimated_unit_price") or 0)
        configured_value = float(item.get("total_price") or 0)
        if configured_value <= 0:
            configured_value = unit_price * int(item.get("generated") or 0)
        return f"{configured_value:.2f}"
    if key == "estimated_unit_price":
        return f"{float(item.get('estimated_unit_price') or 0):.2f}"
    if key == "created_by":
        return item.get("created_by") or item.get("manager_id")
    if key == "distributor":
        return item.get("distributor_display_name") or item.get("distributor_name")
    return item.get(key)


_BATCH_EXPORT_COLUMNS = [
    ("batch_code", "batch_code"),
    ("package_name", "package_name"),
    ("plan_name", "plan_name"),
    ("operational_status", "operational_status"),
    ("source_type", "source_type"),
    ("original_count", "original_count"),
    ("count", "count"),
    ("generated", "generated"),
    ("available_count", "available_count"),
    ("active_count", "active_count"),
    ("expired_count", "expired_count"),
    ("archived_count", "archived_count"),
    ("pending_archive_count", "pending_archive_count"),
    ("revoked_count", "revoked_count"),
    ("remaining_count", "remaining_count"),
    ("operational_remaining_count", "operational_remaining_count"),
    ("sessions_count", "sessions_count"),
    ("unique_macs", "unique_macs"),
    ("active_speed_rules", "active_speed_rules"),
    ("estimated_unit_price", "estimated_unit_price"),
    ("estimated_value", "estimated_value"),
    ("created_by", "created_by"),
    ("distributor", "distributor"),
    ("created_at", "created_at"),
]


def _batch_export_table(rows: list[dict]) -> list[list[str]]:
    table = [[label for _, label in _BATCH_EXPORT_COLUMNS]]
    for item in rows:
        table.append([
            _csv_text(_batch_export_value(item, key))
            for key, _label in _BATCH_EXPORT_COLUMNS
        ])
    return table


def cards_batches_export_xlsx():
    from copy import copy

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Card Batches"
    for row in _batch_export_table(_batch_export_rows()):
        sheet.append(row)
    for cell in sheet[1]:
        font = copy(cell.font)
        font.bold = True
        cell.font = font
    out = io.BytesIO()
    workbook.save(out)
    return Response(
        out.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=card-batches.xlsx"},
    )


def cards_batches_export_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    rows = _batch_export_table(_batch_export_rows())
    pdf_columns = [0, 1, 2, 3, 5, 8, 10, 11, 19, 20, 23]
    pdf_rows = [[row[i] for i in pdf_columns] for row in rows[:101]]
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=landscape(A4),
        leftMargin=18,
        rightMargin=18,
        topMargin=18,
        bottomMargin=18,
    )
    table = Table(pdf_rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123056")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fb")]),
    ]))
    doc.build([table, Spacer(1, 6)])
    return Response(
        out.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=card-batches.pdf"},
    )


def _checker_redirect(query: str):
    return redirect(url_for("radius.cards_checker", query=(query or "").strip()))


def _handle_card_operation():
    svc = get_cards_service()
    # Accept both 'op' (new template forms) and '_card_action' (older
    # forms / curl callers). 'op' wins when both are set.
    action = _form_str("op") or _form_str("_card_action")
    card_id = _form_int("card_id")
    username = _form_str("username")
    query = _form_str("query") or username
    try:
        if action == "lock_mac":
            res = svc.lock_card_mac(
                actor=_actor(), card_id=card_id, mac=_form_str("mac"),
            ) or {}
            macs_locked = res.get("macs") or []
            kicked = len(res.get("kicked") or [])
            kept = res.get("kept") or 0
            if kicked > 0:
                flash(
                    f"تم تثبيت عنوان MAC للبطاقة ({len(macs_locked)} عنوان)، وقطع {kicked} جلسة "
                    f"لأجهزة غير مطابقة. "
                    + (f"{kept} جلسة مطابقة بقيت متّصلة." if kept else ""),
                    "success",
                )
            else:
                flash(
                    f"تم تثبيت عنوان MAC على البطاقة ({len(macs_locked)} عنوان)." +
                    (f" ({kept} جلسة نشطة كانت مطابقة بالفعل.)" if kept else ""),
                    "success",
                )
        elif action == "unlock_mac":
            svc.unlock_card_mac(actor=_actor(), card_id=card_id)
            flash("تم إلغاء تثبيت MAC عن البطاقة.", "success")
        elif action == "disconnect":
            # session_ids = comma-separated acctsessionids (from the
            # per-device picker). Empty / missing → kick all.
            ids_raw = _form_str("session_ids")
            ids = [s.strip() for s in ids_raw.split(",") if s.strip()] if ids_raw else None
            svc.disconnect_card(
                actor=_actor(),
                username=username,
                session_id=_form_str("session_id"),
                session_ids=ids,
            )
            if ids:
                flash(
                    f"تم إرسال أمر قطع لـ {len(ids)} جلسة"
                    + (" مختارة." if len(ids) > 1 else "."),
                    "warning",
                )
            else:
                flash("تم إرسال أمر قطع لكل الجلسات النشطة.", "warning")
        elif action == "reset_usage":
            svc.reset_card_usage(actor=_actor(), card_id=card_id)
            flash("تم تصفير استخدام البطاقة ووقت بدايتها.", "success")
        elif action == "disable":
            res = svc.disable_card(
                actor=_actor(), card_id=card_id, reason=_form_str("reason"),
            )
            frozen = int((res or {}).get("frozen_remaining_seconds") or 0)
            # disable_card now also broadcasts CoA-Disconnect so devices
            # currently online can't keep using the network after the
            # admin froze the card. Reflect both actions in the flash.
            suffix = " وتم قطع كل الجلسات النشطة."
            if frozen > 0:
                h, m = divmod(frozen // 60, 60)
                flash(
                    f"تم تعطيل البطاقة وتجميد الوقت المتبقي ({h} ساعة و {m} دقيقة). "
                    "سيعود نفس الوقت عند إعادة التفعيل." + suffix,
                    "warning",
                )
            else:
                flash("تم تعطيل البطاقة." + suffix, "warning")
        elif action == "enable":
            res = svc.enable_card(actor=_actor(), card_id=card_id)
            restored = int((res or {}).get("restored_seconds") or 0)
            if restored > 0:
                h, m = divmod(restored // 60, 60)
                flash(
                    f"تم تفعيل البطاقة. تمت استعادة الوقت المجمَّد ({h} ساعة و {m} دقيقة).",
                    "success",
                )
            else:
                flash("تم تفعيل البطاقة.", "success")
        elif action == "soft_delete":
            # Default 'حذف' from the Card Checker — moves to recycle bin,
            # NOT permanent. The /admin/radius/recycle-bin screen can
            # restore or finally purge it.
            svc.soft_delete_card(
                actor=_actor(), card_id=card_id, reason=_form_str("reason"),
            )
            flash("تم نقل البطاقة إلى سلة المحذوفات. يمكنك استعادتها من سلة المحذوفات.", "success")
            query = ""
        elif action == "delete_permanent":
            # Hard-delete path retained for the recycle bin screen.
            confirm_delete = _form_str("confirm_delete")
            if confirm_delete != "حذف البطاقة" and confirm_delete.upper() != "DELETE":
                flash("للحذف النهائي اكتب عبارة التأكيد في خانة التأكيد.", "error")
            else:
                svc.delete_card_permanently(actor=_actor(), card_id=card_id)
                flash("تم حذف البطاقة نهائيًا. لا يظهر هذا الخيار في التشغيل اليومي إلا بحذر.", "warning")
                query = ""
        elif action == "set_time":
            # Per-card time adjustment (shift expire_at by ±N seconds).
            # Form fields:
            #   time_amount  → integer > 0
            #   time_unit    → "minutes" | "hours" | "days"
            #   time_op      → "add" | "subtract"
            unit_map = {"minutes": 60, "hours": 3600, "days": 86400}
            amount = _form_int("time_amount")
            unit   = (_form_str("time_unit") or "").strip().lower()
            op     = (_form_str("time_op")   or "").strip().lower()
            if amount <= 0 or unit not in unit_map or op not in ("add", "subtract"):
                flash("بيانات التعديل غير مكتملة. حدّد المدّة والوحدة والعملية.", "error")
            else:
                delta = amount * unit_map[unit] * (-1 if op == "subtract" else 1)
                try:
                    result = svc.adjust_card_time(
                        actor=_actor(), card_id=card_id,
                        delta_seconds=delta, username=username,
                    )
                except RadiusError as e:
                    flash(e.message, "error")
                else:
                    # Build a friendly Arabic summary
                    op_label   = "تمت إضافة" if op == "add" else "تم خصم"
                    unit_label = {"minutes": "دقيقة", "hours": "ساعة", "days": "يوم"}[unit]
                    rem_h, rem_m = divmod(int(result["remaining_seconds"]) // 60, 60)
                    coa = result.get("coa_result")
                    coa_note = ""
                    if coa is not None:
                        if getattr(coa, "ok", False):
                            coa_note = " — وصل التحديث للـ MikroTik (CoA-ACK)."
                        elif getattr(coa, "code_name", "") == "no_active_session":
                            coa_note = " — لا جلسة نشطة الآن، سيُطبَّق في الجلسة التالية."
                        else:
                            coa_note = f" — لم يصل التحديث الفوري للـ MikroTik ({getattr(coa,'code_name','?')})."
                    flash(
                        f"{op_label} {amount} {unit_label} من وقت البطاقة. "
                        f"المتبقي الآن: {rem_h} ساعة و {rem_m} دقيقة.{coa_note}",
                        "success",
                    )
        elif action == "set_speed":
            # Per-card speed override (migration 024). Persists to
            # cards.card_speed_*_kbps, re-syncs the FreeRADIUS radreply
            # row via freeradius_translator, and best-effort pushes a
            # CoA-Request with the new Mikrotik-Rate-Limit so any live
            # session picks the new rate without disconnect.
            #
            # Pass down=0 AND up=0 to CLEAR the override (revert to plan
            # default). The UI doesn't expose clearing yet but the
            # service supports it for API/CLI callers.
            down = _form_int("speed_down_kbps")
            up   = _form_int("speed_up_kbps")
            if down < 0 or up < 0:
                flash("قيم السرعة يجب ألا تكون سالبة.", "error")
            else:
                try:
                    result = svc.set_card_speed(
                        actor=_actor(), card_id=card_id,
                        down_kbps=down, up_kbps=up, username=username,
                    )
                except RadiusError as e:
                    flash(e.message, "error")
                else:
                    coa = result.get("coa_result")
                    coa_note = ""
                    if coa is not None:
                        if getattr(coa, "ok", False):
                            coa_note = " — وصل التحديث للـ MikroTik (CoA-ACK)."
                        elif getattr(coa, "code_name", "") == "no_active_session":
                            coa_note = " — لا جلسة نشطة، سيُطبَّق في الجلسة التالية."
                        else:
                            coa_note = f" — لم يصل التحديث الفوري للـ MikroTik ({getattr(coa,'code_name','?')})."
                    if down == 0 and up == 0:
                        flash(
                            f"تم إلغاء تخصيص السرعة على البطاقة — ترجع لسرعة الحزمة.{coa_note}",
                            "success",
                        )
                    else:
                        flash(
                            f"تم تعيين سرعة البطاقة: تنزيل {down} kbps / رفع {up} kbps.{coa_note}",
                            "success",
                        )
        elif action == "sync_dhcp":
            # On-demand DHCP-lease pull from the tenant's MikroTik
            # routers. Useful when the operator opens the Checker for
            # a brand-new card and doesn't want to wait the 2-minute
            # worker tick. Best-effort — failures are logged, never
            # raised.
            try:
                from ..services import device_fingerprint_sync
                seen = device_fingerprint_sync.sync_tenant(_tid())
                flash(f"تم تحديث بيانات DHCP من المايكروتيك — {seen} عنوان MAC.",
                      "success")
            except Exception as e:  # noqa: BLE001
                flash(f"تعذّر التحديث الفوري للـ DHCP: {e}", "error")
        else:
            flash("إجراء غير معروف.", "error")
    except RadiusError as e:
        flash(e.message, "error")
    return _checker_redirect(query)


def cards_checker():
    """R13.A.4: GET now renders the v2 operations-room template by default.

    The /v2 preview URL stays as an alias (same template), so any
    bookmarked /v2 link keeps working. POST still routes through the
    existing _handle_card_operation() handler — all card operations are
    unchanged.
    """
    if request.method == "POST":
        return _handle_card_operation()

    query = (request.args.get("query") or request.args.get("q") or "").strip()
    result = None
    error = ""
    if query:
        if len(query) > 128:
            error = "أدخل رقم بطاقة أو اسم دخول لا يتجاوز 128 حرفًا."
        else:
            try:
                result = check_card(_tid(), query)
            except Exception as exc:  # noqa: BLE001
                # Never let an internal exception bubble as a bare HTTP 500
                # — log the full traceback so we can find the cause, and
                # show the operator a friendly message so they don't think
                # the whole system is dead.
                import logging
                logging.getLogger(__name__).exception(
                    "cards_checker: check_card raised for query=%r tenant=%s",
                    query, _tid(),
                )
                error = (
                    "حدث خطأ داخلي أثناء فحص البطاقة. تم تسجيل تفاصيل "
                    "الخطأ في سجل الخادم — راجع `docker compose logs "
                    f"hoberadius` للتفاصيل. ({type(exc).__name__})"
                )
                result = None
    return render_template(
        "radius/cards_checker_v2.html",
        query=query,
        result=result,
        error=error,
    )


# ─────────────────────────────────────────────────────────────────────────────
# R13.A.1 — Card Checker JSON API
# ─────────────────────────────────────────────────────────────────────────────
#
# الـ AJAX foundation للـ UI rebuild (R13.A). الـ HTML page الحالي يَستخدم
# render_template مع full reload. الـ rebuild سيَستخدم هذا الـ endpoint
# للـ live lookup عبر fetch().
#
# لا يَكسر القديم — endpoint منفصل تمامًا. يُعيد نفس البيانات التي يَبنيها
# `check_card` كـ JSON. الـ schema:
#
#   200 OK   { "ok": true,  "query": "...", "result": { ... full payload ... } }
#   400 BAD  { "ok": false, "error": "human-readable error", "code": "..." }
#
# الـ codes الموحَّدة:
#   empty_query   — q فاضي
#   query_too_long — q > 128 حرف
#
# نُعيد دائمًا `ok` boolean و `query` echoes حتى الـ frontend يُسهّل الـ
# state matching. لا نَستخدم HTTP 404 لـ "card not found" — هذا حالة
# طبيعية (`result.exists = false`)، لا خطأ.
# ─────────────────────────────────────────────────────────────────────────────
def cards_checker_v2():
    """Render the same checker page for old /cards/checker/v2 bookmarks."""
    return cards_checker()


def cards_checker_api_lookup():
    """GET /admin/radius/cards/checker/api/lookup?q=<query>

    يُرجع JSON مكافئ لـ check_card() — جاهز للـ AJAX frontend.
    """
    query = (request.args.get("q") or request.args.get("query") or "").strip()
    if not query:
        return jsonify({
            "ok": False,
            "error": "أدخل رقم بطاقة أو اسم دخول.",
            "code": "empty_query",
        }), 400
    if len(query) > 128:
        return jsonify({
            "ok": False,
            "error": "أدخل رقم بطاقة أو اسم دخول لا يتجاوز 128 حرفًا.",
            "code": "query_too_long",
        }), 400
    try:
        result = check_card(_tid(), query)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            "cards_checker_api_lookup: check_card raised for query=%r tenant=%s",
            query, _tid(),
        )
        return jsonify({
            "ok": False,
            "error": f"خطأ داخلي أثناء فحص البطاقة ({type(exc).__name__}). "
                     "راجع docker logs hoberadius.",
            "code": "internal_error",
        }), 500
    return jsonify({
        "ok": True,
        "query": query,
        "result": result,
    })


def cards_checker_api_reveal_password():
    """POST /admin/radius/cards/checker/api/reveal-password

    On-demand password reveal for the Card Checker hero.

    Why a SEPARATE endpoint instead of including the password in the
    default Checker payload:
      • The default payload is rendered into the page HTML on every
        Checker request and may be cached, logged in browser dev tools,
        sniffed via the SSE / network tab, or copied accidentally.
      • By keeping the password OUT of the default response and
        requiring an explicit POST to retrieve it, we get:
          – Per-reveal audit row (action: 'card.password_reveal')
            with actor + card_id + tenant_id + timestamp.
          – Role gate: only admins reaching this route can call it
            (the blueprint already enforces login_required on the
            whole radius module).
          – Less leakage: a casual screenshot of the Checker won't
            include the value.

    Body: form field `card_id` (int)
    Returns: 200 {ok:true, password:'...'} or 4xx {ok:false, error:...}
    """
    from ..db.connection import db as _db_conn
    from ..services.audit import get_audit_service
    card_id = _form_int("card_id")
    if not card_id:
        return jsonify({"ok": False, "error": "card_id مطلوب"}), 400
    tenant_id = _tid()
    row = _db_conn().execute(
        "SELECT username, password FROM cards "
        "WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (tenant_id, card_id),
    ).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "البطاقة غير موجودة"}), 404
    username = row["username"] if isinstance(row, dict) else row[0]
    password = row["password"]  if isinstance(row, dict) else row[1]
    if not password:
        return jsonify({"ok": False, "error": "هذه البطاقة بدون كلمة مرور"}), 404
    # Audit the reveal — operator + card + when.
    try:
        get_audit_service().record(
            actor=_actor(), action="card.password_reveal",
            target_type="card", target_id=str(card_id),
            payload={"username": username},
        )
    except Exception:  # noqa: BLE001 — never block the reveal on audit issues
        pass
    return jsonify({
        "ok": True,
        "card_id": card_id,
        "password": password,
    })


def cards_generate():
    if request.method == "POST":
        try:
            plan_id = _form_int("plan_id")
            count = _form_int("count")
            opts = _collect_batch_options()
            batch, cards = get_cards_service().generate_batch(
                actor=_actor(), plan_id=plan_id, count=count, **opts,
            )
            flash(f"تم إنشاء دفعة «{batch.batch_code}» — {len(cards)} بطاقة.", "success")
            _apply_pending_batch_speed_rule(batch, request.form)
            return redirect(url_for("radius.cards_of_batch", batch_id=batch.id))
        except (TypeError, ValueError) as e:
            flash(f"قيم غير صحيحة: {e}", "error")
        except RadiusError as e:
            flash(e.message, "error")
    plans = list(get_plans_service().list(limit=500))
    managers = admins_repo.list_admins()
    distributors = operations_repo.list_distributors(_tid(), limit=500)
    return render_template(
        "radius/cards_generate.html",
        plans=plans,
        managers=managers,
        distributors=distributors,
        form=request.form,
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="card_batch",
            return_to=request.path,
            title="قواعد سرعة مجدولة للبطاقات",
            help_text="أضف قاعدة سرعة مبدئية تنحفظ على الحزمة فور إنشائها وتطبّق على بطاقاتها.",
        ),
    )


def cards_generate_progress_start():
    _cleanup_generate_jobs()
    try:
        plan_id = _form_int("plan_id")
        count = _form_int("count")
        opts = _collect_batch_options()
        speed_form = request.form.copy()
    except RadiusError as e:
        return jsonify({"ok": False, "error": e.message}), 422
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "error": f"قيم غير صحيحة: {e}"}), 422

    app = current_app._get_current_object()
    actor = _actor()
    tenant_id = _tid()
    redirect_template = url_for("radius.cards_of_batch", batch_id=0)
    job_id = uuid.uuid4().hex
    _set_generate_job(
        job_id,
        ok=True,
        status="queued",
        phase="queued",
        current=0,
        total=count,
        message="تم استلام طلب إنشاء الحزمة",
        created_at=time.time(),
    )

    def run_job() -> None:
        with app.app_context():
            g.tenant_id = tenant_id

            def progress(update: dict) -> None:
                _set_generate_job(
                    job_id,
                    status="running",
                    phase=update.get("phase") or "running",
                    current=int(update.get("current") or 0),
                    total=int(update.get("total") or count),
                    message=update.get("message") or "",
                )

            try:
                batch, cards = get_cards_service().generate_batch(
                    actor=actor,
                    plan_id=plan_id,
                    count=count,
                    progress_callback=progress,
                    **opts,
                )
                _apply_pending_batch_speed_rule(batch, speed_form, tenant_id=tenant_id, actor=actor)
                _set_generate_job(
                    job_id,
                    status="done",
                    phase="done",
                    current=len(cards),
                    total=len(cards),
                    generated=len(cards),
                    batch_id=batch.id,
                    batch_code=batch.batch_code,
                    message=f"تم إنشاء {len(cards)} بطاقة بدون تكرار.",
                    redirect_url=redirect_template.replace("/0/", f"/{batch.id}/"),
                )
            except RadiusError as e:
                _set_generate_job(job_id, ok=False, status="error", phase="error", message=e.message)
            except Exception as e:
                _set_generate_job(job_id, ok=False, status="error", phase="error", message=str(e))

    threading.Thread(target=run_job, name=f"cards-generate-{job_id[:8]}", daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


def cards_generate_progress_status(job_id: str):
    job = _get_generate_job(job_id)
    if not job:
        return jsonify({"ok": False, "status": "missing", "message": "طلب التوليد غير موجود أو انتهت صلاحيته."}), 404
    return jsonify(job)


def cards_batch_edit(batch_id: int):
    svc = get_cards_service()
    batch = next((b for b in svc.list_batches(limit=1000) if b.id == batch_id), None)
    if not batch:
        flash("دفعة الكروت غير موجودة.", "error")
        return redirect(url_for("radius.cards_batches"))
    if request.method == "POST":
        if request.form.get("_speed_rule_action"):
            try:
                handle_embedded_speed_rule(
                    tenant_id=_tid(),
                    actor=_actor(),
                    form=request.form,
                    target_type="card_batch",
                    plan_id=batch.plan_id,
                    card_batch_id=batch_id,
                )
                flash("تم تنفيذ إجراء قواعد السرعة لهذه الحزمة.", "success")
            except RadiusError as e:
                flash(e.message, "error")
            return redirect(url_for("radius.cards_batch_edit", batch_id=batch_id))
        try:
            data = _collect_batch_options()
            data.update({
                "plan_id": _form_int("plan_id"),
                "count": _form_int("count", batch.count),
                "status": _form_str("status") or batch.status,
            })
            updated = svc.update_batch(actor=_actor(), batch_id=batch_id, data=data)
            flash("تم حفظ تعديلات دفعة الكروت.", "success")
            return redirect(url_for("radius.cards_of_batch", batch_id=updated.id))
        except (TypeError, ValueError) as e:
            flash(f"قيم غير صحيحة: {e}", "error")
        except RadiusError as e:
            flash(e.message, "error")
    plans = list(get_plans_service().list(limit=500))
    form = request.form if request.method == "POST" else _batch_form_data(batch)
    return render_template(
        "radius/cards_batch_edit.html",
        batch=batch,
        plans=plans,
        form=form,
        speed_rules_panel=speed_rules_panel(
            tenant_id=_tid(),
            target_type="card_batch",
            plan_id=batch.plan_id,
            card_batch_id=batch_id,
            return_to=request.path,
            title="قواعد سرعة هذه الحزمة",
            help_text="أضف قواعد سرعة لكل بطاقات هذه الحزمة. عند وجود سرعة للعرض وسرعة لهذه الحزمة، تُطبّق سرعة الحزمة أولًا.",
        ),
    )


def cards_list():
    """R10.4: pagination + search + batch + revoked filters.

    Pre-R10.4 رفعنا 1000 كرت دفعة واحدة. مع 2020+ كرت، الصفحة كانت
    بطيئة وصعبة التصفّح. الآن:
      - `?q=...`    LIKE على username (يدعم البحث الجزئي بالأرقام).
      - `?batch_id=X` فلترة على دفعة محدّدة.
      - `?used=0|1` ، `?revoked=0|1` — booleans منفصلة.
      - `?page=N`   صفحة (1-based). `?per_page` يقبل 25/50/100 (سقف 100).

    نُمرّر `total / page / per_page / pages_count / q / batch_id / revoked
    / used / preserve_params` للقالب حتى يبني روابط pagination بدون
    إعادة بناء query string.
    """
    used = request.args.get("used")
    used_b = True if used == "1" else (False if used == "0" else None)

    revoked = request.args.get("revoked")
    revoked_b = True if revoked == "1" else (False if revoked == "0" else None)

    raw_batch = (request.args.get("batch_id") or "").strip()
    try:
        batch_id = int(raw_batch) if raw_batch else None
    except ValueError:
        batch_id = None

    q = (request.args.get("q") or "").strip()

    # per_page: clamp إلى whitelist {25, 50, 100} لمنع abuse + استقرار CSS.
    try:
        per_page = int(request.args.get("per_page") or "50")
    except ValueError:
        per_page = 50
    if per_page not in (25, 50, 100):
        per_page = 50

    try:
        page = max(1, int(request.args.get("page") or "1"))
    except ValueError:
        page = 1

    svc = get_cards_service()
    total = svc.count_cards(used=used_b, revoked=revoked_b,
                             batch_id=batch_id, search=q or None)
    pages_count = max(1, (total + per_page - 1) // per_page)
    if page > pages_count:
        page = pages_count
    offset = (page - 1) * per_page

    items = svc.list_cards(used=used_b, revoked=revoked_b,
                           batch_id=batch_id, search=q or None,
                           limit=per_page, offset=offset)
    plans = {p.id: p for p in get_plans_service().list(limit=500)}
    batches = {b.id: b for b in svc.list_batches(limit=500)}

    # preserve_params: نمرّرها للقالب فيستخدمها في hidden inputs + روابط
    # pagination — يحافظ على البحث/الفلاتر عبر تغيير الصفحة.
    preserve = {}
    if used is not None: preserve["used"] = used
    if revoked is not None: preserve["revoked"] = revoked
    if batch_id is not None: preserve["batch_id"] = batch_id
    if q: preserve["q"] = q
    preserve["per_page"] = per_page

    return render_template(
        "radius/cards_list.html",
        items=items, plans=plans, batches=batches,
        used=used, revoked=revoked, batch_id=batch_id, q=q,
        page=page, per_page=per_page, total=total,
        pages_count=pages_count, preserve=preserve,
    )


def _parse_card_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for candidate in (text, text.replace("T", " ")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
    return None


def _format_card_dt(value) -> str:
    dt = _parse_card_dt(value)
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def _format_card_seconds(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    if total <= 0:
        return "—"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} يوم {hours} ساعة"
    if hours:
        return f"{hours} ساعة {minutes} دقيقة"
    return f"{minutes} دقيقة"


def _format_card_bytes(value: int | float | None) -> str:
    size = float(value or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


def _card_status_meta(row: dict, now: datetime) -> dict:
    online = int(row.get("online_sessions") or 0) > 0
    expire_at = _parse_card_dt(row.get("expire_at"))
    expired = bool(expire_at and expire_at < now)
    if row.get("deleted_at"):
        return {"key": "expired", "label": "منتهي", "tone": "rose", "rank": 3}
    if row.get("revoked"):
        return {"key": "expired", "label": "منتهي", "tone": "rose", "rank": 3}
    if expired:
        return {"key": "expired", "label": "منتهي", "tone": "rose", "rank": 3}
    if online:
        return {"key": "online", "label": "متصل", "tone": "green", "rank": 0}
    if row.get("used"):
        return {"key": "used_offline", "label": "مستخدم", "tone": "blue", "rank": 2}
    return {"key": "ready", "label": "متاح", "tone": "violet", "rank": 1}


def _card_remaining_meta(row: dict, now: datetime) -> dict:
    expire_at = _parse_card_dt(row.get("expire_at"))
    if row.get("revoked") and int(row.get("frozen_remaining_seconds") or 0) > 0:
        seconds = int(row.get("frozen_remaining_seconds") or 0)
        return {
            "label": f"مجمد: {_format_card_seconds(seconds)}",
            "seconds": seconds,
            "state": "frozen",
        }
    if not expire_at:
        return {"label": "لم تبدأ", "seconds": 0, "state": "pending"}
    seconds = int((expire_at - now).total_seconds())
    if seconds <= 0:
        return {"label": "منتهي", "seconds": 0, "state": "expired"}
    return {"label": _format_card_seconds(seconds), "seconds": seconds, "state": "active"}


def _batch_cards_details(tenant_id: int, batch_id: int) -> list[dict]:
    rows = db().execute(
        """
        WITH acct AS (
          SELECT tenant_id,
                 username,
                 COUNT(*) AS sessions_count,
                 COUNT(DISTINCT NULLIF(callingstationid, '')) AS unique_macs,
                 SUM(CASE WHEN acctstoptime IS NULL THEN 1 ELSE 0 END) AS online_sessions,
                 COALESCE(SUM(acctsessiontime), 0) AS total_session_seconds,
                 COALESCE(SUM(acctinputoctets), 0) AS total_upload_bytes,
                 COALESCE(SUM(acctoutputoctets), 0) AS total_download_bytes,
                 MIN(acctstarttime) AS first_session_at,
                 MAX(acctstarttime) AS last_connect_at,
                 MAX(acctstoptime) AS last_disconnect_at,
                 MAX(COALESCE(acctupdatetime, acctstoptime, acctstarttime)) AS last_seen_at
            FROM radacct
           WHERE tenant_id = ?
           GROUP BY tenant_id, username
        ),
        latest AS (
          SELECT *
            FROM (
              SELECT tenant_id,
                     username,
                     acctsessionid,
                     acctstarttime,
                     acctupdatetime,
                     acctstoptime,
                     acctsessiontime,
                     acctinputoctets,
                     acctoutputoctets,
                     nasipaddress,
                     callingstationid,
                     framedipaddress,
                     ROW_NUMBER() OVER (
                       PARTITION BY tenant_id, username
                       ORDER BY COALESCE(acctupdatetime, acctstoptime, acctstarttime, '') DESC,
                                radacctid DESC
                     ) AS rn
                FROM radacct
               WHERE tenant_id = ?
            )
           WHERE rn = 1
        )
        SELECT c.id,
               c.batch_id,
               c.username,
               c.password,
               c.plan_id,
               c.used,
               c.first_used_at,
               c.used_by_mac,
               c.used_by_subscriber_id,
               c.expire_at,
               c.revoked,
               c.locked_mac,
               c.disabled_reason,
               c.disabled_at,
               c.disabled_by,
               c.created_at,
               c.card_speed_down_kbps,
               c.card_speed_up_kbps,
               c.frozen_remaining_seconds,
               c.deleted_at,
               COALESCE(p.name, '') AS plan_name,
               COALESCE(p.currency, 'ILS') AS currency,
               COALESCE(a.sessions_count, 0) AS sessions_count,
               COALESCE(a.unique_macs, 0) AS unique_macs,
               COALESCE(a.online_sessions, 0) AS online_sessions,
               COALESCE(a.total_session_seconds, 0) AS total_session_seconds,
               COALESCE(a.total_upload_bytes, 0) AS total_upload_bytes,
               COALESCE(a.total_download_bytes, 0) AS total_download_bytes,
               a.first_session_at,
               a.last_connect_at,
               a.last_disconnect_at,
               a.last_seen_at,
               l.acctsessionid AS latest_session_id,
               l.acctstarttime AS latest_start_at,
               l.acctupdatetime AS latest_update_at,
               l.acctstoptime AS latest_stop_at,
               COALESCE(l.acctsessiontime, 0) AS latest_session_seconds,
               COALESCE(l.acctinputoctets, 0) AS latest_upload_bytes,
               COALESCE(l.acctoutputoctets, 0) AS latest_download_bytes,
               COALESCE(l.nasipaddress, '') AS latest_nas_ip,
               COALESCE(l.callingstationid, '') AS latest_mac,
               COALESCE(l.framedipaddress, '') AS latest_framed_ip
          FROM cards c
          LEFT JOIN access_plans p
            ON p.tenant_id = c.tenant_id AND p.id = c.plan_id
          LEFT JOIN acct a
            ON a.tenant_id = c.tenant_id AND a.username = c.username
          LEFT JOIN latest l
            ON l.tenant_id = c.tenant_id AND l.username = c.username
         WHERE c.tenant_id = ?
           AND c.batch_id = ?
           AND c.deleted_at IS NULL
         ORDER BY c.id DESC
        """,
        (tenant_id, tenant_id, tenant_id, batch_id),
    ).fetchall()
    now = datetime.utcnow()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        status = _card_status_meta(item, now)
        remaining = _card_remaining_meta(item, now)
        item.update({
            "status_key": status["key"],
            "status_label": status["label"],
            "status_tone": status["tone"],
            "status_rank": status["rank"],
            "remaining_label": remaining["label"],
            "remaining_seconds": remaining["seconds"],
            "remaining_state": remaining["state"],
            "is_online": int(item.get("online_sessions") or 0) > 0,
            "first_used_label": _format_card_dt(item.get("first_used_at")),
            "created_label": _format_card_dt(item.get("created_at")),
            "expire_label": _format_card_dt(item.get("expire_at")),
            "last_connect_label": _format_card_dt(item.get("last_connect_at") or item.get("latest_start_at")),
            "last_disconnect_label": _format_card_dt(item.get("last_disconnect_at") or item.get("latest_stop_at")),
            "last_seen_label": _format_card_dt(item.get("last_seen_at") or item.get("latest_update_at")),
            "total_time_label": _format_card_seconds(item.get("total_session_seconds")),
            "latest_time_label": _format_card_seconds(item.get("latest_session_seconds")),
            "total_upload_label": _format_card_bytes(item.get("total_upload_bytes")),
            "total_download_label": _format_card_bytes(item.get("total_download_bytes")),
            "latest_upload_label": _format_card_bytes(item.get("latest_upload_bytes")),
            "latest_download_label": _format_card_bytes(item.get("latest_download_bytes")),
            "speed_label": (
                f"{int(item.get('card_speed_down_kbps') or 0)} / {int(item.get('card_speed_up_kbps') or 0)} Kbps"
                if int(item.get("card_speed_down_kbps") or 0) or int(item.get("card_speed_up_kbps") or 0)
                else "سرعة الحزمة"
            ),
        })
        out.append(item)
    return out


def _batch_cards_summary(items: list[dict]) -> dict:
    return {
        "total": len(items),
        "online": sum(1 for item in items if item["status_key"] == "online"),
        "ready": sum(1 for item in items if item["status_key"] == "ready"),
        "used_offline": sum(1 for item in items if item["status_key"] == "used_offline"),
        "expired": sum(1 for item in items if item["status_key"] == "expired"),
        "revoked": sum(1 for item in items if item["status_key"] == "revoked"),
        "sessions": sum(int(item.get("sessions_count") or 0) for item in items),
        "upload_label": _format_card_bytes(sum(int(item.get("total_upload_bytes") or 0) for item in items)),
        "download_label": _format_card_bytes(sum(int(item.get("total_download_bytes") or 0) for item in items)),
    }


def _selected_card_ids() -> list[int]:
    ids: list[int] = []
    for raw in request.form.getlist("card_ids"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    return sorted(set(ids))


def cards_batch_cards_actions(batch_id: int):
    from ..db.repos import cards_repo

    svc = get_cards_service()
    tenant_id = _tid()
    action = _form_str("bulk_action")
    card_ids = _selected_card_ids()
    return_to = request.form.get("return_to") or url_for("radius.cards_of_batch", batch_id=batch_id)
    if not card_ids:
        flash("اختَر كرت واحد على الأقل لتنفيذ الإجراء.", "error")
        return redirect(return_to)

    unit_map = {"minutes": 60, "hours": 3600, "days": 86400}
    amount = _form_int("time_amount")
    unit = (_form_str("time_unit") or "minutes").strip().lower()
    reason = _form_str("reason")
    changed = 0
    skipped = 0
    errors: list[str] = []

    for card_id in card_ids:
        card = cards_repo.get_card(tenant_id, card_id)
        if not card or card.batch_id != batch_id:
            skipped += 1
            continue
        try:
            if action == "enable":
                svc.enable_card(actor=_actor(), card_id=card_id)
            elif action == "disable":
                svc.disable_card(actor=_actor(), card_id=card_id, reason=reason)
            elif action == "soft_delete":
                svc.soft_delete_card(actor=_actor(), card_id=card_id, reason=reason or "حذف من جدول كروت الحزمة")
            elif action == "reset_usage":
                svc.reset_card_usage(actor=_actor(), card_id=card_id)
            elif action == "disconnect":
                svc.disconnect_card(actor=_actor(), username=card.username)
            elif action in {"add_time", "subtract_time"}:
                if amount <= 0 or unit not in unit_map:
                    raise RadiusValidationError("حدد مدة صحيحة لتعديل وقت البطاقة.")
                delta = amount * unit_map[unit] * (-1 if action == "subtract_time" else 1)
                svc.adjust_card_time(
                    actor=_actor(),
                    card_id=card_id,
                    delta_seconds=delta,
                    username=card.username,
                )
            elif action == "set_speed":
                svc.set_card_speed(
                    actor=_actor(),
                    card_id=card_id,
                    down_kbps=_form_int("speed_down_kbps"),
                    up_kbps=_form_int("speed_up_kbps"),
                    username=card.username,
                )
            elif action == "lock_mac":
                svc.lock_card_mac(actor=_actor(), card_id=card_id, mac=_form_str("mac"))
            elif action == "unlock_mac":
                svc.unlock_card_mac(actor=_actor(), card_id=card_id)
            else:
                flash("إجراء غير معروف.", "error")
                return redirect(return_to)
            changed += 1
        except RadiusError as exc:
            errors.append(f"{card.username}: {exc.message}")

    labels = {
        "enable": "تفعيل",
        "disable": "إيقاف",
        "soft_delete": "حذف",
        "reset_usage": "تصفير الاستخدام",
        "disconnect": "قطع الاتصال",
        "add_time": "إضافة وقت",
        "subtract_time": "خصم وقت",
        "set_speed": "تعديل السرعة",
        "lock_mac": "تثبيت MAC",
        "unlock_mac": "فك MAC",
    }
    if changed:
        flash(f"تم تنفيذ {labels.get(action, 'الإجراء')} على {changed} كرت.", "success")
    if skipped:
        flash(f"تم تجاهل {skipped} كرت خارج هذه الحزمة.", "warning")
    if errors:
        flash("لم تكتمل بعض الكروت: " + " | ".join(errors[:3]), "error")
    return redirect(return_to)


def cards_of_batch(batch_id: int):
    from ..db.repos import cards_repo, plans_repo

    batch = cards_repo.get_batch(_tid(), batch_id, include_deleted=False)
    plan = None
    if batch:
        plan = plans_repo.get_plan(_tid(), batch.plan_id)
    items = _batch_cards_details(_tid(), batch_id) if batch else []
    return render_template(
        "radius/cards_of_batch.html",
        items=items,
        batch=batch,
        plan=plan,
        summary=_batch_cards_summary(items),
    )


def cards_revoke(card_id: int):
    try:
        get_cards_service().revoke_card(actor=_actor(), card_id=card_id)
        flash("تم إلغاء البطاقة.", "warning")
    except RadiusError as e:
        flash(e.message, "error")
    return redirect(request.referrer or url_for("radius.cards_list"))
