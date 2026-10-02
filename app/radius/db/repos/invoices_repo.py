"""Invoices repo."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ...core.types_saas import Invoice
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt


def _row(r) -> Invoice:
    return Invoice(
        id=r["id"], tenant_id=r["tenant_id"],
        invoice_number=r["invoice_number"], subscriber_id=r["subscriber_id"],
        username=r["username"], amount=r["amount"],
        admin_id=r["admin_id"] or 0, plan_id=r["plan_id"],
        plan_name=r["plan_name"] or "", service_type=r["service_type"] or "Hotspot",
        router_id=r["router_id"], direction=r["direction"],
        balance_before=r["balance_before"] or 0.0,
        balance_after=r["balance_after"] or 0.0,
        recharged_on=parse_dt(r["recharged_on"]),
        expiration_at=parse_dt(r["expiration_at"]),
        payment_method=r["payment_method"] or "cash",
        payment_gateway_id=r["payment_gateway_id"],
        status=r["status"], note=r["note"] or "",
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
        currency=_row_currency(r),
    )


def _row_currency(r) -> str:
    try:
        value = r["currency"]
    except (IndexError, KeyError):   # a DB before migration 194
        value = ""
    return str(value or "").strip().upper()


def _currency_for_new(inv: Invoice) -> str:
    """The currency a NEW invoice is written in: the one given, else the
    system currency at write time (stored, so a later setting change does not
    re-label it)."""
    if (inv.currency or "").strip():
        return inv.currency.strip().upper()
    from ...core.system_config import default_currency
    return default_currency()


def _next_invoice_number(tenant_id: int) -> str:
    c = db().execute(
        "SELECT COUNT(*) AS c FROM invoices WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()["c"]
    return f"INV-{datetime.utcnow().strftime('%Y%m')}-{c+1:05d}"


def list_all(tenant_id: int, *, status: Optional[str] = None,
             subscriber_id: Optional[int] = None,
             limit: int = 200, offset: int = 0) -> list[Invoice]:
    # fix3 (F02 H2): the request admin's subscriber scope (owner: all).
    from ...services.subscriber_scope import scope_sql
    _sc, _sv = scope_sql("subscriber_id", by="id", tenant_id=int(tenant_id))
    sql = "SELECT * FROM invoices WHERE tenant_id = ?" + _sc
    vals: list = [tenant_id, *_sv]
    if status:
        sql += " AND status = ?"; vals.append(status)
    if subscriber_id is not None:
        sql += " AND subscriber_id = ?"; vals.append(subscriber_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    return [_row(r) for r in db().execute(sql, vals).fetchall()]


def get(tenant_id: int, iid: int) -> Optional[Invoice]:
    row = db().execute(
        "SELECT * FROM invoices WHERE tenant_id = ? AND id = ?",
        (tenant_id, iid)).fetchone()
    return _row(row) if row else None


def create(inv: Invoice) -> Invoice:
    now = now_iso()
    number = inv.invoice_number or _next_invoice_number(inv.tenant_id)
    with transaction() as conn:
        cur = conn.execute("""
            INSERT INTO invoices(tenant_id, invoice_number, subscriber_id, username, amount,
                admin_id, plan_id, plan_name, service_type, router_id, direction,
                balance_before, balance_after, recharged_on, expiration_at,
                payment_method, payment_gateway_id, status, note, created_at, updated_at,
                currency)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (inv.tenant_id, number, inv.subscriber_id, inv.username, inv.amount,
              inv.admin_id, inv.plan_id, inv.plan_name, inv.service_type,
              inv.router_id, inv.direction, inv.balance_before, inv.balance_after,
              dt_to_iso(inv.recharged_on), dt_to_iso(inv.expiration_at),
              inv.payment_method, inv.payment_gateway_id, inv.status, inv.note, now, now,
              _currency_for_new(inv)))
        new_id = cur.lastrowid
    return get(inv.tenant_id, new_id)


def update_status(tenant_id: int, iid: int, status: str, *, note: str = "") -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE invoices SET status = ?, note = COALESCE(NULLIF(?, ''), note), updated_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            (status, note, now_iso(), tenant_id, iid)
        )


def stats(tenant_id: int) -> dict:
    from ...services.subscriber_scope import scope_sql
    _sc, _sv = scope_sql("subscriber_id", by="id", tenant_id=int(tenant_id))
    cur = db().execute("""
        SELECT status, COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total
        FROM invoices WHERE tenant_id = ?""" + _sc + """ GROUP BY status
    """, (tenant_id, *_sv))
    out = {"total": 0.0, "paid": 0.0, "pending": 0.0, "count": 0}
    for r in cur.fetchall():
        out[r["status"]] = r["total"] or 0
        out["total"] += r["total"] or 0
        out["count"] += r["c"]
    return out
