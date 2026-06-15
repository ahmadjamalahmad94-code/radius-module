"""data_connection_wg_peers — repo لقرناء WireGuard لاتصال البيانات (v7).

feat/data-connection-oneclick (migration 123). صفّ لكل قرين WG يُنشأ من زرّ
«اتصال بيانات» للمشترك. **لا يُخزَّن المفتاح الخاص للعميل** — يُولَّد ويُعرض
مرّة واحدة داخل السكربت. علما applied_to_vps/queue_applied افتراضهما 0
(LAB-PENDING: لم يُدفع القرين/السقف إلى الـVPS الحيّ بعد).
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso, row_to_dict


def create_peer(
    *, tenant_id: int, subscriber_id: int, username: str, public_key: str,
    assigned_ip: str, endpoint_host: str, endpoint_port: int,
    allowed_address: str = "0.0.0.0/0", speed_kbit: int = 5120,
) -> int:
    """يُنشئ صفّ قرين ويُعيد معرّفه. يفشل لو assigned_ip مكرّر (UNIQUE)."""
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO data_connection_wg_peers
                (tenant_id, subscriber_id, username, public_key, assigned_ip,
                 endpoint_host, endpoint_port, allowed_address, speed_kbit,
                 applied_to_vps, queue_applied, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'pending', ?, ?)
            """,
            (int(tenant_id), int(subscriber_id), str(username or ""),
             str(public_key or ""), str(assigned_ip or ""),
             str(endpoint_host or ""), int(endpoint_port or 0),
             str(allowed_address or "0.0.0.0/0"), int(speed_kbit or 0),
             now, now),
        )
        return int(cur.lastrowid)


def list_peers(tenant_id: int, *, subscriber_id: Optional[int] = None,
               limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM data_connection_wg_peers WHERE tenant_id = ?"
    vals: list = [int(tenant_id)]
    if subscriber_id is not None:
        sql += " AND subscriber_id = ?"
        vals.append(int(subscriber_id))
    sql += " ORDER BY id DESC LIMIT ?"
    vals.append(int(limit))
    return [row_to_dict(r) for r in db().execute(sql, vals).fetchall()]


def used_ips(tenant_id: int) -> set[str]:
    """كل العناوين المُسنَدة (لتفادي التصادم عند تخصيص التالي)."""
    rows = db().execute(
        "SELECT assigned_ip FROM data_connection_wg_peers WHERE tenant_id = ?",
        (int(tenant_id),),
    ).fetchall()
    return {str(r["assigned_ip"]) for r in rows if r["assigned_ip"]}


def mark_applied(peer_id: int, *, applied_to_vps: bool = False,
                 queue_applied: bool = False) -> None:
    """LAB-PENDING — يُعلّم القرين بأنه دُفع/طُبِّق سقفه على الـVPS الحيّ.
    لا يُستدعى من المسار الحالي (التطبيق على الـVPS متابعة مخبرية)."""
    with transaction() as conn:
        conn.execute(
            "UPDATE data_connection_wg_peers "
            "SET applied_to_vps = ?, queue_applied = ?, status = ?, updated_at = ? "
            "WHERE id = ?",
            (1 if applied_to_vps else 0, 1 if queue_applied else 0,
             "active" if applied_to_vps else "pending", now_iso(), int(peer_id)),
        )


__all__ = ["create_peer", "list_peers", "used_ips", "mark_applied"]
