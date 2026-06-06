"""Read-only operational reports used by Web and Flutter clients."""
from __future__ import annotations

import json
from typing import Any

from ..connection import db
from ..helpers import row_to_dict
from ...services.login_events import fetch_login_events


REPORT_SLUGS = {
    "sessions",
    "failed-logins",
    "login-states",
    "login-status",
    "mac-history",
    "profile-changes",
    "api-messages",
    "coa-failures",
    "manager-events",
    "manager-login-status",
    "user-events",
    "speed-failures",
    "used-cards",
    "balance-movements",
    "cash-transactions",
}

_SENSITIVE_KEYS = {"password", "pass", "secret", "token", "token_hash", "api_key"}


def _safe_limit(value: Any, default: int = 100) -> int:
    try:
        return min(max(int(value or default), 1), 1000)
    except (TypeError, ValueError):
        return default


def _safe_offset(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _row(row) -> dict:
    return row_to_dict(row) if row else {}


def _rows(sql: str, values: list[Any]) -> list[dict]:
    return [_row(r) for r in db().execute(sql, values).fetchall()]


def _optional_rows(sql: str, values: list[Any]) -> list[dict]:
    try:
        return _rows(sql, values)
    except Exception:
        return []


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[redacted]" if key.lower() in _SENSITIVE_KEYS else _redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _decode_payload(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return _redact_value(raw)
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return _redact_value(data if isinstance(data, dict) else {})


def _sanitize_audit(items: list[dict]) -> list[dict]:
    for item in items:
        item["payload"] = _decode_payload(item.pop("payload_json", None))
    return items


def _sanitize_sync(items: list[dict]) -> list[dict]:
    for item in items:
        item["payload"] = _decode_payload(item.pop("payload_json", None))
    return items


def list_report(tenant_id: int, slug: str, *, query: str = "",
                limit: int = 100, offset: int = 0) -> dict:
    """Return a safe operational report payload for a known slug."""

    slug = slug.strip().lower()
    if slug not in REPORT_SLUGS:
        raise KeyError(slug)

    limit = _safe_limit(limit)
    offset = _safe_offset(offset)
    query = (query or "").strip()

    if slug == "sessions":
        sql = """
            SELECT radacctid, acctsessionid, acctuniqueid, username, nasipaddress,
                   nasportid, nasporttype, acctstarttime, acctupdatetime,
                   acctstoptime, acctsessiontime, acctinputoctets,
                   acctoutputoctets, calledstationid, callingstationid,
                   acctterminatecause, servicetype, framedprotocol,
                   framedipaddress, framedipv6address
            FROM radacct
            WHERE tenant_id = ?
        """
        vals: list[Any] = [tenant_id]
        if query:
            sql += " AND (username LIKE ? OR acctsessionid LIKE ? OR callingstationid LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        sql += " ORDER BY radacctid DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug == "failed-logins":
        sql = """
            SELECT id, username, reply, authdate, class, nas
            FROM radpostauth
            WHERE tenant_id = ? AND reply != 'Access-Accept'
        """
        vals = [tenant_id]
        if query:
            sql += " AND (username LIKE ? OR reply LIKE ? OR nas LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug == "login-states":
        data = fetch_login_events(tenant_id, q=query, limit=limit + offset)
        items = list(data.get("rows") or [])[offset:offset + limit]
    elif slug == "login-status":
        sql = """
            SELECT username, last_login_at, last_seen_at, status, expire_at, online_count
            FROM subscribers
            WHERE tenant_id = ? AND deleted_at IS NULL
        """
        vals = [tenant_id]
        if query:
            sql += " AND username LIKE ?"
            vals.append(f"%{query}%")
        sql += " ORDER BY COALESCE(last_seen_at, '') DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug == "mac-history":
        sql = """
            SELECT username, callingstationid AS mac, nasipaddress,
                   COUNT(*) AS sessions, MAX(acctstarttime) AS last_seen
            FROM radacct
            WHERE tenant_id = ? AND callingstationid != ''
        """
        vals = [tenant_id]
        if query:
            sql += " AND (username LIKE ? OR callingstationid LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%"])
        sql += """
            GROUP BY username, callingstationid, nasipaddress
            ORDER BY COALESCE(last_seen, '') DESC
            LIMIT ? OFFSET ?
        """
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug == "profile-changes":
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "target_type = 'user' AND action IN ('update','extend_time')",
            query=query,
            limit=limit,
            offset=offset,
        ))
    elif slug == "api-messages":
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "actor LIKE 'api-token%'",
            query=query,
            limit=limit,
            offset=offset,
        ))
    elif slug == "coa-failures":
        sql = """
            SELECT id, router_id, kind, entity_id, entity_key, status, attempts,
                   last_error, last_router_id, next_attempt_at, completed_at, created_at,
                   payload_json
            FROM sync_queue
            WHERE tenant_id = ?
              AND kind IN ('disconnect','reset_password')
              AND status IN ('failed','retrying')
        """
        vals = [tenant_id]
        if query:
            sql += " AND (entity_key LIKE ? OR kind LIKE ? OR last_error LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _sanitize_sync(_rows(sql, vals))
    elif slug == "manager-events":
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "actor NOT LIKE 'api-token%' AND actor != 'system'",
            query=query,
            limit=limit,
            offset=offset,
        ))
    elif slug == "manager-login-status":
        sql = """
            SELECT a.id, a.username, a.full_name, a.email, a.role_id, a.enabled,
                   a.last_login_at, a.created_at, r.name AS role_name,
                   r.display_name AS role_display_name
            FROM admins a
            LEFT JOIN roles r ON r.id = a.role_id
            WHERE COALESCE(a.deleted_at, '') = ''
        """
        vals = []
        if query:
            sql += " AND (a.username LIKE ? OR a.full_name LIKE ? OR a.email LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        sql += " ORDER BY COALESCE(a.last_login_at, '') DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug == "user-events":
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "target_type = 'user'",
            query=query,
            limit=limit,
            offset=offset,
        ))
    if slug == "speed-failures":
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "result_status = 'failed' "
            "AND (action LIKE '%speed%' OR action LIKE '%profile%' OR action = 'bulk_set_speeds')",
            query=query,
            limit=limit,
            offset=offset,
            q_cols=("actor", "action", "target_id", "error_message"),
        ))
    elif slug == "used-cards":
        sql = """
            SELECT c.id, c.username, c.used_by_mac, c.first_used_at,
                   c.expire_at, c.revoked, c.plan_id, COALESCE(p.name, '') AS plan_name
            FROM cards c
            LEFT JOIN access_plans p ON p.tenant_id = c.tenant_id AND p.id = c.plan_id
            WHERE c.tenant_id = ? AND c.used = 1
        """
        vals = [tenant_id]
        if query:
            sql += " AND (c.username LIKE ? OR c.used_by_mac LIKE ? OR p.name LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        sql += " ORDER BY COALESCE(c.first_used_at, '') DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug == "balance-movements":
        items = _balance_movements(tenant_id, query=query, limit=limit, offset=offset)
    elif slug == "cash-transactions":
        sql = """
            SELECT id, created_at, username, amount, currency, method, status,
                   plan_price, effective_price, discount_amount, discount_reason,
                   earned_minutes, created_by, notes
            FROM payment_transactions
            WHERE tenant_id = ?
        """
        vals = [tenant_id]
        if query:
            sql += " AND (username LIKE ? OR created_by LIKE ? OR method LIKE ? OR status LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"])
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)

    return {
        "slug": slug,
        "items": items,
        "count": len(items),
        "query": query,
        "limit": limit,
        "offset": offset,
    }


