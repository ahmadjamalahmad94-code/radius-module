"""Operational foundation repositories for distributors and ISP workflows."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, row_to_dict


def _row(row) -> dict:
    return row_to_dict(row) if row else {}


def _json(value: Any, default: Any) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        parsed = json_load(value, default=None)
        if parsed is not None:
            return value
    return json_dump(value)


def _hydrate_json_fields(item: dict, *fields: str) -> dict:
    for field in fields:
        raw = item.get(field)
        item[field] = json_load(raw, default=[] if field.endswith("permissions_json") else {})
    return item


def create_distributor(tenant_id: int, data: dict, *, actor: str) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO distributors(
                tenant_id, admin_id, name, display_name, email, phone, status,
                permissions_json, scope_json, balance, credit_limit, debt_balance,
                created_by, notes, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id,
                data.get("admin_id"),
                data["name"],
                data.get("display_name") or data["name"],
                data.get("email") or "",
                data.get("phone") or "",
                data.get("status") or "active",
                _json(data.get("permissions"), []),
                _json(data.get("scope"), {}),
                float(data.get("balance") or 0),
                float(data.get("credit_limit") or 0),
                float(data.get("debt_balance") or 0),
                actor,
                data.get("notes") or "",
                _json(data.get("metadata"), {}),
                now,
            ),
        )
        distributor_id = cur.lastrowid
    return get_distributor(tenant_id, distributor_id) or {}


def list_distributors(tenant_id: int, *, status: Optional[str] = None,
                      limit: int = 200, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM distributors WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    rows = db().execute(sql, vals).fetchall()
    return [
        _hydrate_json_fields(_row(r), "permissions_json", "scope_json", "metadata_json")
        for r in rows
    ]


def get_distributor(tenant_id: int, distributor_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM distributors WHERE tenant_id = ? AND id = ?",
        (tenant_id, distributor_id),
    ).fetchone()
    if not row:
        return None
    return _hydrate_json_fields(_row(row), "permissions_json", "scope_json", "metadata_json")


def get_distributor_by_admin(tenant_id: int, admin_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM distributors WHERE tenant_id = ? AND admin_id = ? AND status = 'active'",
        (tenant_id, admin_id),
    ).fetchone()
    if not row:
        return None
    return _hydrate_json_fields(_row(row), "permissions_json", "scope_json", "metadata_json")


def assign_batch(tenant_id: int, *, distributor_id: int, batch_id: int,
                 actor: str, notes: str = "") -> dict:
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO card_batch_assignments(
                tenant_id, batch_id, distributor_id, assigned_by, status, notes, assigned_at
            )
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, batch_id) DO UPDATE SET
                distributor_id=excluded.distributor_id,
                assigned_by=excluded.assigned_by,
                status='assigned',
                notes=excluded.notes,
                assigned_at=excluded.assigned_at
            """,
            (tenant_id, batch_id, distributor_id, actor, "assigned", notes, now),
        )
        conn.execute(
            """
            UPDATE card_batches
            SET distributor_id = ?, assigned_to = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (distributor_id, str(distributor_id), tenant_id, batch_id),
        )
    return get_assignment(tenant_id, batch_id) or {}


def batch_assigned_to_distributor(tenant_id: int, batch_id: int,
                                  distributor_id: int) -> bool:
    row = db().execute(
        """
        SELECT 1
        FROM card_batch_assignments
        WHERE tenant_id = ? AND batch_id = ? AND distributor_id = ?
          AND status = 'assigned'
        LIMIT 1
        """,
        (tenant_id, batch_id, distributor_id),
    ).fetchone()
    return row is not None


def assigned_batch_ids(tenant_id: int, distributor_id: int) -> list[int]:
    rows = db().execute(
        """
        SELECT batch_id
        FROM card_batch_assignments
        WHERE tenant_id = ? AND distributor_id = ? AND status = 'assigned'
        """,
        (tenant_id, distributor_id),
    ).fetchall()
    return [int(r["batch_id"]) for r in rows]


def subscriber_in_distributor_scope(tenant_id: int, distributor_id: int, *,
                                    username: str = "",
                                    subscriber_id: int | None = None) -> bool:
    sql = """
        SELECT s.id
        FROM subscribers s
        JOIN card_batch_assignments a
          ON a.tenant_id = s.tenant_id
         AND a.batch_id = s.card_batch_id
         AND a.status = 'assigned'
        WHERE s.tenant_id = ? AND a.distributor_id = ?
          AND s.deleted_at IS NULL
    """
    vals: list[Any] = [tenant_id, distributor_id]
    if subscriber_id:
        sql += " AND s.id = ?"
        vals.append(subscriber_id)
    elif username:
        sql += " AND s.username = ?"
        vals.append(username)
    else:
        return False
    sql += " LIMIT 1"
    return db().execute(sql, vals).fetchone() is not None


def get_assignment(tenant_id: int, batch_id: int) -> Optional[dict]:
    row = db().execute(
        """
        SELECT a.*, d.name AS distributor_name, d.display_name AS distributor_display_name
        FROM card_batch_assignments a
        JOIN distributors d
          ON d.tenant_id = a.tenant_id AND d.id = a.distributor_id
        WHERE a.tenant_id = ? AND a.batch_id = ?
        """,
        (tenant_id, batch_id),
    ).fetchone()
    return _row(row) if row else None


def list_assigned_batches(tenant_id: int, distributor_id: int, *,
                          limit: int = 200, offset: int = 0) -> list[dict]:
    rows = db().execute(
        """
        SELECT
            b.id, b.batch_code, b.package_name, b.plan_id, b.count,
            b.generated, b.used, b.status, b.created_at, b.expire_at,
            b.distributor_id, b.assigned_to,
            a.assigned_at, a.assigned_by, a.notes AS assignment_notes
        FROM card_batch_assignments a
        JOIN card_batches b
          ON b.tenant_id = a.tenant_id AND b.id = a.batch_id
        WHERE a.tenant_id = ? AND a.distributor_id = ? AND a.status = 'assigned'
          AND b.deleted_at IS NULL
        ORDER BY a.assigned_at DESC, b.id DESC
        LIMIT ? OFFSET ?
        """,
        (tenant_id, distributor_id, limit, offset),
    ).fetchall()
    return [_row(r) for r in rows]


def distributor_summary(tenant_id: int, distributor_id: int) -> Optional[dict]:
    distributor = get_distributor(tenant_id, distributor_id)
    if not distributor:
        return None
    batch_count = db().execute(
        """
        SELECT COUNT(*) AS c
        FROM card_batch_assignments
        WHERE tenant_id = ? AND distributor_id = ? AND status = 'assigned'
        """,
        (tenant_id, distributor_id),
    ).fetchone()["c"]
    ledger = db().execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END), 0) AS debit,
            COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END), 0) AS credit,
            COUNT(*) AS entries
        FROM distributor_ledger_entries
        WHERE tenant_id = ? AND distributor_id = ? AND status = 'posted'
        """,
        (tenant_id, distributor_id),
    ).fetchone()
    return {
        "distributor": distributor,
        "assigned_batches": int(batch_count or 0),
        "ledger": {
            "debit": float(ledger["debit"] or 0),
            "credit": float(ledger["credit"] or 0),
            "entries": int(ledger["entries"] or 0),
        },
        "balance": float(distributor.get("balance") or 0),
        "debt_balance": float(distributor.get("debt_balance") or 0),
        "credit_limit": float(distributor.get("credit_limit") or 0),
    }


