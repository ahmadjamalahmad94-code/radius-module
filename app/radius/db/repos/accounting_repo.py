"""Append-only accounting repository.

The ledger path intentionally has no delete helper. Voids and corrections are
stored as new rows so reports can reconstruct history.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import json_dump, json_load, now_iso, row_to_dict


def resolve_subscriber(tenant_id: int, *, subscriber_id: int | None = None,
                       username: str = "") -> Optional[dict]:
    if subscriber_id:
        row = db().execute(
            "SELECT * FROM subscribers WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
            (tenant_id, subscriber_id),
        ).fetchone()
    else:
        row = db().execute(
            "SELECT * FROM subscribers WHERE tenant_id = ? AND username = ? AND deleted_at IS NULL",
            (tenant_id, username),
        ).fetchone()
    return row_to_dict(row) if row else None


def resolve_plan(tenant_id: int, plan_id: int | None) -> Optional[dict]:
    if not plan_id:
        return None
    row = db().execute(
        "SELECT * FROM access_plans WHERE tenant_id = ? AND id = ? AND deleted_at IS NULL",
        (tenant_id, plan_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def create_ledger_entry(conn, *, tenant_id: int, entry_type: str, amount: float,
                        direction: str = "credit", currency: str = "JOD",
                        subscriber_id: int | None = None, username: str = "",
                        admin_id: int = 0, operator: str = "",
                        source_type: str = "", source_id: int | None = None,
                        related_type: str = "", related_id: int | None = None,
                        reversal_of_entry_id: int | None = None,
                        status: str = "posted", notes: str = "",
                        metadata: dict[str, Any] | None = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO accounting_ledger_entries(
            tenant_id, entry_type, direction, amount, currency,
            subscriber_id, username, admin_id, operator,
            source_type, source_id, related_type, related_id,
            reversal_of_entry_id, status, notes, metadata_json, created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            tenant_id, entry_type, direction, amount, currency,
            subscriber_id, username, admin_id, operator,
            source_type, source_id, related_type, related_id,
            reversal_of_entry_id, status, notes, json_dump(metadata or {}),
            now_iso(),
        ),
    )
    return cur.lastrowid


def list_ledger_entries(tenant_id: int, *, entry_type: str = "",
                        subscriber_id: int | None = None, limit: int = 100,
                        offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if entry_type:
        sql += " AND entry_type = ?"
        vals.append(entry_type)
    if subscriber_id:
        sql += " AND subscriber_id = ?"
        vals.append(subscriber_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def get_ledger_entry(tenant_id: int, entry_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM accounting_ledger_entries WHERE tenant_id = ? AND id = ?",
        (tenant_id, entry_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def create_payment(*, tenant_id: int, subscriber: dict, plan: dict | None,
                   amount: float, currency: str, method: str,
                   created_by: str, plan_price: float, custom_price: float | None,
                   discount_amount: float, discount_reason: str,
                   effective_price: float, earned_minutes: int,
                   rounding_mode: str, notes: str,
                   distributor_id: int | None = None,
                   metadata: dict[str, Any] | None = None) -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO payment_transactions(
                tenant_id, subscriber_id, username, plan_id, amount, currency,
                method, status, plan_price, custom_price, discount_amount,
                discount_reason, effective_price, earned_minutes, rounding_mode,
                created_by, notes, metadata_json, distributor_id, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, subscriber["id"], subscriber["username"],
                plan["id"] if plan else None, amount, currency, method,
                "posted", plan_price, custom_price, discount_amount,
                discount_reason, effective_price, earned_minutes,
                rounding_mode, created_by, notes, json_dump(metadata or {}),
                distributor_id,
                now_iso(),
            ),
        )
        payment_id = cur.lastrowid
        ledger_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="payment",
            amount=amount,
            direction="credit",
            currency=currency,
            subscriber_id=subscriber["id"],
            username=subscriber["username"],
            operator=created_by,
            source_type="payment",
            source_id=payment_id,
            notes=notes,
            metadata={
                "plan_id": plan["id"] if plan else None,
                "plan_price": plan_price,
                "custom_price": custom_price,
                "discount_amount": discount_amount,
                "discount_reason": discount_reason,
                "effective_price": effective_price,
                "earned_minutes": earned_minutes,
                "rounding_mode": rounding_mode,
                **(metadata or {}),
            },
        )
        conn.execute(
            "UPDATE payment_transactions SET ledger_entry_id = ? WHERE id = ?",
            (ledger_id, payment_id),
        )
    return get_payment(tenant_id, payment_id) or {}


def get_payment(tenant_id: int, payment_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM payment_transactions WHERE tenant_id = ? AND id = ?",
        (tenant_id, payment_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def void_payment(*, tenant_id: int, payment: dict, actor: str,
                 reason: str = "") -> Optional[dict]:
    ledger_id = payment.get("ledger_entry_id")
    if not ledger_id:
        return None
    original = get_ledger_entry(tenant_id, int(ledger_id))
    if not original:
        return None
    amount = -float(original["amount"] or 0)
    with transaction() as conn:
        void_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="void",
            amount=amount,
            direction="debit" if original["direction"] == "credit" else "credit",
            currency=original["currency"],
            subscriber_id=original["subscriber_id"],
            username=original["username"],
            operator=actor,
            source_type="payment_void",
            source_id=payment["id"],
            related_type="payment",
            related_id=payment["id"],
            reversal_of_entry_id=int(ledger_id),
            status="void",
            notes=reason,
            metadata={"voided_payment_id": payment["id"], "reason": reason},
        )
        conn.execute(
            """
            UPDATE payment_transactions
            SET status = 'voided'
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, payment["id"]),
        )
    return {
        "payment": get_payment(tenant_id, payment["id"]) or {},
        "entry": get_ledger_entry(tenant_id, void_id) or {},
    }


