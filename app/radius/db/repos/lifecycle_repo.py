"""Repository helpers for lifecycle retention and automatic archiving."""
from __future__ import annotations

from typing import Any

from ..connection import db, transaction
from ..helpers import json_dump, now_iso, row_to_dict


def list_policies(tenant_id: int, *, entity_type: str = "", enabled: bool | None = None) -> list[dict]:
    sql = "SELECT * FROM lifecycle_policies WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if entity_type:
        sql += " AND entity_type = ?"
        vals.append(entity_type)
    if enabled is not None:
        sql += " AND enabled = ?"
        vals.append(1 if enabled else 0)
    sql += " ORDER BY enabled DESC, entity_type, id DESC"
    return [row_to_dict(row) for row in db().execute(sql, vals).fetchall()]


def get_policy(tenant_id: int, policy_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM lifecycle_policies WHERE tenant_id = ? AND id = ?",
        (tenant_id, policy_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def create_policy(tenant_id: int, *, entity_type: str, trigger_type: str,
                  delay_value: int, delay_unit: str, action: str,
                  retention_value: int, retention_unit: str, enabled: bool,
                  actor: str = "") -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO lifecycle_policies(
                tenant_id, entity_type, trigger_type, delay_value, delay_unit,
                action, retention_value, retention_unit, enabled,
                created_by, updated_by, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, entity_type, trigger_type, delay_value, delay_unit,
                action, retention_value, retention_unit, 1 if enabled else 0,
                actor, actor, now, now,
            ),
        )
        policy_id = int(cur.lastrowid)
    return get_policy(tenant_id, policy_id) or {}


def update_policy(tenant_id: int, policy_id: int, updates: dict[str, Any], *, actor: str = "") -> dict | None:
    allowed = {
        "entity_type",
        "trigger_type",
        "delay_value",
        "delay_unit",
        "action",
        "retention_value",
        "retention_unit",
        "enabled",
    }
    changes = {key: value for key, value in updates.items() if key in allowed}
    if not changes:
        return get_policy(tenant_id, policy_id)
    changes["updated_by"] = actor
    changes["updated_at"] = now_iso()
    parts = ", ".join(f"{key} = ?" for key in changes)
    vals = list(changes.values()) + [tenant_id, policy_id]
    with transaction() as conn:
        conn.execute(
            f"UPDATE lifecycle_policies SET {parts} WHERE tenant_id = ? AND id = ?",
            vals,
        )
    return get_policy(tenant_id, policy_id)


def disable_policy(tenant_id: int, policy_id: int, *, actor: str = "") -> dict | None:
    return update_policy(tenant_id, policy_id, {"enabled": 0}, actor=actor)


def due_cards(tenant_id: int, cutoff_iso: str, *, limit: int = 500) -> list[dict]:
    rows = db().execute(
        """
        SELECT c.*, COALESCE(NULLIF(b.package_name, ''), b.batch_code, '') AS batch_name,
               b.original_count AS batch_original_count
        FROM cards c
        LEFT JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        WHERE c.tenant_id = ?
          AND c.deleted_at IS NULL
          AND c.expire_at IS NOT NULL
          AND c.expire_at <> ''
          AND c.expire_at <= ?
        ORDER BY c.expire_at ASC, c.id ASC
        LIMIT ?
        """,
        (tenant_id, cutoff_iso, limit),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def due_subscribers(tenant_id: int, cutoff_iso: str, *, limit: int = 500) -> list[dict]:
    rows = db().execute(
        """
        SELECT *
        FROM subscribers
        WHERE tenant_id = ?
          AND deleted_at IS NULL
          AND expire_at IS NOT NULL
          AND expire_at <> ''
          AND expire_at <= ?
        ORDER BY expire_at ASC, id ASC
        LIMIT ?
        """,
        (tenant_id, cutoff_iso, limit),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def pending_cards_by_batch(tenant_id: int, cutoff_iso: str) -> list[dict]:
    rows = db().execute(
        """
        SELECT
            c.batch_id,
            COALESCE(NULLIF(b.package_name, ''), b.batch_code, '') AS batch_name,
            COALESCE(NULLIF(b.original_count, 0), NULLIF(b.count, 0), NULLIF(b.generated, 0), 0) AS original_count,
            COUNT(*) AS pending_archive_count
        FROM cards c
        LEFT JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id
        WHERE c.tenant_id = ?
          AND c.deleted_at IS NULL
          AND c.expire_at IS NOT NULL
          AND c.expire_at <> ''
          AND c.expire_at <= ?
        GROUP BY c.batch_id, b.package_name, b.batch_code, b.original_count, b.count, b.generated
        ORDER BY pending_archive_count DESC, batch_name
        """,
        (tenant_id, cutoff_iso),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def archive_card(conn, *, tenant_id: int, card_id: int, policy_id: int,
                 actor: str, reason: str, retention_expires_at: str) -> bool:
    now = now_iso()
    cur = conn.execute(
        """
        UPDATE cards
        SET deleted_at = ?, deleted_by = ?, delete_reason = ?, revoked = 1,
            archive_source = 'auto', archive_policy_id = ?,
            retention_expires_at = ?, auto_archive_at = ?
        WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
        """,
        (now, actor, reason, policy_id, retention_expires_at, now, tenant_id, card_id),
    )
    return cur.rowcount > 0


def archive_subscriber(conn, *, tenant_id: int, subscriber_id: int, policy_id: int,
                       actor: str, reason: str, retention_expires_at: str) -> bool:
    now = now_iso()
    cur = conn.execute(
        """
        UPDATE subscribers
        SET deleted_at = ?, deleted_by = ?, delete_reason = ?, status = 'disabled',
            updated_at = ?, archive_source = 'auto', archive_policy_id = ?,
            retention_expires_at = ?, auto_archive_at = ?
        WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL
        """,
        (now, actor, reason, now, policy_id, retention_expires_at, now, tenant_id, subscriber_id),
    )
    return cur.rowcount > 0


def record_event(conn, *, tenant_id: int, policy_id: int | None, entity_type: str,
                 entity_id: int | None, action: str, scheduled_for: str,
                 executed_at: str, status: str, reason: str,
                 snapshot: dict[str, Any] | None = None, error: str = "") -> int:
    cur = conn.execute(
        """
        INSERT INTO lifecycle_events(
            tenant_id, policy_id, entity_type, entity_id, action,
            scheduled_for, executed_at, status, reason,
            snapshot_json, error, created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            tenant_id, policy_id, entity_type, entity_id, action,
            scheduled_for, executed_at, status, reason,
            json_dump(snapshot or {}), error, now_iso(),
        ),
    )
    return int(cur.lastrowid)


def recent_events(tenant_id: int, *, limit: int = 100) -> list[dict]:
    rows = db().execute(
        """
        SELECT *
        FROM lifecycle_events
        WHERE tenant_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (tenant_id, limit),
    ).fetchall()
    return [row_to_dict(row) for row in rows]