def post_distributor_ledger(tenant_id: int, distributor_id: int, *,
                            entry_type: str, direction: str, amount: float,
                            currency: str, actor: str, notes: str = "",
                            related_type: str = "", related_id: int | None = None,
                            metadata: Optional[dict] = None) -> dict:
    now = now_iso()
    amount = float(amount)
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO distributor_ledger_entries(
                tenant_id, distributor_id, entry_type, direction, amount, currency,
                related_type, related_id, status, notes, created_by, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, distributor_id, entry_type, direction, amount, currency,
                related_type, related_id, "posted", notes, actor, _json(metadata, {}), now,
            ),
        )
        if direction == "debit":
            conn.execute(
                "UPDATE distributors SET debt_balance = debt_balance + ?, updated_at = ? "
                "WHERE tenant_id = ? AND id = ?",
                (amount, now, tenant_id, distributor_id),
            )
        else:
            conn.execute(
                """
                UPDATE distributors
                SET balance = balance + ?, debt_balance = MAX(debt_balance - ?, 0), updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (amount, amount, now, tenant_id, distributor_id),
            )
        entry_id = cur.lastrowid
    row = db().execute(
        "SELECT * FROM distributor_ledger_entries WHERE tenant_id = ? AND id = ?",
        (tenant_id, entry_id),
    ).fetchone()
    return _hydrate_json_fields(_row(row), "metadata_json")


def create_bandwidth_schedule(tenant_id: int, data: dict, *, actor: str) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO bandwidth_schedules(
                tenant_id, plan_id, target_type, subscriber_username, card_batch_id,
                subscriber_group_id,
                priority, name, starts_at_time, ends_at_time, days_csv,
                speed_down_kbps, speed_up_kbps, cir_down_kbps, cir_up_kbps,
                restore_mode, enabled, created_by, notes, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, data["plan_id"], data.get("target_type") or "plan",
                data.get("subscriber_username") or "", data.get("card_batch_id"),
                data.get("subscriber_group_id"),
                int(data.get("priority") or 100), data["name"], data["starts_at_time"],
                data["ends_at_time"], data.get("days_csv") or "",
                data.get("speed_down_kbps") or 0,
                data.get("speed_up_kbps") or 0, data.get("cir_down_kbps") or 0,
                data.get("cir_up_kbps") or 0,
                data.get("restore_mode") or "profile_default",
                1 if data.get("enabled", True) else 0,
                actor, data.get("notes") or "", _json(data.get("metadata"), {}), now,
            ),
        )
        schedule_id = cur.lastrowid
    return get_bandwidth_schedule(tenant_id, schedule_id) or {}


def list_bandwidth_schedules(tenant_id: int, *, plan_id: int | None = None,
                             target_type: str | None = None,
                             subscriber_username: str | None = None,
                             card_batch_id: int | None = None,
                             subscriber_group_id: int | None = None,
                             limit: int = 200, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM bandwidth_schedules WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if target_type:
        sql += " AND target_type = ?"
        vals.append(target_type)
    if plan_id is not None:
        sql += " AND plan_id = ?"
        vals.append(plan_id)
    if subscriber_username:
        sql += " AND subscriber_username = ?"
        vals.append(subscriber_username)
    if card_batch_id is not None:
        sql += " AND card_batch_id = ?"
        vals.append(card_batch_id)
    if subscriber_group_id is not None:
        sql += " AND subscriber_group_id = ?"
        vals.append(subscriber_group_id)
    sql += " ORDER BY target_type, plan_id, subscriber_username, card_batch_id, priority, starts_at_time LIMIT ? OFFSET ?"
    vals += [limit, offset]
    return [
        _hydrate_json_fields(_row(r), "metadata_json")
        for r in db().execute(sql, vals).fetchall()
    ]


def get_bandwidth_schedule(tenant_id: int, schedule_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM bandwidth_schedules WHERE tenant_id = ? AND id = ?",
        (tenant_id, schedule_id),
    ).fetchone()
    if not row:
        return None
    return _hydrate_json_fields(_row(row), "metadata_json")


def update_bandwidth_schedule(tenant_id: int, schedule_id: int, data: dict) -> dict:
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            """
            UPDATE bandwidth_schedules
            SET name = ?, starts_at_time = ?, ends_at_time = ?, days_csv = ?,
                speed_down_kbps = ?, speed_up_kbps = ?,
                cir_down_kbps = ?, cir_up_kbps = ?,
                restore_mode = ?, priority = ?, enabled = ?,
                notes = ?, updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                data["name"], data["starts_at_time"], data["ends_at_time"],
                data.get("days_csv") or "",
                data.get("speed_down_kbps") or 0, data.get("speed_up_kbps") or 0,
                data.get("cir_down_kbps") or 0, data.get("cir_up_kbps") or 0,
                data.get("restore_mode") or "profile_default",
                int(data.get("priority") or 100),
                1 if data.get("enabled", True) else 0,
                data.get("notes") or "", now, tenant_id, schedule_id,
            ),
        )
    return get_bandwidth_schedule(tenant_id, schedule_id) or {}


