"""
routes للحالة التشغيلية: /admin/radius/_status + sync queue inspector.

/admin/radius/_status   ← HTML + JSON (Accept: application/json) — يعرض workers, queues, MT health
/admin/radius/sync      ← قائمة sync_queue + إعادة محاولة / إلغاء
/admin/radius/audit     ← آخر 200 audit action
"""
from __future__ import annotations

import time

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import mikrotik_repo, sync_queue_repo, webhooks_repo
from ..services import system_probe


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_status_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/_status", "system_status", system_status, methods=["GET"])
    bp.add_url_rule("/sync", "sync_list", sync_list, methods=["GET"])
    bp.add_url_rule("/sync/<int:job_id>/retry", "sync_retry", sync_retry, methods=["POST"])
    bp.add_url_rule("/sync/<int:job_id>/cancel", "sync_cancel", sync_cancel, methods=["POST"])
    bp.add_url_rule("/audit", "audit_list", audit_list, methods=["GET"])
    bp.add_url_rule("/_reconcile_now", "reconcile_now",
                    reconcile_now, methods=["POST", "GET"])
    bp.add_url_rule("/diagnostics", "diagnostics",
                    diagnostics, methods=["GET"])
    bp.add_url_rule("/mt-push-setup", "mt_push_setup",
                    mt_push_setup, methods=["GET"])


def mt_push_setup():
    """Generates a MikroTik scheduler script the operator pastes into
    Winbox/terminal so the router pushes DHCP leases to us via HTTPS.

    Used when the standard PULL mode (VPS → MT API on 8728) is blocked
    by firewall/NAT — outbound HTTPS from the router almost always
    works, so this path is the universal fallback.
    """
    from ..db.repos import api_tokens_repo

    # Best-effort: pick the first non-revoked API token for this tenant
    # to suggest. Operator can override on the page if they have many.
    tid = _tid()
    tokens = [t for t in api_tokens_repo.list_tokens(tid) if not t.get("revoked")]
    suggested_token_name = tokens[0]["name"] if tokens else ""

    # The VPS public URL — best-effort detection from request headers.
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    forwarded_host  = request.headers.get("X-Forwarded-Host", "")
    proto = forwarded_proto or ("https" if request.is_secure else "http")
    host  = forwarded_host  or request.host
    base_url = f"{proto}://{host}"

    return render_template(
        "radius/mt_push_setup.html",
        base_url=base_url,
        tokens=tokens,
        suggested_token_name=suggested_token_name,
    )


def diagnostics():
    """Per-router health-check page. Runs TCP probe + API login test
    against every configured router and renders verdicts + fix hints +
    copyable MT commands.

    O5 — the repair-script block in the template branches on each
    router's connection_mode: 'vpn' rows get a WG-subnet rule
    (so locking the API to the public VPS IP doesn't break the
    tunnel-side reach), 'direct' rows get the public-IP rule.
    Both subnet + public IP are passed to the template so it can
    pick the right one without re-reading env.
    """
    import os
    from ..services import mt_diagnostics
    report = mt_diagnostics.diagnose_tenant(_tid())
    vps_ip = request.headers.get("X-Real-IP") or \
             request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or \
             request.remote_addr or "YOUR_VPS_IP"
    # WG subnet for the VPN-mode repair branch. Falls back to the
    # documented default if the env var isn't set yet.
    wg_subnet = (
        os.environ.get("HOBERADIUS_WG_SUBNET") or "10.10.0.0/24"
    ).strip()
    return render_template(
        "radius/mt_diagnostics.html",
        report=report,
        vps_ip=vps_ip,
        wg_subnet=wg_subnet,
    )


def reconcile_now():
    """On-demand: run the MT-reconciler once across this tenant.

    Useful after a router reboot when the operator wants to flush
    ghost sessions immediately instead of waiting for the next 30s
    background tick. Returns JSON so it can be hit from the UI or
    curl/cron. Safe to spam — the reconciler is idempotent.
    """
    from app.workers import mt_reconciler
    try:
        stats = mt_reconciler.reconcile_once()
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "stats": stats})


# ─────────────── status ───────────────

