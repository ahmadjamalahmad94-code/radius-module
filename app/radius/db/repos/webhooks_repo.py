"""Webhook subscriptions + deliveries repo."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ...core.types_saas import WebhookDelivery, WebhookSubscription
from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, parse_dt


# ─────────────── subscriptions ───────────────

def _sub_row(r) -> WebhookSubscription:
    return WebhookSubscription(
        id=r["id"], tenant_id=r["tenant_id"], target_url=r["target_url"],
        secret=r["secret"] or "",
        enabled_events=tuple(json_load(r["enabled_events_json"], default=[])),
        enabled=bool(r["enabled"]),
        created_at=parse_dt(r["created_at"]),
    )


def list_subs(tenant_id: int) -> list[WebhookSubscription]:
    cur = db().execute(
        "SELECT * FROM webhook_subscriptions WHERE tenant_id = ? ORDER BY id",
        (tenant_id,))
    return [_sub_row(r) for r in cur.fetchall()]


def get_sub(tenant_id: int, sid: int) -> Optional[WebhookSubscription]:
    row = db().execute(
        "SELECT * FROM webhook_subscriptions WHERE tenant_id = ? AND id = ?",
        (tenant_id, sid)).fetchone()
    return _sub_row(row) if row else None


def upsert_sub(s: WebhookSubscription) -> WebhookSubscription:
    now = now_iso()
    with transaction() as conn:
        if s.id is None:
            cur = conn.execute("""
                INSERT INTO webhook_subscriptions(tenant_id, target_url, secret, enabled_events_json, enabled, created_at)
                VALUES(?,?,?,?,?,?)
            """, (s.tenant_id, s.target_url, s.secret,
                  json_dump(list(s.enabled_events)), int(s.enabled), now))
            new_id = cur.lastrowid
        else:
            conn.execute("""
                UPDATE webhook_subscriptions
                SET target_url=?, secret=?, enabled_events_json=?, enabled=?
                WHERE tenant_id = ? AND id = ?
            """, (s.target_url, s.secret, json_dump(list(s.enabled_events)),
                  int(s.enabled), s.tenant_id, s.id))
            new_id = s.id
    return get_sub(s.tenant_id, new_id)


def delete_sub(tenant_id: int, sid: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM webhook_subscriptions WHERE tenant_id = ? AND id = ?",
                     (tenant_id, sid))


# ─────────────── deliveries ───────────────

def _del_row(r) -> WebhookDelivery:
    return WebhookDelivery(
        id=r["id"], tenant_id=r["tenant_id"], subscription_id=r["subscription_id"],
        event=r["event"], event_id=r["event_id"],
        payload=json_load(r["payload_json"], default={}),
        status=r["status"], attempts=r["attempts"],
        last_status_code=r["last_status_code"] or 0,
        last_response_excerpt=r["last_response_excerpt"] or "",
        next_attempt_at=parse_dt(r["next_attempt_at"]),
        created_at=parse_dt(r["created_at"]),
    )


def enqueue(tenant_id: int, subscription_id: int, *, event: str, event_id: str,
            payload: dict) -> int:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO webhook_deliveries(tenant_id, subscription_id, event, event_id,
                payload_json, status, attempts, next_attempt_at, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (tenant_id, subscription_id, event, event_id,
              json_dump(payload), "queued", 0, now, now))
        return cur.lastrowid


def list_deliveries(tenant_id: int, *, status: Optional[str] = None,
                     limit: int = 200) -> list[WebhookDelivery]:
    sql = "SELECT * FROM webhook_deliveries WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"; vals.append(status)
    sql += " ORDER BY id DESC LIMIT ?"; vals.append(limit)
    return [_del_row(r) for r in db().execute(sql, vals).fetchall()]


def pick_due(*, limit: int = 20) -> list[WebhookDelivery]:
    """يلتقط الـ deliveries التي حان وقتها."""
    cur = db().execute("""
        SELECT * FROM webhook_deliveries
        WHERE status IN ('queued','retrying') AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
        ORDER BY id LIMIT ?
    """, (now_iso(), limit))
    return [_del_row(r) for r in cur.fetchall()]


def mark_delivered(delivery_id: int, *, status_code: int) -> None:
    with transaction() as conn:
        conn.execute("""
            UPDATE webhook_deliveries
            SET status='delivered', attempts = attempts + 1, last_status_code=?
            WHERE id = ?
        """, (status_code, delivery_id))


def mark_failed(delivery_id: int, *, status_code: int, excerpt: str,
                 next_attempt_at: datetime, terminal: bool = False) -> None:
    new_status = "failed" if terminal else "retrying"
    with transaction() as conn:
        conn.execute("""
            UPDATE webhook_deliveries
            SET status=?, attempts = attempts + 1,
                last_status_code=?, last_response_excerpt=?, next_attempt_at=?
            WHERE id = ?
        """, (new_status, status_code, (excerpt or "")[:500],
              next_attempt_at.isoformat() + "Z" if isinstance(next_attempt_at, datetime) else next_attempt_at,
              delivery_id))