def set_bandwidth_schedule_enabled(tenant_id: int, schedule_id: int, enabled: bool) -> dict:
    with transaction() as conn:
        conn.execute(
            """
            UPDATE bandwidth_schedules
            SET enabled = ?, updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (1 if enabled else 0, now_iso(), tenant_id, schedule_id),
        )
    return get_bandwidth_schedule(tenant_id, schedule_id) or {}


def set_bandwidth_schedules_enabled_for_target(
    tenant_id: int,
    *,
    target_type: str,
    enabled: bool,
    plan_id: int | None = None,
    subscriber_username: str = "",
    card_batch_id: int | None = None,
    subscriber_group_id: int | None = None,
) -> int:
    sql = "UPDATE bandwidth_schedules SET enabled = ?, updated_at = ? WHERE tenant_id = ? AND target_type = ?"
    vals: list[Any] = [1 if enabled else 0, now_iso(), tenant_id, target_type]
    if target_type == "subscriber":
        sql += " AND subscriber_username = ?"
        vals.append(subscriber_username or "")
    elif target_type == "card_batch":
        sql += " AND card_batch_id = ?"
        vals.append(card_batch_id)
    elif target_type == "subscriber_group":
        sql += " AND subscriber_group_id = ?"
        vals.append(subscriber_group_id)
    else:
        sql += " AND plan_id = ?"
        vals.append(plan_id)
    with transaction() as conn:
        cur = conn.execute(sql, vals)
        return int(cur.rowcount or 0)


def delete_bandwidth_schedule(tenant_id: int, schedule_id: int) -> bool:
    with transaction() as conn:
        conn.execute(
            "DELETE FROM bandwidth_schedule_logs WHERE tenant_id = ? AND schedule_id = ?",
            (tenant_id, schedule_id),
        )
        cur = conn.execute(
            "DELETE FROM bandwidth_schedules WHERE tenant_id = ? AND id = ?",
            (tenant_id, schedule_id),
        )
        return bool(cur.rowcount)


def _time_minutes(value: str) -> int:
    hour, minute = (value or "00:00").split(":", 1)
    return int(hour) * 60 + int(minute)


def _in_time_window(now_hm: str, start_hm: str, end_hm: str) -> bool:
    current = _time_minutes(now_hm)
    start = _time_minutes(start_hm)
    end = _time_minutes(end_hm)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _active_rule_for_target(
    tenant_id: int,
    *,
    target_type: str,
    now_hm: str,
    plan_id: int | None = None,
    subscriber_username: str | None = None,
    card_batch_id: int | None = None,
) -> Optional[dict]:
    sql = """
        SELECT * FROM bandwidth_schedules
        WHERE tenant_id = ? AND target_type = ? AND enabled = 1
    """
    vals: list[Any] = [tenant_id, target_type]
    if target_type == "subscriber":
        sql += " AND subscriber_username = ?"
        vals.append(subscriber_username or "")
    elif target_type == "card_batch":
        sql += " AND card_batch_id = ?"
        vals.append(card_batch_id)
    else:
        sql += " AND plan_id = ?"
        vals.append(plan_id)
    sql += " ORDER BY priority ASC, id DESC"
    for row in db().execute(sql, vals).fetchall():
        item = _hydrate_json_fields(_row(row), "metadata_json")
        if _in_time_window(now_hm, item["starts_at_time"], item["ends_at_time"]):
            return item
    return None


def resolve_effective_bandwidth_schedule(
    tenant_id: int,
    *,
    subscriber_username: str = "",
    card_batch_id: int | None = None,
    plan_id: int | None = None,
    at: datetime | None = None,
) -> Optional[dict]:
    """Return the active speed rule using subscriber/card-batch/plan priority."""
    now_hm = (at or datetime.utcnow()).strftime("%H:%M")
    if subscriber_username:
        rule = _active_rule_for_target(
            tenant_id,
            target_type="subscriber",
            subscriber_username=subscriber_username,
            now_hm=now_hm,
        )
        if rule:
            return rule
    if card_batch_id is not None:
        rule = _active_rule_for_target(
            tenant_id,
            target_type="card_batch",
            card_batch_id=card_batch_id,
            now_hm=now_hm,
        )
        if rule:
            return rule
    if plan_id is not None:
        return _active_rule_for_target(
            tenant_id,
            target_type="plan",
            plan_id=plan_id,
            now_hm=now_hm,
        )
    return None


def usernames_for_bandwidth_schedule(
    tenant_id: int,
    schedule: dict,
    *,
    limit: int = 1000,
) -> list[str]:
    """Return usernames affected by a schedule for live CoA application.

    This is deliberately bounded. The caller can inspect the returned count and
    run the operation in slices later if a very large ISP deployment needs it.
    """
    target_type = (schedule.get("target_type") or "plan").strip().lower()
    usernames: list[str] = []
    if target_type == "subscriber":
        username = (schedule.get("subscriber_username") or "").strip()
        return [username] if username else []
    if target_type == "card_batch":
        batch_id = schedule.get("card_batch_id")
        rows = db().execute(
            """
            SELECT username
              FROM cards
             WHERE tenant_id = ? AND batch_id = ?
               AND COALESCE(revoked, 0) = 0
             ORDER BY id
             LIMIT ?
            """,
            (tenant_id, batch_id, limit),
        ).fetchall()
        return [str(row["username"]) for row in rows if row["username"]]
    plan_id = schedule.get("plan_id")
    rows = db().execute(
        """
        SELECT username
          FROM subscribers
         WHERE tenant_id = ? AND plan_id = ?
           AND COALESCE(deleted_at, '') = ''
         ORDER BY id
         LIMIT ?
        """,
        (tenant_id, plan_id, limit),
    ).fetchall()
    usernames.extend(str(row["username"]) for row in rows if row["username"])
    remaining = max(0, limit - len(usernames))
    if remaining:
        rows = db().execute(
            """
            SELECT username
              FROM cards
             WHERE tenant_id = ? AND plan_id = ?
               AND COALESCE(revoked, 0) = 0
             ORDER BY id
             LIMIT ?
            """,
            (tenant_id, plan_id, remaining),
        ).fetchall()
        usernames.extend(str(row["username"]) for row in rows if row["username"])
    return usernames


def log_bandwidth_schedule(tenant_id: int, schedule_id: int, *,
                           action: str, status: str, message: str = "") -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO bandwidth_schedule_logs(
                tenant_id, schedule_id, action, status, message, created_at
            )
            VALUES(?,?,?,?,?,?)
            """,
            (tenant_id, schedule_id, action, status, message, now_iso()),
        )
        log_id = cur.lastrowid
    row = db().execute(
        "SELECT * FROM bandwidth_schedule_logs WHERE tenant_id = ? AND id = ?",
        (tenant_id, log_id),
    ).fetchone()
    return _row(row)


