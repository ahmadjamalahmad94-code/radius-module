"""Executive dashboard, reports, and immutable archive analytics."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.system_config import local_period_utc_range, local_today
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
        self._rev_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def executive_summary(self, *, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        # يومُ المشغّل لا يوم UTC — راجع local_today في system_config.
        today = local_today(self.tenant_id).isoformat()
        period_anchor = date_from[:10] if date_from else today
        month = period_anchor[:7]
        year = period_anchor[:4]
        return {
            "filters": {"date_from": date_from, "date_to": date_to},
            "subscribers": {
                # Real subscribers only — excludes card rows (user_type='card'
                # mirrors of imported/generated cards, card_marketplace, and any
                # username present in `cards`). Single source of truth =
                # subscribers_repo.count_subscribers(user_type='subscriber'),
                # same classification as «قائمة المشتركين». disabled = total-active.
                "total": self._real_subscriber_count(),
                "active": self._real_subscriber_count(status="enabled"),
                "disabled": (self._real_subscriber_count()
                             - self._real_subscriber_count(status="enabled")),
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
                # لكلّ عملة رقمها (``[{currency, revenue, profit, payments}]``) —
                # الحقول المفردة أعلاه مجموعٌ خامّ للتوافق.
                "revenue_by_currency": self._rev(date_from, date_to)["by_currency"],
                "revenue_today_by_currency": self._rev(today, today)["by_currency"],
                "revenue_month_by_currency": self._rev_period(month)["by_currency"],
                "revenue_year_by_currency": self._rev_period(year)["by_currency"],
                "mixed_currency": bool(self._rev(date_from, date_to)["mixed_currency"]),
                "url": "/admin/radius/reports/financial",
            },
            "cards": {
                # الكروت المحذوفة (ومنها كروت الحزم المؤرشفة) لا تُحسب.
                "total": self._count("cards", "COALESCE(deleted_at, '') = ''"),
                "unused": self._count("cards", "COALESCE(deleted_at, '') = '' AND used=0 AND revoked=0"),
                "active": self._count("cards", "COALESCE(deleted_at, '') = '' AND used=1 AND revoked=0"),
                "expired": self._count("cards", "COALESCE(deleted_at, '') = '' AND expire_at!='' AND expire_at IS NOT NULL AND expire_at < ?", (today,)),
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
        if self._scope_id() is not None:
            # frozen NETWORK-wide snapshots — never shown to a scoped manager.
            return []
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

    # ── fix3 (F02 H2 / F01 F8 / F08 H3): the request admin's scope ──────────
    def _scope_id(self):
        """None = sees everything; else the manager whose data is shown."""
        from .subscriber_scope import current_scope_admin_id
        return current_scope_admin_id(tenant_id=self.tenant_id)

    def _table_scope(self, table: str) -> tuple[str, list[Any]]:
        if table == "subscribers":
            scope = self._scope_id()
            if scope is None:
                return "", []
            from .subscriber_scope import owner_scope_clause
            return owner_scope_clause(int(scope), tenant_id=self.tenant_id)
        if table == "cards":
            from .card_batch_scope import batch_scope_sql
            return batch_scope_sql(column="batch_id", tenant_id=self.tenant_id)
        return "", []

    def _count(self, table: str, where: str = "1=1", params: tuple[Any, ...] = ()) -> int:
        sc, sv = self._table_scope(table)
        row = db().execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id=? AND {where}" + sc,
            (self.tenant_id, *params, *sv),
        ).fetchone()
        return int(row["c"] or 0)

    def _real_subscriber_count(self, *, status: str | None = None) -> int:
        """Real subscribers only (excludes card rows) — same classification as
        «قائمة المشتركين» via the shared repo filter."""
        try:
            from ..db.repos import subscribers_repo
            return int(subscribers_repo.count_subscribers(
                self.tenant_id, user_type="subscriber", status=status,
                owner_admin_id=self._scope_id()))
        except Exception:  # noqa: BLE001
            return 0

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

    def _utc_bounds(self, date_from: str = "", date_to: str = "") -> tuple[str, str]:
        """``YYYY-MM-DD`` محلّيّ (يوم المشغّل) → حدّا UTC [from, to) شاملين لليوم
        الأخير كاملًا. كانت المقارنة ``substr(created_at,1,10)`` = يوم UTC."""
        from datetime import date as _date

        def _valid(value: str) -> bool:
            try:
                _date.fromisoformat((value or "")[:10])
                return True
            except ValueError:
                return False

        # قيمة غير صالحة تُهمَل (لا تُستبدَل بـ«اليوم» كما يفعل المساعد) —
        # المسارات تتحقّق وتنبّه قبل الوصول هنا.
        lower = (local_period_utc_range("daily", date_from[:10], self.tenant_id)[0]
                 if date_from and _valid(date_from) else "")
        upper = (local_period_utc_range("daily", date_to[:10], self.tenant_id)[1]
                 if date_to and _valid(date_to) else "")
        return lower, upper

    def _rev(self, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        """الإيراد الموحّد (accounting_repo.revenue_summary) لفترة محلّيّة —
        المصدر نفسه لـ/api/v1/finance/revenue والمركز المالي. مخزَّن لكلّ نداء."""
        key = (date_from or "", date_to or "")
        if key not in self._rev_cache:
            from ..db.repos import accounting_repo
            lower, upper = self._utc_bounds(*key)
            self._rev_cache[key] = accounting_repo.revenue_summary(
                self.tenant_id, utc_from=lower, utc_to=upper)
        return self._rev_cache[key]

    def _rev_period(self, period: str) -> dict[str, Any]:
        """``YYYY-MM`` أو ``YYYY`` محلّيّ → الإيراد الموحّد لذلك الشهر/السنة."""
        from ..db.repos import accounting_repo
        key = ("period", period)
        if key not in self._rev_cache:
            grain = "yearly" if len(period) == 4 else "monthly"
            lower, upper = local_period_utc_range(grain, period, self.tenant_id)
            self._rev_cache[key] = accounting_repo.revenue_summary(
                self.tenant_id, utc_from=lower, utc_to=upper)
        return self._rev_cache[key]

    def _revenue_total(self, *, date_from: str = "", date_to: str = "") -> float:
        # دفعات الدفتر (صافية من الإلغاء) + سجلّات الكروت المُرحَّلة — كانت
        # revenue_records وحده (الدفعات لا تكتب فيه) ⇒ «الإيرادات 0 ₪».
        return float(self._rev(date_from, date_to)["revenue"])

    def _margin_total(self, *, date_from: str = "", date_to: str = "") -> float:
        return float(self._rev(date_from, date_to)["profit"])

    def _invoice_total(self, *, date_from: str = "", date_to: str = "") -> float:
        # «الدفعات» = دفعات المشتركين في الدفتر (كانت الفواتير المدفوعة فقط:
        # «130 ₪» مقابل ~96.9 ألف في الدفتر).
        return float(self._rev(date_from, date_to)["payments"])

    def _revenue_for_period(self, period: str) -> float:
        return float(self._rev_period(period)["revenue"])

    def _margin_for_period(self, period: str) -> float:
        return float(self._rev_period(period)["profit"])

    def _subscriber_debt(self) -> int:
        return self._count("subscribers", "deleted_at IS NULL AND balance < 0")

    def _subscriber_debt_amount(self) -> float:
        sc, sv = self._table_scope("subscribers")
        row = db().execute(
            "SELECT COALESCE(SUM(ABS(balance)),0) AS total FROM subscribers WHERE tenant_id=? AND deleted_at IS NULL AND balance < 0" + sc,
            (self.tenant_id, *sv),
        ).fetchone()
        return round(float(row["total"] or 0), 2)

    def _online_count(self) -> int:
        # المصدر نفسه للوحة والـAPI: جلسات مشتركين/كروت حقيقيّين فقط.
        scope = self._scope_id()
        if scope is not None:
            from .subscriber_scope import scope_sql
            sc, sv = scope_sql("username", scope=int(scope), tenant_id=self.tenant_id,
                               use_request=False)
            row = db().execute(
                "SELECT COUNT(DISTINCT username) AS c FROM radacct WHERE tenant_id=? "
                "AND acctstoptime IS NULL" + sc, (self.tenant_id, *sv)).fetchone()
            return int(row["c"] or 0)
        from .dashboard_metrics import get_online_count
        return int(get_online_count(self.tenant_id))

    def _ending_soon(self) -> int:
        today = local_today(self.tenant_id)
        end = (today + timedelta(days=7)).isoformat()
        return self._count(
            "subscribers",
            "deleted_at IS NULL AND status='enabled' AND expire_at IS NOT NULL AND expire_at!='' AND substr(expire_at,1,10) BETWEEN ? AND ?",
            (today.isoformat(), end),
        )

    def _connected_cards(self) -> int:
        from .card_batch_scope import batch_scope_sql
        bsc, bsv = batch_scope_sql(column="c.batch_id", tenant_id=self.tenant_id)
        row = db().execute(
            """
            SELECT COUNT(DISTINCT c.id) AS c
            FROM cards c
            JOIN radacct a ON a.tenant_id=c.tenant_id AND a.username=c.username AND a.acctstoptime IS NULL
            WHERE c.tenant_id=? AND c.used=1 AND c.revoked=0
            """ + bsc,
            (self.tenant_id, *bsv),
        ).fetchone()
        return int(row["c"] or 0)

    def _cards_sold_for_period(self, period: str) -> int:
        from .card_batch_scope import batch_scope_sql
        bsc, bsv = batch_scope_sql(column="batch_id", tenant_id=self.tenant_id)
        row = db().execute(
            """
            SELECT COUNT(*) AS c FROM cards
            WHERE tenant_id=? AND used=1 AND first_used_at IS NOT NULL
              AND substr(first_used_at,1,?)=?
            """ + bsc,
            (self.tenant_id, len(period), period, *bsv),
        ).fetchone()
        return int(row["c"] or 0)

    def _distributor_scope(self, column: str) -> tuple[str, list[Any]]:
        scope = self._scope_id()
        if scope is None:
            return "", []
        return (f" AND {column} IN (SELECT id FROM distributors WHERE admin_id = ? "
                "OR login_admin_id = ?)", [int(scope), int(scope)])

    def _profit_share_total(self, beneficiary_type: str) -> float:
        dsc, dsv = self._distributor_scope("beneficiary_id")
        row = db().execute(
            """
            SELECT COALESCE(SUM(share_amount_minor),0) AS total
            FROM profit_shares
            WHERE tenant_id=? AND beneficiary_type=? AND status IN ('posted','pending')
            """ + dsc,
            (self.tenant_id, beneficiary_type, *dsv),
        ).fetchone()
        return _money(row["total"])

    def _alerts(self) -> list[dict[str, Any]]:
        from .subscriber_scope import entity_scope_sql
        esc, esv = entity_scope_sql("target_type", "target_id", actor_type_col="actor_type",
                                    actor_id_col="actor_id", tenant_id=self.tenant_id)
        rows = db().execute(
            """
            SELECT severity, event_key, message, created_at
            FROM business_events
            WHERE tenant_id=? AND severity IN ('warning','error','critical')
            """ + esc + """
            ORDER BY id DESC LIMIT 10
            """,
            (self.tenant_id, *esv),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _financial_report(self, *, date_from: str = "", date_to: str = "") -> list[dict[str, Any]]:
        by_cur = self._rev(date_from, date_to)["by_currency"]

        def _split(key: str) -> list[dict[str, Any]]:
            return [{"currency": c["currency"], "total": c[key]} for c in by_cur]

        return [
            {"metric": "revenue", "value": self._revenue_total(date_from=date_from, date_to=date_to),
             "by_currency": _split("revenue")},
            {"metric": "payments", "value": self._invoice_total(date_from=date_from, date_to=date_to),
             "by_currency": _split("payments")},
            {"metric": "margin", "value": self._margin_total(date_from=date_from, date_to=date_to),
             "by_currency": _split("profit")},
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
            """ + self._distributor_scope("beneficiary_id")[0] + """
            GROUP BY beneficiary_id
            ORDER BY share_total_minor DESC
            """,
            (self.tenant_id, *self._distributor_scope("beneficiary_id")[1]),
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
        today = local_today().isoformat()
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
            # آخر يومٍ حقيقيّ في الشهر (كان «-31» دائمًا: 2026-02-31 تاريخٌ غير صالح).
            import calendar
            try:
                last = calendar.monthrange(int(period[:4]), int(period[5:7]))[1]
            except (TypeError, ValueError):
                last = 31
            return period + "-01", f"{period}-{last:02d}"
        return period + "-01-01", period + "-12-31"
