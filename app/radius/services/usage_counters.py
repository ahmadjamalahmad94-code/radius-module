"""Usage counters and quota decision foundation.

Reads `radacct` only. Does not disconnect users, mutate auth policy, create
ledger entries, or perform reseller settlement.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from app.radius.db.connection import db


def _window_prefix(window: str, now: datetime | None = None) -> str:
    frozen_now = str(os.environ.get("HOBERADIUS_USAGE_COUNTERS_NOW") or "").strip()
    if now is None and frozen_now:
        try:
            now = datetime.fromisoformat(frozen_now.replace("Z", "+00:00"))
        except ValueError:
            now = None
    current = now or datetime.utcnow()
    if window == "monthly":
        return current.strftime("%Y-%m")
    return current.strftime("%Y-%m-%d")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


class UsageCountersService:
    def tenant_summary(self, *, tenant_id: int, window: str = "daily") -> dict[str, Any]:
        prefix = _window_prefix(window)
        row = db().execute(
            """
            SELECT COUNT(*) AS sessions,
                   COALESCE(SUM(acctinputoctets),0) AS bytes_in,
                   COALESCE(SUM(acctoutputoctets),0) AS bytes_out,
                   COALESCE(SUM(acctsessiontime),0) AS seconds
            FROM radacct
            WHERE tenant_id = ? AND COALESCE(acctstarttime, '') LIKE ?
            """,
            (int(tenant_id), f"{prefix}%"),
        ).fetchone()
        by_nas = db().execute(
            """
            SELECT nasipaddress,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(acctinputoctets),0) AS bytes_in,
                   COALESCE(SUM(acctoutputoctets),0) AS bytes_out
            FROM radacct
            WHERE tenant_id = ? AND COALESCE(acctstarttime, '') LIKE ?
            GROUP BY nasipaddress
            ORDER BY sessions DESC, nasipaddress ASC
            """,
            (int(tenant_id), f"{prefix}%"),
        ).fetchall()
        return {
            "scope": "tenant",
            "tenant_id": int(tenant_id),
            "window": window,
            "window_prefix": prefix,
            "sessions": _int(row["sessions"] if row else 0),
            "bytes_in": _int(row["bytes_in"] if row else 0),
            "bytes_out": _int(row["bytes_out"] if row else 0),
            "total_bytes": _int(row["bytes_in"] if row else 0) + _int(row["bytes_out"] if row else 0),
            "seconds": _int(row["seconds"] if row else 0),
            "by_nas": [dict(item) for item in by_nas],
        }

    def subscriber_summary(self, *, tenant_id: int, username: str, window: str = "daily") -> dict[str, Any]:
        prefix = _window_prefix(window)
        row = db().execute(
            """
            SELECT username,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(acctinputoctets),0) AS bytes_in,
                   COALESCE(SUM(acctoutputoctets),0) AS bytes_out,
                   COALESCE(SUM(acctsessiontime),0) AS seconds
            FROM radacct
            WHERE tenant_id = ? AND username = ? AND COALESCE(acctstarttime, '') LIKE ?
            GROUP BY username
            """,
            (int(tenant_id), str(username), f"{prefix}%"),
        ).fetchone()
        return self._summary_row(
            row=dict(row) if row else {"username": username},
            scope="subscriber",
            tenant_id=tenant_id,
            window=window,
            prefix=prefix,
        )

    def plan_summary(self, *, tenant_id: int, plan_id: int, window: str = "daily") -> dict[str, Any]:
        prefix = _window_prefix(window)
        row = db().execute(
            """
            SELECT s.plan_id,
                   COUNT(a.radacctid) AS sessions,
                   COALESCE(SUM(a.acctinputoctets),0) AS bytes_in,
                   COALESCE(SUM(a.acctoutputoctets),0) AS bytes_out,
                   COALESCE(SUM(a.acctsessiontime),0) AS seconds
            FROM subscribers s
            LEFT JOIN radacct a
              ON a.tenant_id = s.tenant_id
             AND a.username = s.username
             AND COALESCE(a.acctstarttime, '') LIKE ?
            WHERE s.tenant_id = ? AND s.plan_id = ?
            GROUP BY s.plan_id
            """,
            (f"{prefix}%", int(tenant_id), int(plan_id)),
        ).fetchone()
        data = dict(row) if row else {"plan_id": int(plan_id)}
        return self._summary_row(
            row=data,
            scope="plan",
            tenant_id=tenant_id,
            window=window,
            prefix=prefix,
        )

    def quota_decision(
        self,
        *,
        tenant_id: int,
        username: str,
        limit_bytes: int,
        warning_ratio: float = 0.8,
        window: str = "daily",
    ) -> dict[str, Any]:
        summary = self.subscriber_summary(tenant_id=tenant_id, username=username, window=window)
        used = int(summary["total_bytes"])
        limit = max(0, int(limit_bytes or 0))
        if limit <= 0:
            decision = "allow"
            reason = "no_quota_limit"
        elif used >= limit:
            decision = "block"
            reason = "quota_exceeded"
        elif used >= int(limit * float(warning_ratio or 0.8)):
            decision = "warn"
            reason = "quota_warning_threshold"
        else:
            decision = "allow"
            reason = "within_quota"
        return {
            "decision": decision,
            "reason": reason,
            "enforced": False,
            "username": username,
            "limit_bytes": limit,
            "used_bytes": used,
            "remaining_bytes": max(limit - used, 0) if limit else None,
            "window": window,
        }

    def _summary_row(
        self,
        *,
        row: dict[str, Any],
        scope: str,
        tenant_id: int,
        window: str,
        prefix: str,
    ) -> dict[str, Any]:
        bytes_in = _int(row.get("bytes_in"))
        bytes_out = _int(row.get("bytes_out"))
        return {
            "scope": scope,
            "tenant_id": int(tenant_id),
            "window": window,
            "window_prefix": prefix,
            "sessions": _int(row.get("sessions")),
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
            "total_bytes": bytes_in + bytes_out,
            "seconds": _int(row.get("seconds")),
            **{k: v for k, v in row.items() if k not in {"sessions", "bytes_in", "bytes_out", "seconds"}},
        }