def create_print_template(tenant_id: int, data: dict, *, actor: str) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO card_print_templates(
                tenant_id, name, orientation, cards_per_row, cards_per_column,
                page_size, show_qr, username_x, username_y, password_x, password_y,
                qr_x, qr_y, font_size, color, layout_json, created_by, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, data["name"], data.get("orientation") or "portrait",
                data.get("cards_per_row") or 2, data.get("cards_per_column") or 5,
                data.get("page_size") or "A4", 1 if data.get("show_qr", True) else 0,
                float(data.get("username_x") or 0), float(data.get("username_y") or 0),
                float(data.get("password_x") or 0), float(data.get("password_y") or 0),
                float(data.get("qr_x") or 0), float(data.get("qr_y") or 0),
                int(data.get("font_size") or 12), data.get("color") or "#1f2937",
                _json(data.get("layout"), {}), actor, now,
            ),
        )
        template_id = cur.lastrowid
    return get_print_template(tenant_id, template_id) or {}


def update_print_template(tenant_id: int, template_id: int, data: dict, *, actor: str) -> dict:
    current = get_print_template(tenant_id, template_id)
    if not current:
        return {}
    merged = {**current, **data}
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            """
            UPDATE card_print_templates
            SET name = ?, orientation = ?, cards_per_row = ?, cards_per_column = ?,
                page_size = ?, show_qr = ?, username_x = ?, username_y = ?,
                password_x = ?, password_y = ?, qr_x = ?, qr_y = ?,
                font_size = ?, color = ?, layout_json = ?, updated_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                merged["name"], merged.get("orientation") or "portrait",
                merged.get("cards_per_row") or 2, merged.get("cards_per_column") or 5,
                merged.get("page_size") or "A4", 1 if merged.get("show_qr", True) else 0,
                float(merged.get("username_x") or 0), float(merged.get("username_y") or 0),
                float(merged.get("password_x") or 0), float(merged.get("password_y") or 0),
                float(merged.get("qr_x") or 0), float(merged.get("qr_y") or 0),
                int(merged.get("font_size") or 12), merged.get("color") or "#1f2937",
                _json(merged.get("layout") or merged.get("layout_json"), {}), now,
                tenant_id, template_id,
            ),
        )
    return get_print_template(tenant_id, template_id) or {}


def list_print_templates(tenant_id: int, *, limit: int = 200, offset: int = 0) -> list[dict]:
    rows = db().execute(
        """
        SELECT * FROM card_print_templates
        WHERE tenant_id = ?
        ORDER BY id DESC LIMIT ? OFFSET ?
        """,
        (tenant_id, limit, offset),
    ).fetchall()
    return [_hydrate_json_fields(_row(r), "layout_json") for r in rows]


def get_print_template(tenant_id: int, template_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM card_print_templates WHERE tenant_id = ? AND id = ?",
        (tenant_id, template_id),
    ).fetchone()
    if not row:
        return None
    return _hydrate_json_fields(_row(row), "layout_json")


def delete_print_template(tenant_id: int, template_id: int) -> bool:
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM card_print_templates WHERE tenant_id = ? AND id = ?",
            (tenant_id, template_id),
        )
        return cur.rowcount > 0


def create_print_job(
    tenant_id: int,
    *,
    template_id: int | None,
    batch_id: int | None,
    export_type: str,
    status: str,
    card_count: int = 0,
    file_name: str = "",
    message: str = "",
    metadata: dict | None = None,
    actor: str = "",
) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO print_jobs(
                tenant_id, template_id, batch_id, export_type, status,
                card_count, file_name, message, metadata_json, created_by,
                created_at, completed_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, template_id, batch_id, export_type, status,
                int(card_count or 0), file_name, message, _json(metadata or {}, {}),
                actor, now, now if status in {"success", "failed"} else None,
            ),
        )
        job_id = cur.lastrowid
    return get_print_job(tenant_id, job_id) or {}


def finish_print_job(
    tenant_id: int,
    job_id: int,
    *,
    status: str,
    card_count: int,
    file_name: str,
    message: str = "",
    metadata: dict | None = None,
) -> dict:
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            """
            UPDATE print_jobs
            SET status = ?, card_count = ?, file_name = ?, message = ?,
                metadata_json = ?, completed_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                status, int(card_count or 0), file_name, message,
                _json(metadata or {}, {}), now, tenant_id, job_id,
            ),
        )
    return get_print_job(tenant_id, job_id) or {}