def list_payments(tenant_id: int, *, subscriber_id: int | None = None,
                  distributor_id: int | None = None,
                  limit: int = 100, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM payment_transactions WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if subscriber_id:
        sql += " AND subscriber_id = ?"
        vals.append(subscriber_id)
    if distributor_id:
        sql += " AND distributor_id = ?"
        vals.append(distributor_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def create_loan(*, tenant_id: int, subscriber: dict, duration_minutes: int,
                amount: float, currency: str, reason: str, created_by: str,
                starts_at: str, ends_at: str, max_limit_snapshot: int,
                metadata: dict[str, Any] | None = None) -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO loan_entries(
                tenant_id, subscriber_id, username, duration_minutes, amount,
                currency, reason, status, approval_status, starts_at, ends_at,
                max_limit_snapshot, created_by, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, subscriber["id"], subscriber["username"],
                duration_minutes, amount, currency, reason, "open",
                "not_required", starts_at, ends_at, max_limit_snapshot,
                created_by, json_dump(metadata or {}), now_iso(),
            ),
        )
        loan_id = cur.lastrowid
        ledger_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="loan",
            amount=amount,
            direction="debit",
            currency=currency,
            subscriber_id=subscriber["id"],
            username=subscriber["username"],
            operator=created_by,
            source_type="loan",
            source_id=loan_id,
            notes=reason,
            metadata={
                "duration_minutes": duration_minutes,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "max_limit_snapshot": max_limit_snapshot,
                **(metadata or {}),
            },
        )
        conn.execute(
            "UPDATE loan_entries SET ledger_entry_id = ? WHERE id = ?",
            (ledger_id, loan_id),
        )
    return get_loan(tenant_id, loan_id) or {}


