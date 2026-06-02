"""router_metrics_repo — push-mode router metric samples + heartbeat state.

Routers (behind NAT) push interface RX/TX + uptime to the metrics ingest
endpoint every ~2 min. We keep a rolling sample log plus a denormalised
per-router state row (last_push_at + last_sample_id) so the smart-alerts
evaluator can read the heartbeat and the last-two samples cheaply.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..connection import db, transaction


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _row(row: Any) -> dict[str, Any]:
    d = dict(row)
    try:
        d["interfaces"] = json.loads(d.get("interfaces_json") or "[]")
    except (TypeError, ValueError):
        d["interfaces"] = []
    return d


def record_sample(*, tenant_id: int, router_id: int, reported_at: str = "",
                  uptime_seconds: int | None = None,
                  interfaces: list | None = None) -> int:
    """Insert one push sample + bump the router's heartbeat. Returns sample id."""
    payload = json.dumps(interfaces or [], ensure_ascii=False)
    now = _now()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO router_metric_samples(
                tenant_id, router_id, reported_at, uptime_seconds,
                interfaces_json, recorded_at)
            VALUES(?,?,?,?,?,?)
            """,
            (int(tenant_id), int(router_id), str(reported_at or "")[:40],
             uptime_seconds, payload, now),
        )
        sample_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO router_metric_state(
                tenant_id, router_id, last_push_at, last_sample_id)
            VALUES(?,?,?,?)
            ON CONFLICT(tenant_id, router_id) DO UPDATE SET
                last_push_at=excluded.last_push_at,
                last_sample_id=excluded.last_sample_id
            """,
            (int(tenant_id), int(router_id), now, sample_id),
        )
    return int(sample_id)


def latest_two(tenant_id: int, router_id: int) -> list[dict]:
    """Most-recent two samples (newest first) — for Δ rate computation."""
    cur = db().execute(
        """
        SELECT * FROM router_metric_samples
        WHERE tenant_id=? AND router_id=?
        ORDER BY id DESC LIMIT 2
        """,
        (int(tenant_id), int(router_id)),
    )
    return [_row(r) for r in cur.fetchall()]


def samples_since(tenant_id: int, router_id: int, since_iso: str,
                  limit: int = 2000) -> list[dict]:
    """Samples recorded at/after `since_iso` (oldest first) — for windowed
    usage accumulation (sum of positive per-interface byte deltas)."""
    cur = db().execute(
        """
        SELECT * FROM router_metric_samples
        WHERE tenant_id=? AND router_id=? AND recorded_at >= ?
        ORDER BY id ASC LIMIT ?
        """,
        (int(tenant_id), int(router_id), str(since_iso), limit),
    )
    return [_row(r) for r in cur.fetchall()]


def last_push_map(tenant_id: int) -> dict[int, str]:
    """{router_id: last_push_at_iso} for every router that ever pushed."""
    cur = db().execute(
        "SELECT router_id, last_push_at FROM router_metric_state WHERE tenant_id=?",
        (int(tenant_id),),
    )
    return {int(r["router_id"]): (r["last_push_at"] or "") for r in cur.fetchall()}
