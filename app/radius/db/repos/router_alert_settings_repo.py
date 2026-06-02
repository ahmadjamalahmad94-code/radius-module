"""router_alert_settings_repo — per-router smart-alert thresholds.

One row per (tenant, router). NULL columns mean "use the tenant-global
default" (stored in tenant_settings under network.alerts.*); the merge happens
in app/radius/services/smart_alerts.py, never here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..connection import db, transaction


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def get(tenant_id: int, router_id: int) -> Optional[dict]:
    cur = db().execute(
        "SELECT * FROM router_alert_settings WHERE tenant_id=? AND router_id=?",
        (int(tenant_id), int(router_id)),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def list_for_tenant(tenant_id: int) -> dict[int, dict]:
    """{router_id: settings_row} for every router with a saved override."""
    cur = db().execute(
        "SELECT * FROM router_alert_settings WHERE tenant_id=?",
        (int(tenant_id),),
    )
    return {int(r["router_id"]): dict(r) for r in cur.fetchall()}


def upsert(*, tenant_id: int, router_id: int, enabled: bool = True,
           offline_after_min: int | None = None,
           normal_speed_mbps: int | None = None,
           normal_usage_gb: int | None = None,
           usage_window: str | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO router_alert_settings(
                tenant_id, router_id, enabled, offline_after_min,
                normal_speed_mbps, normal_usage_gb, usage_window, updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, router_id) DO UPDATE SET
                enabled=excluded.enabled,
                offline_after_min=excluded.offline_after_min,
                normal_speed_mbps=excluded.normal_speed_mbps,
                normal_usage_gb=excluded.normal_usage_gb,
                usage_window=excluded.usage_window,
                updated_at=excluded.updated_at
            """,
            (int(tenant_id), int(router_id), 1 if enabled else 0,
             offline_after_min, normal_speed_mbps, normal_usage_gb,
             usage_window, _now()),
        )
