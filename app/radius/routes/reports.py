"""
Reports — قراءات تحليلية مبنية على جداولنا (radacct, radpostauth, audit_log,
sync_queue, webhook_deliveries). كلها read-only، tenant-scoped.
"""
from __future__ import annotations

from flask import Blueprint, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_reports_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/reports/sessions", "rep_sessions", rep_sessions, methods=["GET"])
    bp.add_url_rule("/reports/failed_logins", "rep_failed_logins", rep_failed_logins, methods=["GET"])
    bp.add_url_rule("/reports/login_status", "rep_login_status", rep_login_status, methods=["GET"])
    bp.add_url_rule("/reports/mac_history", "rep_mac_history", rep_mac_history, methods=["GET"])
    bp.add_url_rule("/reports/profile_changes", "rep_profile_changes", rep_profile_changes, methods=["GET"])
    bp.add_url_rule("/reports/api_messages", "rep_api_messages", rep_api_messages, methods=["GET"])
    bp.add_url_rule("/reports/coa_failures", "rep_coa_failures", rep_coa_failures, methods=["GET"])
    bp.add_url_rule("/reports/manager_events", "rep_manager_events", rep_manager_events, methods=["GET"])
    bp.add_url_rule("/reports/manager_login_status", "rep_manager_login_status", rep_manager_login_status, methods=["GET"])
    bp.add_url_rule("/reports/user_events", "rep_user_events", rep_user_events, methods=["GET"])


def _limit() -> tuple[int, int]:
    try:
        l = min(max(int(request.args.get("limit") or 100), 1), 1000)
        o = max(int(request.args.get("offset") or 0), 0)
    except ValueError:
        l, o = 100, 0
    return l, o


# ─────────────── 1. Sessions (radacct) ───────────────

def rep_sessions():
    limit, offset = _limit()
    username = (request.args.get("username") or "").strip()
    sql = "SELECT * FROM radacct WHERE tenant_id = ?"
    vals: list = [_tid()]
    if username:
        sql += " AND username LIKE ?"
        vals.append(f"%{username}%")
    sql += " ORDER BY radacctid DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    rows = [dict(r) for r in db().execute(sql, vals).fetchall()]
    return render_template("radius/rep_sessions.html",
                            items=rows, username=username, limit=limit)


# ─────────────── 2. Failed logins (radpostauth Access-Reject) ───────────────

def rep_failed_logins():
    limit, offset = _limit()
    rows = [dict(r) for r in db().execute("""
        SELECT * FROM radpostauth
        WHERE tenant_id = ? AND reply != 'Access-Accept'
        ORDER BY id DESC LIMIT ? OFFSET ?
    """, (_tid(), limit, offset)).fetchall()]
    return render_template("radius/rep_failed_logins.html", items=rows, limit=limit)


# ─────────────── 3. Login status (last login per user) ───────────────

def rep_login_status():
    rows = [dict(r) for r in db().execute("""
        SELECT username, last_login_at, last_seen_at, status, expire_at, online_count
        FROM subscribers
        WHERE tenant_id = ?
        ORDER BY last_seen_at DESC NULLS LAST
        LIMIT 500
    """, (_tid(),)).fetchall()]
    return render_template("radius/rep_login_status.html", items=rows)


# ─────────────── 4. MAC history (per username distinct MACs) ───────────────

def rep_mac_history():
    rows = [dict(r) for r in db().execute("""
        SELECT username, callingstationid AS mac, nasipaddress,
               COUNT(*) AS sessions, MAX(acctstarttime) AS last_seen
        FROM radacct
        WHERE tenant_id = ? AND callingstationid != ''
        GROUP BY username, callingstationid
        ORDER BY last_seen DESC NULLS LAST
        LIMIT 500
    """, (_tid(),)).fetchall()]
    return render_template("radius/rep_mac_history.html", items=rows)


# ─────────────── 5. Profile (plan) changes (audit_log) ───────────────

def rep_profile_changes():
    rows = [dict(r) for r in db().execute("""
        SELECT * FROM audit_log
        WHERE tenant_id = ? AND target_type = 'user' AND action IN ('update','extend_time')
        ORDER BY id DESC LIMIT 300
    """, (_tid(),)).fetchall()]
    return render_template("radius/rep_profile_changes.html", items=rows)


# ─────────────── 6. API messages (audit_log where actor=api-token) ───────────────

def rep_api_messages():
    rows = [dict(r) for r in db().execute("""
        SELECT * FROM audit_log
        WHERE tenant_id = ? AND actor LIKE 'api-token%'
        ORDER BY id DESC LIMIT 300
    """, (_tid(),)).fetchall()]
    return render_template("radius/rep_api_messages.html", items=rows)


# ─────────────── 7. CoA failures (sync_queue disconnect failed) ───────────────

def rep_coa_failures():
    rows = [dict(r) for r in db().execute("""
        SELECT * FROM sync_queue
        WHERE tenant_id = ? AND kind IN ('disconnect','reset_password')
              AND status IN ('failed','retrying')
        ORDER BY id DESC LIMIT 300
    """, (_tid(),)).fetchall()]
    return render_template("radius/rep_coa_failures.html", items=rows)


# ─────────────── 8. Manager events (admin actions) ───────────────

def rep_manager_events():
    rows = [dict(r) for r in db().execute("""
        SELECT * FROM audit_log
        WHERE tenant_id = ? AND actor NOT LIKE 'api-token%' AND actor != 'system'
        ORDER BY id DESC LIMIT 500
    """, (_tid(),)).fetchall()]
    return render_template("radius/rep_manager_events.html", items=rows)


# ─────────────── 9. Manager login status (admins) ───────────────

def rep_manager_login_status():
    rows = [dict(r) for r in db().execute("""
        SELECT id, username, full_name, email, role_id, enabled, last_login_at, created_at
        FROM admins ORDER BY last_login_at DESC NULLS LAST
    """).fetchall()]
    return render_template("radius/rep_manager_login_status.html", items=[dict(r) for r in rows])


# ─────────────── 10. User events (per subscriber) ───────────────

def rep_user_events():
    rows = [dict(r) for r in db().execute("""
        SELECT * FROM audit_log
        WHERE tenant_id = ? AND target_type = 'user'
        ORDER BY id DESC LIMIT 500
    """, (_tid(),)).fetchall()]
    return render_template("radius/rep_user_events.html", items=rows)