def _gather_status(tenant_id: int) -> dict:
    from app.workers.heartbeat import snapshot
    workers = snapshot()
    sync_stats = sync_queue_repo.stats(tenant_id)
    mt_configs = mikrotik_repo.list_configs(tenant_id)
    enabled_mt = [c for c in mt_configs if c["enabled"]]
    vps = system_probe.get_vps_status()

    # webhook deliveries summary
    cur = db().execute("""
        SELECT status, COUNT(*) AS c FROM webhook_deliveries WHERE tenant_id = ? GROUP BY status
    """, (tenant_id,))
    wh = {"queued": 0, "retrying": 0, "delivered": 0, "failed": 0}
    for r in cur.fetchall():
        wh[r["status"]] = r["c"]

    # rowcounts (سريعة)
    counts = {}
    for t in ("subscribers", "access_plans", "cards", "card_batches",
              "vouchers", "invoices", "tickets", "radacct"):
        try:
            counts[t] = db().execute(f"SELECT COUNT(*) AS c FROM {t} WHERE tenant_id = ?",
                                       (tenant_id,)).fetchone()["c"]
        except Exception:
            counts[t] = -1

    return {
        "tenant_id": tenant_id,
        "workers": workers,
        "sync_queue": sync_stats,
        "webhook_deliveries": wh,
        "mt_routers": {
            "total": len(mt_configs),
            "enabled": len(enabled_mt),
            "items": [{"id": c["id"], "name": c["name"], "host": c["host"],
                       "last_status": (c["last_status"] or "")[:80],
                       "last_seen_at": c["last_seen_at"], "enabled": bool(c["enabled"])}
                      for c in mt_configs],
        },
        "counts": counts,
        "vps": vps,
        "system": {
            "hostname": vps.get("hostname"),
            "platform": vps.get("platform"),
            "process_uptime": vps.get("process_uptime"),
            "system_uptime": vps.get("system_uptime"),
            "cpu_pct": vps.get("cpu_pct"),
            "ram_pct": (vps.get("memory") or {}).get("percent"),
            "disk_pct": (vps.get("disk") or {}).get("percent"),
            "load": vps.get("load"),
            "network": vps.get("network"),
        },
        "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def system_status():
    data = _gather_status(_tid())
    # JSON إذا طُلب
    if "application/json" in (request.headers.get("Accept") or "") \
       or request.args.get("format") == "json":
        return jsonify(data)
    return render_template("radius/_status.html", s=data)


# ─────────────── sync queue ───────────────

def sync_list():
    status = request.args.get("status") or None
    jobs = sync_queue_repo.list_jobs(_tid(), status=status, limit=300)
    return render_template("radius/sync_list.html", jobs=jobs, status=status,
                            stats=sync_queue_repo.stats(_tid()))


def sync_retry(job_id: int):
    """يعيد job إلى queued + يصفّر next_attempt_at."""
    from ..db.connection import transaction
    from ..db.helpers import now_iso
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE sync_queue SET status='queued', next_attempt_at=?, last_error='' "
            "WHERE tenant_id = ? AND id = ?",
            (now_iso(), _tid(), job_id)
        )
        if cur.rowcount == 0:
            abort(404)
    flash("أُعيدت المحاولة فورًا.", "success")
    return redirect(request.referrer or url_for("radius.sync_list"))


def sync_cancel(job_id: int):
    from ..db.connection import transaction
    from ..db.helpers import now_iso
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE sync_queue SET status='failed', last_error='canceled by admin' "
            "WHERE tenant_id = ? AND id = ? AND status IN ('queued','retrying')",
            (_tid(), job_id)
        )
        if cur.rowcount == 0:
            abort(404)
    flash("تم إلغاء المهمة.", "warning")
    return redirect(request.referrer or url_for("radius.sync_list"))


# ─────────────── audit ───────────────

def audit_list():
    cur = db().execute("""
        SELECT * FROM audit_log
        WHERE tenant_id = ?
        ORDER BY id DESC LIMIT 300
    """, (_tid(),))
    items = [dict(r) for r in cur.fetchall()]
    return render_template("radius/audit_list.html", items=items)