def update_print_job(
    tenant_id: int,
    job_id: int,
    *,
    status: str | None = None,
    card_count: int | None = None,
    file_name: str | None = None,
    message: str | None = None,
    metadata: dict | None = None,
    completed: bool = False,
) -> dict:
    current = get_print_job(tenant_id, job_id)
    if not current:
        return {}
    next_metadata = current.get("metadata_json") if isinstance(current.get("metadata_json"), dict) else {}
    if metadata:
        next_metadata.update(metadata)
    now = now_iso()
    with transaction() as conn:
        conn.execute(
            """
            UPDATE print_jobs
            SET status = ?,
                card_count = ?,
                file_name = ?,
                message = ?,
                metadata_json = ?,
                completed_at = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (
                status if status is not None else current.get("status"),
                int(card_count if card_count is not None else current.get("card_count") or 0),
                file_name if file_name is not None else current.get("file_name") or "",
                message if message is not None else current.get("message") or "",
                _json(next_metadata, {}),
                now if completed else current.get("completed_at"),
                tenant_id,
                job_id,
            ),
        )
    return get_print_job(tenant_id, job_id) or {}


def get_print_job(tenant_id: int, job_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM print_jobs WHERE tenant_id = ? AND id = ?",
        (tenant_id, job_id),
    ).fetchone()
    if not row:
        return None
    return _hydrate_json_fields(_row(row), "metadata_json")


def list_print_jobs(tenant_id: int, *, limit: int = 50, offset: int = 0) -> list[dict]:
    rows = db().execute(
        """
        SELECT j.*, t.name AS template_name, b.batch_code, b.package_name
        FROM print_jobs j
        LEFT JOIN card_print_templates t
          ON t.tenant_id = j.tenant_id AND t.id = j.template_id
        LEFT JOIN card_batches b
          ON b.tenant_id = j.tenant_id AND b.id = j.batch_id
        WHERE j.tenant_id = ?
        ORDER BY j.id DESC
        LIMIT ? OFFSET ?
        """,
        (tenant_id, limit, offset),
    ).fetchall()
    return [_hydrate_json_fields(_row(r), "metadata_json") for r in rows]


def ensure_backup_job(tenant_id: int, *, actor: str = "system") -> dict:
    row = db().execute(
        "SELECT * FROM backup_jobs WHERE tenant_id = ? AND name = ?",
        (tenant_id, "local-manual"),
    ).fetchone()
    if row:
        return _hydrate_json_fields(_row(row), "metadata_json")
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO backup_jobs(
                tenant_id, name, schedule, target, enabled, last_status,
                last_message, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, "local-manual", "manual", "local", 1, "never_run",
                "No local backup has been run yet.", _json({"created_by": actor}, {}), now,
            ),
        )
        job_id = cur.lastrowid
    row = db().execute("SELECT * FROM backup_jobs WHERE id = ?", (job_id,)).fetchone()
    return _hydrate_json_fields(_row(row), "metadata_json")


def record_backup_run(tenant_id: int, *, job_id: int | None, status: str,
                      path: str, message: str) -> dict:
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO backup_run_logs(tenant_id, job_id, status, path, message, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (tenant_id, job_id, status, path, message, now),
        )
        if job_id:
            conn.execute(
                """
                UPDATE backup_jobs
                SET last_status = ?, last_run_at = ?, last_message = ?, updated_at = ?
                WHERE tenant_id = ? AND id = ?
                """,
                (status, now, message, now, tenant_id, job_id),
            )
        log_id = cur.lastrowid
    row = db().execute(
        "SELECT * FROM backup_run_logs WHERE tenant_id = ? AND id = ?",
        (tenant_id, log_id),
    ).fetchone()
    return _row(row)


def backup_status(tenant_id: int) -> dict:
    job = ensure_backup_job(tenant_id)
    logs = db().execute(
        """
        SELECT * FROM backup_run_logs
        WHERE tenant_id = ?
        ORDER BY id DESC LIMIT 5
        """,
        (tenant_id,),
    ).fetchall()
    return {"job": job, "recent_runs": [_row(r) for r in logs]}
