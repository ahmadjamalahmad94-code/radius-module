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


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_status_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/_status", "system_status", system_status, methods=["GET"])
    bp.add_url_rule("/sync", "sync_list", sync_list, methods=["GET"])
    bp.add_url_rule("/sync/<int:job_id>/retry", "sync_retry", sync_retry, methods=["POST"])
    bp.add_url_rule("/sync/<int:job_id>/cancel", "sync_cancel", sync_cancel, methods=["POST"])
    bp.add_url_rule("/audit", "audit_list", audit_list, methods=["GET"])


# ─────────────── status ───────────────

def _gather_status(tenant_id: int) -> dict:
    from app.workers.heartbeat import snapshot
    workers = snapshot()
    sync_stats = sync_queue_repo.stats(tenant_id)
    mt_configs = mikrotik_repo.list_configs(tenant_id)
    enabled_mt = [c for c in mt_configs if c["enabled"]]

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
