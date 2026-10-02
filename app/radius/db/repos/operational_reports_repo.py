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
                limit: int = 100, offset: int = 0,
                date_from: str = "", date_to: str = "",
                result: str = "", source: str = "") -> dict:
    """Return a safe operational report payload for a known slug.

    ``date_from``/``date_to`` = يوم محلّيّ شامل (نفس صفحات تقارير الويب، عبر
    ``services.report_dates``). قيمة غير صالحة → ``ReportDateError``."""
    from ...services.report_dates import local_bounds, range_sql

    slug = slug.strip().lower()
    if slug not in REPORT_SLUGS:
        raise KeyError(slug)

    limit = _safe_limit(limit)
    offset = _safe_offset(offset)
    query = (query or "").strip()
    lower, upper, upper_excl = local_bounds(date_from, date_to, tenant_id)
    # login-event slugs: the web's result (الكل/نجاح/فشل) + source filters.
    result = (result or "").strip().lower()
    result = result if result in ("success", "fail") else ""
    source = (source or "").strip().lower()
    source = source if source in ("panel", "portal", "network") else ""
    matched: int | None = None

    def _rng(column: str) -> tuple[str, list[Any]]:
        where, params = range_sql(column, lower, upper, upper_excl)
        return "".join(f" AND {w}" for w in where), params

    # fix3 (F02 H2): the request admin's subscriber scope — the ONE predicate
    # (services/subscriber_scope) shared with the web report pages.
    from ...services.subscriber_scope import (audit_scope_sql, current_scope_admin_id,
                                              scope_sql)
    _scope_id = current_scope_admin_id(tenant_id=int(tenant_id))

    def _s(column: str, by: str = "username") -> tuple[str, list[Any]]:
        if _scope_id is None:
            return "", []
        return scope_sql(column, by=by, scope=int(_scope_id), tenant_id=int(tenant_id),
                         use_request=False)

    _audit_scope = (("", []) if _scope_id is None else
                    audit_scope_sql(scope=int(_scope_id), tenant_id=int(tenant_id),
                                    use_request=False))

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
        _sc, _sv = _s("username")
        sql += _sc
        vals.extend(_sv)
        if query:
            sql += " AND (username LIKE ? OR acctsessionid LIKE ? OR callingstationid LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        rs, rv = _rng("acctstarttime")
        sql += rs
        vals.extend(rv)
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
        _sc, _sv = _s("username")
        sql += _sc
        vals.extend(_sv)
        if query:
            sql += " AND (username LIKE ? OR reply LIKE ? OR nas LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        rs, rv = _rng("authdate")
        sql += rs
        vals.extend(rv)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug in ("login-states", "login-status"):
        # zero-w2: «login-status» = the web /reports/login_status page — the
        # flat login-attempts log (login_events), NOT a subscribers roster.
        data = fetch_login_events(tenant_id, q=query, result=result, source=source,
                                  limit=limit + offset,
                                  date_from=date_from, date_to=date_to)
        items = list(data.get("rows") or [])[offset:offset + limit]
        matched = int(data.get("matched") or 0)
    elif slug == "mac-history":
        sql = """
            SELECT username, callingstationid AS mac, nasipaddress,
                   COUNT(*) AS sessions, MAX(acctstarttime) AS last_seen
            FROM radacct
            WHERE tenant_id = ? AND callingstationid != ''
        """
        vals = [tenant_id]
        _sc, _sv = _s("username")
        sql += _sc
        vals.extend(_sv)
        if query:
            sql += " AND (username LIKE ? OR callingstationid LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%"])
        rs, rv = _rng("acctstarttime")
        sql += rs
        vals.extend(rv)
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
            rng=_rng("created_at"),
            scope=_audit_scope,
        ))
    elif slug == "api-messages":
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "actor LIKE 'api-token%'",
            query=query,
            limit=limit,
            offset=offset,
            rng=_rng("created_at"),
            scope=_audit_scope,
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
        _sc, _sv = _s("entity_key")
        sql += _sc
        vals.extend(_sv)
        if query:
            sql += " AND (entity_key LIKE ? OR kind LIKE ? OR last_error LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        rs, rv = _rng("created_at")
        sql += rs
        vals.extend(rv)
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
            rng=_rng("created_at"),
            scope=_audit_scope,
        ))
    elif slug == "manager-login-status":
        # zero-w2: same source as the web /reports/manager_login_status page —
        # manager login attempts (login_events, actor locked to "admin"); the
        # old admins roster had no tenant filter and ignored the dates.
        data = fetch_login_events(tenant_id, actor="admin", q=query, result=result,
                                  limit=limit + offset,
                                  date_from=date_from, date_to=date_to)
        items = list(data.get("rows") or [])[offset:offset + limit]
        matched = int(data.get("matched") or 0)
    elif slug == "user-events":
        items = _sanitize_audit(_audit_rows(
            tenant_id,
            "target_type = 'user'",
            query=query,
            limit=limit,
            offset=offset,
            rng=_rng("created_at"),
            scope=_audit_scope,
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
            rng=_rng("created_at"),
            scope=_audit_scope,
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
        from ...services.card_batch_scope import batch_scope_sql
        _bc, _bv = batch_scope_sql(column="c.batch_id", tenant_id=int(tenant_id))
        sql += _bc
        vals.extend(_bv)
        if query:
            sql += " AND (c.username LIKE ? OR c.used_by_mac LIKE ? OR p.name LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        rs, rv = _rng("c.first_used_at")
        sql += rs
        vals.extend(rv)
        sql += " ORDER BY COALESCE(c.first_used_at, '') DESC LIMIT ? OFFSET ?"
        vals.extend([limit, offset])
        items = _rows(sql, vals)
    elif slug == "balance-movements":
        items = _balance_movements(tenant_id, query=query, limit=limit, offset=offset,
                                   rng=_rng, scope=_scope_id)
    elif slug == "cash-transactions":
        sql = """
            SELECT id, created_at, username, amount, currency, method, status,
                   plan_price, effective_price, discount_amount, discount_reason,
                   earned_minutes, created_by, notes
            FROM payment_transactions
            WHERE tenant_id = ?
        """
        vals = [tenant_id]
        _sc, _sv = _s("subscriber_id", by="id")
        sql += _sc
        vals.extend(_sv)
        if query:
            sql += " AND (username LIKE ? OR created_by LIKE ? OR method LIKE ? OR status LIKE ?)"
            vals.extend([f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"])
        rs, rv = _rng("created_at")
        sql += rs
        vals.extend(rv)
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
        "date_from": date_from or "",
        "date_to": date_to or "",
        "result": result,
        "source": source,
        # total rows matching the filters (login-event slugs; None elsewhere)
        "matched": matched,
    }


def _audit_rows(tenant_id: int, predicate: str, *, query: str,
                limit: int, offset: int,
                q_cols: tuple[str, ...] = ("actor", "action", "target_id"),
                rng: tuple[str, list[Any]] = ("", []),
                scope: tuple[str, list[Any]] = ("", [])) -> list[dict]:
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
    sql += rng[0]
    vals.extend(rng[1])
    sql += scope[0]
    vals.extend(scope[1])
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals.extend([limit, offset])
    return _rows(sql, vals)


_MOVEMENT_LABELS = {
    "payment_to_balance": "دفعة — إضافة للرصيد",
    "payment_to_debt": "دفعة — خصم من الدين",
    "debt_settle": "تسديد دين",
    "on_account_credit": "رصيد على الحساب (دين)",
    "settlement": "تسوية حساب",
}

def _balance_movements(tenant_id: int, *, query: str, limit: int, offset: int,
                       rng=None, scope=None) -> list[dict]:
    """حركات الرصيد من دفترين (عامّ + موزّعين) مدموجةً ومرتّبةً زمنيًّا.

    الترقيم يُطبَّق على **الناتج المدموج** لا على كلّ استعلام وحده: نجلب
    أوّل offset+limit صفًّا من كلّ مصدر، ندمجها بترتيب ثابت
    (created_at تنازليًّا ثمّ النطاق ثمّ id)، ثمّ نقصّ [offset:offset+limit].
    كان الإزاحة تُطبَّق على كلّ مصدر منفردًا ثمّ يُقصّ المدموج إلى limit، فلا
    تظهر حركات الموزّعين في أيّ صفحة (653 صفًّا → 420 فريدًا فقط)."""
    window = limit + offset
    general_sql = """
        SELECT id AS entry_id, created_at, entry_type, direction, amount, currency, username,
               operator, admin_id, source_type, status, notes,
               'general' AS scope
        FROM accounting_ledger_entries
        WHERE tenant_id = ?
    """
    general_vals: list[Any] = [tenant_id]
    if scope is not None:
        # fix3: his subscribers' movements + his own manager-wallet rows.
        from ...services.subscriber_scope import scope_sql
        _sc, _sv = scope_sql("subscriber_id", by="id", scope=int(scope),
                             tenant_id=int(tenant_id), use_request=False)
        general_sql += " AND (" + _sc[len(" AND "):] + " OR admin_id = ?)"
        general_vals.extend([*_sv, int(scope)])
    if query:
        general_sql += (
            " AND (username LIKE ? OR operator LIKE ? OR entry_type LIKE ? "
            "OR source_type LIKE ? OR status LIKE ?)"
        )
        general_vals.extend([f"%{query}%"] * 5)
    if rng is not None:
        rs, rv = rng("created_at")
        general_sql += rs
        general_vals.extend(rv)
    general_sql += " ORDER BY id DESC LIMIT ?"
    general_vals.append(window)
    items: list[dict] = list(_optional_rows(general_sql, general_vals))

    distributor_sql = """
        SELECT dl.id AS entry_id, dl.created_at, dl.entry_type, dl.direction, dl.amount, dl.currency,
               COALESCE(d.name, '') AS username, dl.created_by AS operator,
               dl.distributor_id AS admin_id, 'distributor' AS source_type,
               '' AS status, dl.notes, 'distributor' AS scope
        FROM distributor_ledger_entries dl
        LEFT JOIN distributors d ON d.tenant_id = dl.tenant_id AND d.id = dl.distributor_id
        WHERE dl.tenant_id = ?
    """
    distributor_vals: list[Any] = [tenant_id]
    if scope is not None:
        distributor_sql += " AND (d.admin_id = ? OR d.login_admin_id = ?)"
        distributor_vals.extend([int(scope), int(scope)])
    if query:
        distributor_sql += " AND (d.name LIKE ? OR dl.entry_type LIKE ?)"
        distributor_vals.extend([f"%{query}%"] * 2)
    if rng is not None:
        rs, rv = rng("dl.created_at")
        distributor_sql += rs
        distributor_vals.extend(rv)
    distributor_sql += " ORDER BY dl.id DESC LIMIT ?"
    distributor_vals.append(window)
    items.extend(_optional_rows(distributor_sql, distributor_vals))
    # دفعات الموزّع بأثرٍ واحد: التسمية تُظهر أيّهما (للرصيد أم من الدين).
    for row in items:
        row["entry_label"] = _MOVEMENT_LABELS.get(str(row.get("entry_type") or ""), "")

    def _key(row: dict) -> tuple:
        ts = str(row.get("created_at") or "").replace(" ", "T").rstrip("Z")
        return (ts, str(row.get("scope") or ""), int(row.get("entry_id") or 0))

    items.sort(key=_key, reverse=True)
    return items[offset:offset + limit]
