"""Executive dashboard, reports, and immutable archive analytics."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import now_iso, row_to_dict
from .accounting import AccountingService


def _money(minor: int | float | None) -> float:
    return round(float(minor or 0) / 100.0, 2)


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _load(raw: Any) -> Any:
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


class DashboardReportsService:
    """Read-only analytics facade plus immutable archive snapshot creation."""

    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)

    def executive_summary(self, *, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        today = datetime.now(timezone.utc).date().isoformat()
        period_anchor = date_from[:10] if date_from else today
        month = period_anchor[:7]
        year = period_anchor[:4]
        return {
            "filters": {"date_from": date_from, "date_to": date_to},
            "subscribers": {
                "total": self._count("subscribers", "deleted_at IS NULL"),
                "active": self._count("subscribers", "deleted_at IS NULL AND status='enabled'"),
                "disabled": self._count("subscribers", "deleted_at IS NULL AND status!='enabled'"),
                "online": self._online_count(),
                "ending_soon": self._ending_soon(),
                "debt": self._subscriber_debt(),
                "url": "/admin/radius/users",
            },
            "finance": {
                "revenue": self._revenue_total(date_from=date_from, date_to=date_to),
                "debts": self._subscriber_debt_amount(),
                "payments": self._invoice_total(date_from=date_from, date_to=date_to),
                "distributor_profits": self._profit_share_total("distributor"),
                "revenue_today": self._revenue_total(date_from=today, date_to=today),
                "revenue_month": self._revenue_for_period(month),
                "revenue_year": self._revenue_for_period(year),
                "margin_today": self._margin_total(date_from=today, date_to=today),
                "margin_month": self._margin_for_period(month),
                "margin_year": self._margin_for_period(year),
                "url": "/admin/radius/reports/financial",
            },
            "cards": {
                "total": self._count("cards"),
                "unused": self._count("cards", "used=0 AND revoked=0"),
                "active": self._count("cards", "used=1 AND revoked=0"),
                "expired": self._count("cards", "expire_at!='' AND expire_at IS NOT NULL AND expire_at < ?", (today,)),
                "connected": self._connected_cards(),
                "sold_today": self._cards_sold_for_period(today),
                "sold_month": self._cards_sold_for_period(month),
                "sold_year": self._cards_sold_for_period(year),
                "url": "/admin/radius/reports/cards",
            },
            "alerts": self._alerts(),
            "drilldowns": self.drilldown_links(),
        }

    def drilldown_links(self) -> dict[str, str]:
        return {
            "subscribers_total": "/admin/radius/users",
            "subscribers_active": "/admin/radius/users?status=enabled",
            "subscribers_disabled": "/admin/radius/users?status=disabled",
            "online_users": "/admin/radius/online",
            "cards_total": "/admin/radius/cards",
            "cards_unused": "/admin/radius/cards?used=0",
            "cards_active": "/admin/radius/cards?used=1",
            "financial_reports": "/admin/radius/reports/financial",
            "audit_reports": "/admin/radius/events",
        }

    def report_catalog(self) -> list[dict[str, str]]:
        return [
            {"key": "financial", "title": "التقارير المالية", "description": "إيرادات، دفعات، هامش، وديون", "url": "/admin/radius/reports/financial"},
            {"key": "subscribers", "title": "تقارير المشتركين", "description": "حالة المشتركين ونشاط الدخول", "url": "/admin/radius/reports?section=subscribers"},
            {"key": "cards", "title": "تقارير الكروت", "description": "المستخدمة وغير المستخدمة والمباعة", "url": "/admin/radius/reports/cards"},
            {"key": "revenue", "title": "تقارير الإيرادات", "description": "مجاميع يومية وشهرية وسنوية", "url": "/admin/radius/reports/financial?type=yearly"},
            {"key": "distributors", "title": "تقارير الموزعين", "description": "حصص وأرباح وحركة توزيع", "url": "/admin/radius/reports/distributors"},
            {"key": "usage", "title": "تقارير الاستخدام", "description": "جلسات الشبكة وحالات الاتصال", "url": "/admin/radius/reports/sessions"},
            {"key": "audit", "title": "تقارير التدقيق", "description": "أحداث النظام وعمليات المدراء", "url": "/admin/radius/events"},
        ]

    def report_data(self, report_type: str, *, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        report_type = (report_type or "financial").strip()
        if report_type == "cards":
            items = self._card_report()
        elif report_type == "distributors":
            items = self._distributor_report()
        elif report_type == "archive":
            items = self.list_archives()
        else:
            items = self._financial_report(date_from=date_from, date_to=date_to)
        return {"report_type": report_type, "items": items, "count": len(items)}

    def create_archive_snapshot(
        self,
        *,
        archive_type: str = "yearly",
        period: str = "",
        report_type: str = "financial",
        actor: str = "",
    ) -> dict[str, Any]:
        archive_type = archive_type if archive_type in {"daily", "monthly", "yearly"} else "yearly"
        period = (period or self._default_period(archive_type)).strip()
        existing = self._archive_by_key(archive_type=archive_type, period=period, report_type=report_type)
        if existing:
            existing["created"] = False
            return existing

        date_from, date_to = self._period_bounds(archive_type, period)
        summary = self.executive_summary(date_from=date_from, date_to=date_to)
        source_snapshot = AccountingService(self.tenant_id).create_report_snapshot(
            report_type="yearly" if archive_type == "yearly" else "daily",
            actor=actor,
            date_from=date_from,
            date_to=date_to,
            parameters={"archive_type": archive_type, "period": period, "report_type": report_type},
        )
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO report_archive_snapshots(
                    tenant_id, archive_type, period, report_type, summary_json,
                    source_snapshot_id, created_by, created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    self.tenant_id,
                    archive_type,
                    period,
                    report_type,
                    _json(summary),
                    source_snapshot.get("id"),
                    actor,
                    now_iso(),
                ),
            )
        created = self.get_archive(int(cur.lastrowid))
        created["created"] = True
        return created

    def list_archives(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM report_archive_snapshots
            WHERE tenant_id=?
            ORDER BY period DESC, id DESC
            LIMIT ?
            """,
            (self.tenant_id, int(limit)),
        ).fetchall()
        archives = [self._archive_row(row_to_dict(row)) for row in rows]
        # نُلحق بكل أرشيف بيانات اللقطة المصدر (financial_report_snapshots):
        # الفترة من/إلى + عدد الصفوف + الإجمالي — نفس أعمدة جدول لقطات
        # المحاسبة. الأرشيفات القديمة بلا لقطة مصدر تعرض «—» في الواجهة.
        self._attach_source_snapshots(archives)
        return archives

    def _attach_source_snapshots(self, archives: list[dict[str, Any]]) -> None:
        """قراءة لقطات المصدر دفعة واحدة وإلحاق (الفترة/الصفوف/الإجمالي) بكل أرشيف."""
        ids = sorted({
            int(a["source_snapshot_id"]) for a in archives
            if a.get("source_snapshot_id")
        })
        snapshots: dict[int, dict[str, Any]] = {}
        if ids:
            marks = ",".join("?" for _ in ids)
            try:
                for row in db().execute(
                    f"""
                    SELECT id, date_from, date_to, result_json
                    FROM financial_report_snapshots
                    WHERE tenant_id=? AND id IN ({marks})
                    """,
                    (self.tenant_id, *ids),
                ).fetchall():
                    data = row_to_dict(row)
                    data["result"] = _load(data.pop("result_json", "{}"))
                    snapshots[int(data["id"])] = data
            except Exception:  # noqa: BLE001 — الإلحاق تحسيني، لا يُسقط الصفحة
                snapshots = {}
        for archive in archives:
            snap = snapshots.get(int(archive.get("source_snapshot_id") or 0)) or {}
            result = snap.get("result") or {}
            archive["snapshot_date_from"] = snap.get("date_from") or result.get("date_from") or ""
            archive["snapshot_date_to"] = snap.get("date_to") or result.get("date_to") or ""
            archive["snapshot_rows"] = result.get("count") if snap else None
            archive["snapshot_total"] = result.get("total") if snap else None

    def get_archive(self, archive_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM report_archive_snapshots WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(archive_id)),
        ).fetchone()
        return self._archive_row(row_to_dict(row)) if row else {}

    def _count(self, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
        row = db().execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id=? AND {where}",
            (self.tenant_id, *params),
        ).fetchone()
        return int(row["c"] or 0)

    def _date_clause(self, column: str, *, date_from: str = "", date_to: str = "") -> tuple[str, list[Any]]:
        clause = ""
        params: list[Any] = []
        if date_from:
            clause += f" AND substr({column},1,10) >= ?"
            params.append(date_from)
        if date_to:
            clause += f" AND substr({column},1,10) <= ?"
            params.append(date_to)
        return clause, params

    def _revenue_total(self, *, date_from: str = "", date_to: str = "") -> float:
        clause, params = self._date_clause("created_at", date_from=date_from, date_to=date_to)
        row = db().execute(
            f"SELECT COALESCE(SUM(collected_amount_minor),0) AS total FROM revenue_records WHERE tenant_id=? AND status='posted'{clause}",
            (self.tenant_id, *params),
        ).fetchone()
        return _money(row["total"])

    def _margin_total(self, *, date_from: str = "", date_to: str = "") -> float:
        clause, params = self._date_clause("created_at", date_from=date_from, date_to=date_to)
        row = db().execute(
            f"SELECT COALESCE(SUM(net_profit_minor),0) AS total FROM revenue_records WHERE tenant_id=? AND status='posted'{clause}",
            (self.tenant_id, *params),
        ).fetchone()
        return _money(row["total"])

    def _invoice_total(self, *, date_from: str = "", date_to: str = "") -> float:
        clause, params = self._date_clause("created_at", date_from=date_from, date_to=date_to)
        row = db().execute(
            f"SELECT COALESCE(SUM(amount),0) AS total FROM invoices WHERE tenant_id=? AND status='paid'{clause}",
            (self.tenant_id, *params),
        ).fetchone()
        return round(float(row["total"] or 0), 2)

    def _revenue_for_period(self, period: str) -> float:
        row = db().execute(
            """
            SELECT COALESCE(SUM(collected_amount_minor),0) AS total
            FROM revenue_records
            WHERE tenant_id=? AND status='posted' AND substr(created_at,1,?)=?
            """,
            (self.tenant_id, len(period), period),
        ).fetchone()
        return _money(row["total"])

    def _margin_for_period(self, period: str) -> float:
        row = db().execute(
            """
            SELECT COALESCE(SUM(net_profit_minor),0) AS total
            FROM revenue_records
            WHERE tenant_id=? AND status='posted' AND substr(created_at,1,?)=?
            """,
            (self.tenant_id, len(period), period),
        ).fetchone()
        return _money(row["total"])

    def _subscriber_debt(self) -> int:
        return self._count("subscribers", "deleted_at IS NULL AND balance < 0")

    def _subscriber_debt_amount(self) -> float:
        row = db().execute(
            "SELECT COALESCE(SUM(ABS(balance)),0) AS total FROM subscribers WHERE tenant_id=? AND deleted_at IS NULL AND balance < 0",
            (self.tenant_id,),
        ).fetchone()
        return round(float(row["total"] or 0), 2)

    def _online_count(self) -> int:
        return self._count("radacct", "acctstoptime IS NULL")

    def _ending_soon(self) -> int:
        today = datetime.now(timezone.utc).date()
        end = (today + timedelta(days=7)).isoformat()
        return self._count(
            "subscribers",
            "deleted_at IS NULL AND status='enabled' AND expire_at IS NOT NULL AND expire_at!='' AND substr(expire_at,1,10) BETWEEN ? AND ?",
            (today.isoformat(), end),
        )

    def _connected_cards(self) -> int:
        row = db().execute(
            """
            SELECT COUNT(DISTINCT c.id) AS c
            FROM cards c
            JOIN radacct a ON a.tenant_id=c.tenant_id AND a.username=c.username AND a.acctstoptime IS NULL
            WHERE c.tenant_id=? AND c.used=1 AND c.revoked=0
            """,
            (self.tenant_id,),
        ).fetchone()
        return int(row["c"] or 0)

    def _cards_sold_for_period(self, period: str) -> int:
        row = db().execute(
            """
            SELECT COUNT(*) AS c FROM cards
            WHERE tenant_id=? AND used=1 AND first_used_at IS NOT NULL
              AND substr(first_used_at,1,?)=?
            """,
            (self.tenant_id, len(period), period),
        ).fetchone()
        return int(row["c"] or 0)

    def _profit_share_total(self, beneficiary_type: str) -> float:
        row = db().execute(
            """
            SELECT COALESCE(SUM(share_amount_minor),0) AS total
            FROM profit_shares
            WHERE tenant_id=? AND beneficiary_type=? AND status IN ('posted','pending')
            """,
            (self.tenant_id, beneficiary_type),
        ).fetchone()
        return _money(row["total"])

    def _alerts(self) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT severity, event_key, message, created_at
            FROM business_events
            WHERE tenant_id=? AND severity IN ('warning','error','critical')
            ORDER BY id DESC LIMIT 10
            """,
            (self.tenant_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _financial_report(self, *, date_from: str = "", date_to: str = "") -> list[dict[str, Any]]:
        return [
            {"metric": "revenue", "value": self._revenue_total(date_from=date_from, date_to=date_to)},
            {"metric": "payments", "value": self._invoice_total(date_from=date_from, date_to=date_to)},
            {"metric": "margin", "value": self._margin_total(date_from=date_from, date_to=date_to)},
            {"metric": "subscriber_debts", "value": self._subscriber_debt_amount()},
            {"metric": "distributor_profits", "value": self._profit_share_total("distributor")},
        ]

    def _card_report(self) -> list[dict[str, Any]]:
        cards = self.executive_summary()["cards"]
        return [{"metric": key, "value": value} for key, value in cards.items() if key != "url"]

    def _distributor_report(self) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT beneficiary_id AS distributor_id,
                   COUNT(*) AS share_count,
                   COALESCE(SUM(share_amount_minor),0) AS share_total_minor
            FROM profit_shares
            WHERE tenant_id=? AND beneficiary_type='distributor'
            GROUP BY beneficiary_id
            ORDER BY share_total_minor DESC
            """,
            (self.tenant_id,),
        ).fetchall()
        return [
            {
                "distributor_id": row["distributor_id"],
                "share_count": row["share_count"],
                "share_total": _money(row["share_total_minor"]),
            }
            for row in rows
        ]

    def _archive_by_key(self, *, archive_type: str, period: str, report_type: str) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT * FROM report_archive_snapshots
            WHERE tenant_id=? AND archive_type=? AND period=? AND report_type=?
            """,
            (self.tenant_id, archive_type, period, report_type),
        ).fetchone()
        return self._archive_row(row_to_dict(row)) if row else {}

    def _archive_row(self, row: dict[str, Any]) -> dict[str, Any]:
        if not row:
            return {}
        row["summary"] = _load(row.pop("summary_json", "{}"))
        return row

    @staticmethod
    def _default_period(archive_type: str) -> str:
        today = datetime.now(timezone.utc).date().isoformat()
        if archive_type == "daily":
            return today
        if archive_type == "monthly":
            return today[:7]
        return today[:4]

    @staticmethod
    def _period_bounds(archive_type: str, period: str) -> tuple[str, str]:
        if archive_type == "daily":
            return period, period
        if archive_type == "monthly":
            return period + "-01", period + "-31"
        return period + "-01-01", period + "-12-31"