def _audit_rows(tenant_id: int, predicate: str, *, query: str,
                limit: int, offset: int,
                q_cols: tuple[str, ...] = ("actor", "action", "target_id")) -> list[dict]:
    sql = f"""
        SELECT id, actor, action, target_type, target_id, payload_json,
               ip_address, user_agent, result_status, error_message, created_at
        FROM audit_log
        WHERE tenant_id = ? AND {predicate}
    """
    vals: list[Any] = [tenant_id]
    if query:
        sql += " AND (" + " OR ".join(f"{col} LIKE ?" for col in q_cols) + ")"
        vals.extend([f"%{query}%"] * len(q_cols))
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return _rows(sql, vals)


def _balance_movements(tenant_id: int, *, query: str, limit: int, offset: int) -> list[dict]:
    items: list[dict] = []
    general_sql = """
        SELECT created_at, entry_type, direction, amount, currency, username,
               operator, admin_id, source_type, status, notes,
               'general' AS scope
        FROM accounting_ledger_entries
        WHERE tenant_id = ?
    """
    general_vals: list[Any] = [tenant_id]
    if query:
        general_sql += (
            " AND (username LIKE ? OR operator LIKE ? OR entry_type LIKE ? "
            "OR source_type LIKE ? OR status LIKE ?)"
        )
        general_vals.extend([f"%{query}%"] * 5)
    general_sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    general_vals.extend([limit, offset])
    items.extend(_optional_rows(general_sql, general_vals))

    distributor_sql = """
        SELECT dl.created_at, dl.entry_type, dl.direction, dl.amount, dl.currency,
               COALESCE(d.name, '') AS username, dl.created_by AS operator,
               dl.distributor_id AS admin_id, 'distributor' AS source_type,
               '' AS status, dl.notes, 'distributor' AS scope
        FROM distributor_ledger_entries dl
        LEFT JOIN distributors d ON d.tenant_id = dl.tenant_id AND d.id = dl.distributor_id
        WHERE dl.tenant_id = ?
    """
    distributor_vals: list[Any] = [tenant_id]
    if query:
        distributor_sql += " AND (d.name LIKE ? OR dl.entry_type LIKE ?)"
        distributor_vals.extend([f"%{query}%"] * 2)
    distributor_sql += " ORDER BY dl.id DESC LIMIT ? OFFSET ?"
    distributor_vals.extend([limit, offset])
    items.extend(_optional_rows(distributor_sql, distributor_vals))

    items.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return items[:limit]
