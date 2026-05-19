"""Operational foundation repositories for distributors and ISP workflows."""
from __future__ import annotations

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
                tenant_id, plan_id, name, starts_at_time, ends_at_time,
                speed_down_kbps, speed_up_kbps, cir_down_kbps, cir_up_kbps,
                restore_mode, enabled, created_by, notes, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, data["plan_id"], data["name"], data["starts_at_time"],
                data["ends_at_time"], data.get("speed_down_kbps") or 0,
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
                             limit: int = 200, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM bandwidth_schedules WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if plan_id is not None:
        sql += " AND plan_id = ?"
        vals.append(plan_id)
    sql += " ORDER BY plan_id, starts_at_time LIMIT ? OFFSET ?"
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
