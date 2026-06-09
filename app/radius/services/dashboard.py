"""DashboardService — جمع KPIs من كل المصادر.

R8.1: استبدلنا الاستدعاء المتزامن لـ `adapter.list_online(2000)` (الذي
كان يضرب MikroTik API على كل router لكل request) بقراءات SQL مباشرة
من جدول radacct. السبب: list_online() كان يستغرق 45–103s عند تعذّر
الاتصال بـ MT، فيُغلِق nginx الطلب بـ 504. الآن radacct هو المصدر
الأساسي بعد أن أصبح FreeRADIUS rlm_sql يكتب فيه (R1→R7).

R10.2: إصلاحان مهمّان في عَدّ الأرقام:
  1. `total_subscribers / enabled / expired` كانت تشمل صفوف الكروت
     (user_type='card') أيضًا — فيظهر العدد 2057 بدل 37. الآن نُمرّر
     user_type='subscriber' عند الاستدعاء، فيُحسب المشتركون فقط.
  2. `revenue_today / revenue_month` كانت تجمع أسعار الباقات لآخر 20
     مشترك / لجميع المشتركين (بغض النظر عن الدفع الفعلي) — أرقام
     خيالية. الآن نقرأ من `payment_transactions` (status='posted')
     بفلترة `created_at` لليوم / للشهر، فالأرقام تطابق الـ ledger.

نحافظ على نفس DTO و نفس الـ UI semantics. الفروق الوحيدة:
- `online_now` يأتي من COUNT(*) في radacct بدل len(MT-list).
- `bytes_today_in`/`out` يأتيان من SUM(...) على الجلسات المفتوحة
  (نفس الـ semantics السابقة — كانا في الحقيقة "bytes الحية حاليًا"
  لا "اليوم"؛ نُبقي السلوك القديم بنفس الأسماء).
- لا اتصال بـ MikroTik أثناء render؛ التحديث الحيّ يأتي من
  acct packets التي يكتبها FreeRADIUS في radacct.
"""
from __future__ import annotations

import logging
from collections import Counter

from ..core.constants import USER_TYPE_SUBSCRIBER
from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import DashboardSnapshot
from ..db.connection import db
from ..integration.adapter import RadiusAdapter
from ..stores.admins_store import AdminsStore
from ..stores.cards_store import CardsStore
from .audit import RadiusAuditService

_LOG = logging.getLogger(__name__)


def _tid() -> int:
    """tenant_id من Flask g مع fallback آمن — يطابق نمط dashboard_metrics."""
    try:
        from flask import g
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (ImportError, RuntimeError):
        return DEFAULT_TENANT_ID


def _live_session_totals(tenant_id: int) -> tuple[int, int, int]:
    """يُرجع (online_count, bytes_in_sum, bytes_out_sum) من الجلسات
    المفتوحة في radacct. لا يرفع أبدًا — fallback (0,0,0) عند أي خطأ
    كي لا نُكسر render الـ dashboard."""
    try:
        row = db().execute(
            "SELECT COUNT(*) AS c, "
            "       COALESCE(SUM(acctinputoctets), 0)  AS bi, "
            "       COALESCE(SUM(acctoutputoctets), 0) AS bo "
            "  FROM radacct "
            " WHERE tenant_id = ? AND acctstoptime IS NULL",
            (tenant_id,),
        ).fetchone()
        if not row:
            return 0, 0, 0
        return int(row["c"] or 0), int(row["bi"] or 0), int(row["bo"] or 0)
    except Exception:  # noqa: BLE001
        _LOG.exception("dashboard: radacct totals query failed — using zeros")
        return 0, 0, 0


def _revenue_totals(tenant_id: int) -> tuple[float, float]:
    """R10.2: يُرجع (revenue_today, revenue_month) — جمع المدفوعات الفعلية
    من payment_transactions بفلترة status='posted' (لا نحسب الـ voided
    / refunded). الـ created_at مُخزَّن كـ ISO TEXT، فنُقارنه بـ
    boundaries نصّيّة — sqlite يقارنها lexicographically بشكل صحيح
    طالما الـ format موحَّد (YYYY-MM-DDTHH:MM:SS...).

    fallback (0.0, 0.0) عند أي خطأ — مفضّل على إيقاف الـ dashboard."""
    try:
        row = db().execute(
            "SELECT "
            "  COALESCE(SUM(CASE WHEN created_at >= date('now','start of day') "
            "                    THEN effective_price ELSE 0 END), 0) AS today, "
            "  COALESCE(SUM(CASE WHEN created_at >= date('now','start of month') "
            "                    THEN effective_price ELSE 0 END), 0) AS month "
            "  FROM payment_transactions "
            " WHERE tenant_id = ? AND status = 'posted'",
            (tenant_id,),
        ).fetchone()
        if not row:
            return 0.0, 0.0
        return float(row["today"] or 0.0), float(row["month"] or 0.0)
    except Exception:  # noqa: BLE001
        _LOG.exception("dashboard: revenue totals query failed — using zeros")
        return 0.0, 0.0


class DashboardService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def snapshot(self) -> DashboardSnapshot:
        # R10.2: user_type='subscriber' يستثني صفوف الكروت — العدد يطابق
        # شاشة /users الحالية (بعد R9.0).
        subs = list(self._adapter.list_accounts(
            user_type=USER_TYPE_SUBSCRIBER, limit=10_000))
        plans = list(self._adapter.list_profiles(limit=1000))
        nas = list(self._adapter.list_nas(limit=1000))
        # R8.1: radacct بدل MT API — مكروسيكوندز بدل عشرات الثواني.
        online_now, bytes_in, bytes_out = _live_session_totals(_tid())
        # R10.2: أرقام إيرادات من payment_transactions بدل تجميع
        # أسعار الباقات الوهمي.
        revenue_today, revenue_month = _revenue_totals(_tid())
        cards_stats = CardsStore.instance().stats()
        admins_count = len(AdminsStore.instance().list_admins())

        enabled = sum(1 for u in subs if u.status == "enabled")
        expired = sum(1 for u in subs if u.status == "expired")

        # top plans
        plan_name_by_id = {p.id: p.name for p in plans}
        counts = Counter((plan_name_by_id.get(u.plan_id, "—") for u in subs))
        top = counts.most_common(5)

        recent = list(self._audit.recent(limit=8))

        return DashboardSnapshot(
            total_subscribers=len(subs),
            enabled_subscribers=enabled,
            expired_subscribers=expired,
            total_cards=cards_stats.get("total_cards", 0),
            used_cards=cards_stats.get("used_cards", 0),
            online_now=online_now,
            nas_total=len(nas),
            nas_online=sum(1 for d in nas if d.enabled),
            plans_total=len(plans),
            admins_total=admins_count,
            bytes_today_in=bytes_in,
            bytes_today_out=bytes_out,
            revenue_today=revenue_today,
            revenue_month=revenue_month,
            recent_actions=tuple(recent),
            top_plans=tuple(top),
        )


def get_dashboard_service() -> DashboardService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return DashboardService(get_radius_adapter(), audit=get_audit_service())