def get_loan(tenant_id: int, loan_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM loan_entries WHERE tenant_id = ? AND id = ?",
        (tenant_id, loan_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def list_loans(tenant_id: int, *, status: str = "", subscriber_id: int | None = None,
               limit: int = 100, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM loan_entries WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if status:
        sql += " AND status = ?"
        vals.append(status)
    if subscriber_id:
        sql += " AND subscriber_id = ?"
        vals.append(subscriber_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def settle_loan(*, tenant_id: int, loan: dict, amount: float, currency: str,
                method: str, created_by: str, notes: str = "",
                metadata: dict[str, Any] | None = None) -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO settlement_entries(
                tenant_id, subscriber_id, username, loan_id, amount, currency,
                method, status, created_by, notes, metadata_json, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, loan["subscriber_id"], loan["username"], loan["id"],
                amount, currency, method, "posted", created_by, notes,
                json_dump(metadata or {}), now_iso(),
            ),
        )
        settlement_id = cur.lastrowid
        ledger_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="settlement",
            amount=amount,
            direction="credit",
            currency=currency,
            subscriber_id=loan["subscriber_id"],
            username=loan["username"],
            operator=created_by,
            source_type="settlement",
            source_id=settlement_id,
            related_type="loan",
            related_id=loan["id"],
            notes=notes,
            metadata=metadata,
        )
        conn.execute(
            "UPDATE settlement_entries SET ledger_entry_id = ? WHERE id = ?",
            (ledger_id, settlement_id),
        )
        conn.execute(
            """
            UPDATE loan_entries
            SET status = 'settled', settled_at = ?, settlement_entry_id = ?
            WHERE tenant_id = ? AND id = ?
            """,
            (now_iso(), settlement_id, tenant_id, loan["id"]),
        )
    return get_settlement(tenant_id, settlement_id) or {}


def get_settlement(tenant_id: int, settlement_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM settlement_entries WHERE tenant_id = ? AND id = ?",
        (tenant_id, settlement_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def void_ledger_entry(*, tenant_id: int, entry_id: int, actor: str,
                      reason: str = "") -> Optional[dict]:
    original = get_ledger_entry(tenant_id, entry_id)
    if not original:
        return None
    amount = -float(original["amount"] or 0)
    with transaction() as conn:
        new_id = create_ledger_entry(
            conn,
            tenant_id=tenant_id,
            entry_type="void",
            amount=amount,
            direction="debit" if original["direction"] == "credit" else "credit",
            currency=original["currency"],
            subscriber_id=original["subscriber_id"],
            username=original["username"],
            operator=actor,
            source_type="ledger_void",
            source_id=entry_id,
            reversal_of_entry_id=entry_id,
            status="void",
            notes=reason,
            metadata={"voided_entry_id": entry_id, "reason": reason},
        )
    return get_ledger_entry(tenant_id, new_id)


def sales_summary(tenant_id: int, *, grain: str = "daily") -> list[dict]:
    if grain == "monthly":
        expr = "substr(l.created_at, 1, 7)"
    elif grain == "yearly":
        expr = "substr(l.created_at, 1, 4)"
    else:
        expr = "substr(l.created_at, 1, 10)"
    rows = db().execute(
        f"""
        SELECT {expr} AS period, COUNT(*) AS count, COALESCE(SUM(l.amount), 0) AS total
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ?
          AND (
            (l.entry_type = 'payment' AND l.status = 'posted')
            OR (l.entry_type IN ('void', 'reversal', 'correction') AND orig.entry_type = 'payment')
          )
        GROUP BY {expr}
        ORDER BY period DESC
        LIMIT 60
        """,
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def subscriber_payment_report(tenant_id: int, *, subscriber_id: int | None = None) -> list[dict]:
    sql = """
        SELECT l.subscriber_id, l.username, COUNT(*) AS count,
               COALESCE(SUM(l.amount), 0) AS total,
               MAX(l.created_at) AS last_entry_at
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ?
          AND (
            (l.entry_type = 'payment' AND l.status = 'posted')
            OR (l.entry_type IN ('void', 'reversal', 'correction') AND orig.entry_type = 'payment')
          )
    """
    vals: list[Any] = [tenant_id]
    if subscriber_id:
        sql += " AND subscriber_id = ?"
        vals.append(subscriber_id)
    sql += " GROUP BY l.subscriber_id, l.username ORDER BY last_entry_at DESC, l.username LIMIT 200"
    return [dict(r) for r in db().execute(sql, vals).fetchall()]


def loan_report(tenant_id: int) -> list[dict]:
    rows = db().execute(
        """
        SELECT status, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total,
               COALESCE(SUM(duration_minutes), 0) AS duration_minutes
        FROM loan_entries
        WHERE tenant_id = ?
        GROUP BY status
        ORDER BY status
        """,
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def activation_report(tenant_id: int) -> list[dict]:
    rows = db().execute(
        """
        SELECT
            l.entry_type, l.status, l.amount, l.username, l.subscriber_id,
            l.metadata_json, orig.metadata_json AS orig_metadata_json
        FROM accounting_ledger_entries l
        LEFT JOIN accounting_ledger_entries orig
          ON orig.tenant_id = l.tenant_id AND orig.id = l.reversal_of_entry_id
        WHERE l.tenant_id = ?
          AND (
            (l.entry_type = 'payment' AND l.status = 'posted')
            OR (l.entry_type IN ('void', 'reversal', 'correction') AND orig.entry_type = 'payment')
          )
        """,
        (tenant_id,),
    ).fetchall()
    totals: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        meta = json_load(item.get("metadata_json"), default={}) or {}
        orig_meta = json_load(item.get("orig_metadata_json"), default={}) or {}
        minutes = int((orig_meta if item["entry_type"] != "payment" else meta).get("earned_minutes") or 0)
        if item["entry_type"] != "payment":
            minutes = -minutes
        key = item.get("username") or ""
        current = totals.setdefault(key, {
            "username": key,
            "subscriber_id": item.get("subscriber_id"),
            "activation_count": 0,
            "earned_minutes": 0,
        })
        current["activation_count"] += 1
        current["earned_minutes"] += minutes
    return sorted(totals.values(), key=lambda x: x["earned_minutes"], reverse=True)


def profit_loss_summary(tenant_id: int) -> list[dict]:
    row = db().execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END), 0) AS credits,
            COALESCE(SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END), 0) AS debits,
            COUNT(*) AS entries
        FROM accounting_ledger_entries
        WHERE tenant_id = ?
        """,
        (tenant_id,),
    ).fetchone()
    credits = float(row["credits"] or 0)
    debits = float(row["debits"] or 0)
    return [{
        "credits": credits,
        "debits": debits,
        "net": credits - debits,
        "entries": int(row["entries"] or 0),
        "source": "accounting_ledger_entries",
    }]


def card_sales_report(tenant_id: int) -> list[dict]:
    rows = db().execute(
        """
        SELECT source_id AS batch_id, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS total
        FROM accounting_ledger_entries
        WHERE tenant_id = ? AND entry_type = 'payment'
          AND source_type = 'card_sale' AND status = 'posted'
        GROUP BY source_id
        ORDER BY total DESC
        LIMIT 200
        """,
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def distributor_debts_report(tenant_id: int) -> list[dict]:
    rows = db().execute(
        """
        SELECT id AS distributor_id, name, display_name, debt_balance, balance, credit_limit
        FROM distributors
        WHERE tenant_id = ? AND status = 'active'
        ORDER BY debt_balance DESC, name
        LIMIT 200
        """,
        (tenant_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def create_report_snapshot(tenant_id: int, *, report_type: str,
                           result: dict | list, created_by: str = "",
                           date_from: str = "", date_to: str = "",
                           parameters: dict | None = None) -> dict:
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO financial_report_snapshots(
                tenant_id, report_type, date_from, date_to, parameters_json,
                result_json, source, created_by, created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                tenant_id, report_type, date_from, date_to,
                json_dump(parameters or {}), json_dump(result),
                "ledger", created_by, now_iso(),
            ),
        )
    row = db().execute(
        "SELECT * FROM financial_report_snapshots WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone()
    return _serialize_report_snapshot(row_to_dict(row))


def _serialize_report_snapshot(row: dict) -> dict:
    if not row:
        return {}
    data = dict(row)
    data["parameters"] = json_load(data.pop("parameters_json", "{}"), {})
    data["result"] = json_load(data.pop("result_json", "{}"), {})
    return data


def list_report_snapshots(tenant_id: int, *, report_type: str = "",
                          limit: int = 50, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM financial_report_snapshots WHERE tenant_id = ?"
    vals: list[Any] = [tenant_id]
    if report_type:
        sql += " AND report_type = ?"
        vals.append(report_type)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    rows = db().execute(sql, vals).fetchall()
    return [_serialize_report_snapshot(row_to_dict(r)) for r in rows]


def get_report_snapshot(tenant_id: int, snapshot_id: int) -> dict | None:
    row = db().execute(
        "SELECT * FROM financial_report_snapshots WHERE tenant_id = ? AND id = ?",
        (tenant_id, snapshot_id),
    ).fetchone()
    return _serialize_report_snapshot(row_to_dict(row)) if row else None
