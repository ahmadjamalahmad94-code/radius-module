"""تقارير VPN + WireGuard + سجل التدقيق — مع تصدير CSV.

المسارات:
  GET  /service-reports                          → لوحة التقارير الرئيسية
  GET  /service-reports/export/vpn-accounts.csv  → تصدير حسابات VPN
  GET  /service-reports/export/wg-peers.csv      → تصدير WireGuard peers
  GET  /service-reports/export/audit-log.csv     → تصدير سجل التدقيق

كل المسارات read-only، tenant-scoped، تتطلّب تسجيل دخول.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, Response, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Helpers ──────────────────────────────────────────────────────

def _vpn_accounts(tenant_id: int, limit: int = 500) -> list:
    return db().execute(
        """SELECT
               va.id,
               va.username,
               va.status,
               va.allocation_mirror_id,
               sam.service_type,
               sam.expires_at,
               va.created_at,
               va.updated_at
           FROM vpn_account va
           LEFT JOIN service_allocation_mirror sam
               ON sam.id = va.allocation_mirror_id
           WHERE va.tenant_id = ?
           ORDER BY va.id DESC
           LIMIT ?""",
        (tenant_id, limit),
    ).fetchall()


def _wg_peers(tenant_id: int, limit: int = 500) -> list:
    return db().execute(
        """SELECT
               p.id,
               p.display_name,
               p.public_key,
               p.peer_address,
               p.status,
               p.speed_limit_mbps,
               p.transfer_limit_bytes,
               p.quota_bytes_used,
               p.quota_period,
               p.last_handshake_at,
               p.created_at,
               s.interface_name
           FROM wireguard_peer p
           JOIN wireguard_data_service s ON s.id = p.service_id
           WHERE p.tenant_id = ?
           ORDER BY p.id DESC
           LIMIT ?""",
        (tenant_id, limit),
    ).fetchall()


def _audit_log(tenant_id: int, limit: int = 1000) -> list:
    return db().execute(
        """SELECT
               id,
               entity_type,
               entity_id,
               action,
               actor,
               description,
               created_at
           FROM service_audit_log
           WHERE tenant_id = ?
           ORDER BY id DESC
           LIMIT ?""",
        (tenant_id, limit),
    ).fetchall()


def _allocations_index(tenant_id: int) -> dict:
    """خريطة allocation_id → service_type للعرض السريع."""
    rows = db().execute(
        "SELECT id, service_type, status FROM service_allocation_mirror WHERE tenant_id=?",
        (tenant_id,),
    ).fetchall()
    return {r["id"]: r for r in rows}


def _wg_service(tenant_id: int):
    return db().execute(
        "SELECT id, interface_name, status, quota_bytes_used, transfer_limit_bytes, max_peers "
        "FROM wireguard_data_service WHERE tenant_id=? LIMIT 1",
        (tenant_id,),
    ).fetchone()


# ─── CSV builder ──────────────────────────────────────────────────

def _csv_response(filename: str, headers: list[str], rows) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow([row[h] if hasattr(row, "keys") else row[i]
                    for i, h in enumerate(headers)])
    return Response(
        "﻿" + buf.getvalue(),           # BOM للتوافق مع Excel/Arabic
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-store",
        },
    )


def _csv_rows_vpn(rows) -> list[list]:
    out = []
    for r in rows:
        out.append([
            r["id"],
            r["username"],
            r["status"],
            r["service_type"] or "",
            r["expires_at"] or "",
            r["created_at"] or "",
            r["updated_at"] or "",
        ])
    return out


def _csv_rows_wg(rows) -> list[list]:
    out = []
    for r in rows:
        limit_gb = ""
        if r["transfer_limit_bytes"]:
            limit_gb = f"{r['transfer_limit_bytes'] // (1024**3):.2f}"
        used_mb = f"{(r['quota_bytes_used'] or 0) // (1024**2)}"
        out.append([
            r["id"],
            r["display_name"] or "",
            r["peer_address"] or "",
            r["status"],
            r["interface_name"] or "",
            r["speed_limit_mbps"] or "",
            limit_gb,
            used_mb,
            r["quota_period"] or "",
            r["last_handshake_at"] or "",
            r["created_at"] or "",
        ])
    return out


def _csv_rows_audit(rows) -> list[list]:
    return [[
        r["id"],
        r["entity_type"] or "",
        r["entity_id"] or "",
        r["action"] or "",
        r["actor"] or "",
        r["description"] or "",
        r["created_at"] or "",
    ] for r in rows]


# ─── Views ────────────────────────────────────────────────────────

def service_reports():
    tid = _tid()

    # فلاتر اختيارية
    status_filter = (request.args.get("status") or "").strip()
    allocation_filter = request.args.get("allocation_id", type=int)
    audit_type_filter = (request.args.get("entity_type") or "").strip()
    limit_str = request.args.get("limit", "200")
    try:
        limit = min(int(limit_str), 1000)
    except ValueError:
        limit = 200

    vpn_q = db().execute(
        """SELECT va.id, va.username, va.status,
                  va.allocation_mirror_id, sam.service_type, sam.expires_at,
                  va.created_at, va.updated_at
           FROM vpn_account va
           LEFT JOIN service_allocation_mirror sam ON sam.id = va.allocation_mirror_id
           WHERE va.tenant_id = ?
             AND (? = '' OR va.status = ?)
             AND (? = 0 OR va.allocation_mirror_id = ?)
           ORDER BY va.id DESC LIMIT ?""",
        (tid,
         status_filter, status_filter,
         1 if allocation_filter else 0, allocation_filter or 0,
         limit),
    ).fetchall()

    wg_q = db().execute(
        """SELECT p.id, p.display_name, p.peer_address, p.status,
                  p.speed_limit_mbps, p.transfer_limit_bytes, p.quota_bytes_used,
                  p.quota_period, p.last_handshake_at, p.created_at,
                  s.interface_name
           FROM wireguard_peer p
           JOIN wireguard_data_service s ON s.id = p.service_id
           WHERE p.tenant_id = ?
             AND (? = '' OR p.status = ?)
           ORDER BY p.id DESC LIMIT ?""",
        (tid,
         status_filter, status_filter,
         limit),
    ).fetchall()

    audit_q = db().execute(
        """SELECT id, entity_type, entity_id, action, actor, description, created_at
           FROM service_audit_log
           WHERE tenant_id = ?
             AND (? = '' OR entity_type = ?)
           ORDER BY id DESC LIMIT ?""",
        (tid,
         audit_type_filter, audit_type_filter,
         min(limit, 500)),
    ).fetchall()

    allocs = db().execute(
        "SELECT id, service_type, status FROM service_allocation_mirror WHERE tenant_id=?",
        (tid,),
    ).fetchall()

    wg_svc = _wg_service(tid)

    # إحصاءات سريعة
    vpn_totals = {
        "total": len(vpn_q),
        "active": sum(1 for r in vpn_q if r["status"] == "active"),
        "expired": sum(1 for r in vpn_q if r["status"] == "expired"),
    }
    wg_totals = {
        "total": len(wg_q),
        "active": sum(1 for r in wg_q if r["status"] == "active"),
        "quota_exceeded": sum(1 for r in wg_q if r["status"] == "quota_exceeded"),
    }

    return render_template(
        "radius/vpn_reports.html",
        vpn_accounts=vpn_q,
        wg_peers=wg_q,
        audit_log=audit_q,
        allocations=allocs,
        wg_svc=wg_svc,
        vpn_totals=vpn_totals,
        wg_totals=wg_totals,
        status_filter=status_filter,
        allocation_filter=allocation_filter,
        audit_type_filter=audit_type_filter,
        limit=limit,
        now=_now_utc(),
    )


def export_vpn_accounts_csv():
    tid = _tid()
    rows = _vpn_accounts(tid, limit=5000)
    data_rows = _csv_rows_vpn(rows)
    headers = [
        "id", "username", "status", "service_type",
        "expires_at", "created_at", "updated_at",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(data_rows)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=vpn-accounts-{ts}.csv",
            "Cache-Control": "no-store",
        },
    )


def export_wg_peers_csv():
    tid = _tid()
    rows = _wg_peers(tid, limit=5000)
    data_rows = _csv_rows_wg(rows)
    headers = [
        "id", "display_name", "peer_address", "status", "interface",
        "speed_limit_mbps", "transfer_limit_gb", "quota_used_mb",
        "quota_period", "last_handshake_at", "created_at",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(data_rows)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=wg-peers-{ts}.csv",
            "Cache-Control": "no-store",
        },
    )


def export_audit_log_csv():
    tid = _tid()
    rows = _audit_log(tid, limit=5000)
    data_rows = _csv_rows_audit(rows)
    headers = [
        "id", "entity_type", "entity_id", "action", "actor",
        "description", "created_at",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(data_rows)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=audit-log-{ts}.csv",
            "Cache-Control": "no-store",
        },
    )


# ─── Registration ─────────────────────────────────────────────────

def register_vpn_reports_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/service-reports",
        "vpn_service_reports",
        service_reports,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/service-reports/export/vpn-accounts.csv",
        "vpn_reports_export_accounts",
        export_vpn_accounts_csv,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/service-reports/export/wg-peers.csv",
        "vpn_reports_export_wg_peers",
        export_wg_peers_csv,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/service-reports/export/audit-log.csv",
        "vpn_reports_export_audit",
        export_audit_log_csv,
        methods=["GET"],
    )


__all__ = ["register_vpn_reports_routes"]
