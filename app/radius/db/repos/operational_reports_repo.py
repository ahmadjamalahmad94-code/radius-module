"""Read-only operational reports used by Web and Flutter clients."""
from __future__ import annotations

import json
from typing import Any

from ..connection import db
from ..helpers import row_to_dict


REPORT_SLUGS = {
    "sessions",
    "failed-logins",
    "login-status",
    "mac-history",
    "profile-changes",
    "api-messages",
    "coa-failures",
    "manager-events",
    "manager-login-status",
    "user-events",
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
    else:  # user-events
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "target_type = 'user'",
            query=query,
            limit=limit,
            offset=offset,
        ))

    return {
        "slug": slug,
        "items": items,
        "count": len(items),
        "query": query,
        "limit": limit,
        "offset": offset,
    }


def _audit_rows(tenant_id: int, predicate: str, *, query: str,
                limit: int, offset: int) -> list[dict]:
    sql = f"""
        SELECT id, actor, action, target_type, target_id, payload_json,
               ip_address, user_agent, created_at
        FROM audit_log
        WHERE tenant_id = ? AND {predicate}
    """
    vals: list[Any] = [tenant_id]
    if query:
        sql += " AND (actor LIKE ? OR action LIKE ? OR target_id LIKE ?)"
        vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return _rows(sql, vals)
