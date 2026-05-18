"""DashboardService — جمع KPIs من كل المصادر."""
from __future__ import annotations

from collections import Counter

from ..core.types import DashboardSnapshot
from ..integration.adapter import RadiusAdapter
from ..stores.admins_store import AdminsStore
from ..stores.cards_store import CardsStore
from .audit import RadiusAuditService


class DashboardService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def snapshot(self) -> DashboardSnapshot:
        subs = list(self._adapter.list_accounts(limit=10_000))
        plans = list(self._adapter.list_profiles(limit=1000))
        nas = list(self._adapter.list_nas(limit=1000))
        online = list(self._adapter.list_online(limit=2000))
        cards_stats = CardsStore.instance().stats()
        admins_count = len(AdminsStore.instance().list_admins())

        enabled = sum(1 for u in subs if u.status == "enabled")
        expired = sum(1 for u in subs if u.status == "expired")

        # top plans
        plan_name_by_id = {p.id: p.name for p in plans}
        counts = Counter((plan_name_by_id.get(u.plan_id, "—") for u in subs))
        top = counts.most_common(5)

        bytes_in = sum(s.bytes_in for s in online)
        bytes_out = sum(s.bytes_out for s in online)

        recent = list(self._audit.recent(limit=8))

        return DashboardSnapshot(
            total_subscribers=len(subs),
            enabled_subscribers=enabled,
            expired_subscribers=expired,
            total_cards=cards_stats.get("total_cards", 0),
            used_cards=cards_stats.get("used_cards", 0),
            online_now=len(online),
            nas_total=len(nas),
            nas_online=sum(1 for d in nas if d.enabled),
            plans_total=len(plans),
            admins_total=admins_count,
            bytes_today_in=bytes_in,
            bytes_today_out=bytes_out,
            revenue_today=sum((next((p.price for p in plans if p.id == u.plan_id), 0.0)) for u in subs[-20:]),
            revenue_month=sum((next((p.price for p in plans if p.id == u.plan_id), 0.0)) for u in subs),
            recent_actions=tuple(recent),
            top_plans=tuple(top),
        )


def get_dashboard_service() -> DashboardService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return DashboardService(get_radius_adapter(), audit=get_audit_service())
