"""Vouchers repo."""
from __future__ import annotations

import secrets
import string
from typing import Optional

from ...core.types_saas import Voucher
from ..connection import db, transaction
from ..helpers import dt_to_iso, now_iso, parse_dt


def _row(r) -> Voucher:
    return Voucher(
        id=r["id"], tenant_id=r["tenant_id"], code=r["code"], amount=r["amount"],
        plan_id=r["plan_id"], status=r["status"],
        used_by_subscriber_id=r["used_by_subscriber_id"],
        used_at=parse_dt(r["used_at"]), expire_at=parse_dt(r["expire_at"]),
        generated_by=r["generated_by"] or 0,
        created_at=parse_dt(r["created_at"]),
    )


def list_all(tenant_id: int, *, status: Optional[str] = None,
             limit: int = 200, offset: int = 0) -> list[Voucher]:
    sql = "SELECT * FROM vouchers WHERE tenant_id = ?"
    vals: list = [tenant_id]
    if status:
        sql += " AND status = ?"; vals.append(status)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    return [_row(r) for r in db().execute(sql, vals).fetchall()]


def get(tenant_id: int, vid: int) -> Optional[Voucher]:
    row = db().execute(
        "SELECT * FROM vouchers WHERE tenant_id = ? AND id = ?",
        (tenant_id, vid)).fetchone()
    return _row(row) if row else None


def _gen_code() -> str:
    raw = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def generate_bulk(*, tenant_id: int, amount: float, count: int,
                  plan_id: Optional[int] = None, expire_at=None,
                  generated_by: int = 0) -> list[Voucher]:
    if count <= 0: return []
    seen = {r["code"] for r in db().execute(
        "SELECT code FROM vouchers WHERE tenant_id = ?", (tenant_id,)).fetchall()}
    now = now_iso()
    rows = []
    for _ in range(count):
        for _try in range(30):
            code = _gen_code()
            if code not in seen:
                seen.add(code); break
        rows.append((tenant_id, code, amount, plan_id, "active",
                     dt_to_iso(expire_at), generated_by, now))
    with transaction() as conn:
        conn.executemany("""
            INSERT INTO vouchers(tenant_id, code, amount, plan_id, status, expire_at, generated_by, created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, rows)
    return list_all(tenant_id, status="active", limit=count)


def revoke(tenant_id: int, vid: int) -> None:
    with transaction() as conn:
        conn.execute("UPDATE vouchers SET status = 'revoked' WHERE tenant_id = ? AND id = ?",
                     (tenant_id, vid))


def stats(tenant_id: int) -> dict:
    rows = db().execute("""
        SELECT status, COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total
        FROM vouchers WHERE tenant_id = ? GROUP BY status
    """, (tenant_id,)).fetchall()
    out = {"active": 0, "used": 0, "revoked": 0, "expired": 0,
           "total_amount": 0.0, "total_count": 0}
    for r in rows:
        out[r["status"]] = r["c"]
        out["total_count"] += r["c"]
        out["total_amount"] += r["total"] or 0
    return out
